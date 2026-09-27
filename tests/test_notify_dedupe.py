"""
Testes para dedupe de notificações Telegram (notified.json) — item 3 do backlog.

Cobre:
- Estado: load/save/remove/clear notified.json + lock fcntl + prune 90 dias
- Migração lazy: deals.json pré-existente sem notified.json -> nada enviado na 1ª execução
- notify(): classificação (novo / pulado / re-notificado por preço), split de mensagens,
  persistência só após todas as mensagens OK, contadores retornados
- Config: notify_price_change_pct (default 5%, custom, inválido)
- API: /api/deals campo derivado notified + filtro ?notified=0|1,
  POST /api/deals/renotify, GET/DELETE /api/notified

Isolado via tmpdir monkeypatch de olx_scraper.STATE_DIR/NOTIFIED_FILE/etc (padrão de
test_schedule.py). Telegram simulado via monkeypatch de olx_scraper._telegram_send.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture()
def isolated_olx(tmp_path, monkeypatch):
    """Isola ~/.olx em tmp_path."""
    import olx_scraper
    import app as app_module

    state = tmp_path / ".olx"
    state.mkdir()
    monkeypatch.setattr(olx_scraper, "STATE_DIR", str(state))
    monkeypatch.setattr(olx_scraper, "CONFIG_FILE", str(state / "config.json"))
    monkeypatch.setattr(olx_scraper, "SCHEDULES_FILE", str(state / "schedules.json"))
    monkeypatch.setattr(olx_scraper, "COOKIE_JAR", str(state / "cookies.json"))
    monkeypatch.setattr(olx_scraper, "HISTORY_FILE", str(state / "deals.json"))
    monkeypatch.setattr(olx_scraper, "NOTIFIED_FILE", str(state / "notified.json"))
    monkeypatch.setattr(olx_scraper, "NOTIFIED_LOCK_FILE", str(state / ".notified.lock"))

    yield {
        "state": state,
        "hist_file": state / "deals.json",
        "notified_file": state / "notified.json",
        "lock_file": state / ".notified.lock",
        "app": app_module,
        "olx_scraper": olx_scraper,
    }


@pytest.fixture()
def client(isolated_olx):
    app = isolated_olx["app"].app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class FakeTg:
    """Substituto de _telegram_send: registra textos; fail simula erro HTTP."""

    def __init__(self):
        self.calls = []
        self.fail = False

    def __call__(self, token, chat_id, text, timeout=30):
        self.calls.append(text)
        return not self.fail


@pytest.fixture()
def fake_tg(monkeypatch):
    ft = FakeTg()
    scraper = __import__("olx_scraper")
    monkeypatch.setattr(scraper, "_telegram_send", ft)
    # telegram configurado
    monkeypatch.setattr(scraper, "load_telegram_config", lambda: ("tok-teste", "chat-teste"))
    return ft


def _deal(pid="hd-6-12tb", ad_id="111", title="HD 8TB WD", price=800.0):
    return {
        "id": ad_id,
        "title": title,
        "price": price,
        "url": f"https://www.olx.com.br/item/{ad_id}",
        "query": "hd 8tb",
        "page": 1,
        "product_id": pid,
        "product_name": "HD 6-12TB",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "matched": True,
    }


# ---------------------------------------------------------------------------
# Caso 1: 3 scrapes seguidos com mesmo resultado -> apenas 1 notificação
# ---------------------------------------------------------------------------

class TestDedupeBasic:
    def test_three_notifies_same_deals_only_first_sends(self, isolated_olx, fake_tg):
        s = isolated_olx["olx_scraper"]
        deals = [_deal(ad_id="1"), _deal(ad_id="2")]

        c1 = s.notify(deals)
        assert c1 == {"enviados": 2, "pulados": 0, "re_notificados": 0}
        assert len(fake_tg.calls) == 1

        c2 = s.notify(deals)
        assert c2["enviados"] == 0
        assert c2["pulados"] == 2
        assert len(fake_tg.calls) == 1  # nenhuma mensagem nova

        c3 = s.notify(deals)
        assert c3["enviados"] == 0 and c3["pulados"] == 2
        assert len(fake_tg.calls) == 1

    def test_full_flow_save_history_plus_notify(self, isolated_olx, fake_tg):
        """Fluxo real pós-migração: scrape->save_history->notify com mesmos resultados 3x."""
        s = isolated_olx["olx_scraper"]
        # estado pré-existente (upgrade): histórico antigo já migrado
        s.save_history([_deal(ad_id="old")])
        s.load_notified()
        results = [_deal(ad_id="10"), _deal(ad_id="11")]
        counts = []
        for _ in range(3):
            new = s.save_history(results)
            counts.append(s.notify(new))
        assert len(fake_tg.calls) == 1          # só o 1º run envia
        assert counts[0]["enviados"] == 2
        assert counts[1]["enviados"] == 0       # runs seguintes não geram 'new'
        assert counts[2]["enviados"] == 0

    def test_empty_deals_returns_counters_without_send(self, isolated_olx, fake_tg):
        s = isolated_olx["olx_scraper"]
        cnt = s.notify([])
        assert cnt == {"enviados": 0, "pulados": 0, "re_notificados": 0}
        assert fake_tg.calls == []


# ---------------------------------------------------------------------------
# Caso 2: deals.json apagado -> registro em notified.json ainda evita reenvio
# ---------------------------------------------------------------------------

class TestHistoryWiped:
    def test_history_deleted_registry_prevents_resend(self, isolated_olx, fake_tg):
        s = isolated_olx["olx_scraper"]
        deals = [_deal(ad_id="20"), _deal(ad_id="21")]
        s.save_history(deals)
        s.load_notified()  # migração marca ambos
        s.remove_notified(["hd-6-12tb:20", "hd-6-12tb:21"])
        assert s.notify(deals)["enviados"] == 2

        # apaga deals.json (debug/reset) — registro deve segurar o reenvio
        isolated_olx["hist_file"].unlink()
        c2 = s.notify(deals)
        assert c2["enviados"] == 0
        assert c2["pulados"] == 2
        assert len(fake_tg.calls) == 1


# ---------------------------------------------------------------------------
# Caso 3: mudança de preço >5% -> re-notifica com "Preço atualizado"
# ---------------------------------------------------------------------------

class TestPriceChange:
    def test_price_drop_over_threshold_renotifies(self, isolated_olx, fake_tg):
        s = isolated_olx["olx_scraper"]
        d = _deal(ad_id="30", price=1000.0)
        assert s.notify([d])["enviados"] == 1

        d2 = _deal(ad_id="30", price=890.0)  # -11%
        c = s.notify([d2])
        assert c["re_notificados"] == 1
        assert c["pulados"] == 0
        assert len(fake_tg.calls) == 2
        assert "Preço atualizado" in fake_tg.calls[1]

    def test_small_change_within_threshold_skipped(self, isolated_olx, fake_tg):
        s = isolated_olx["olx_scraper"]
        d = _deal(ad_id="31", price=1000.0)
        s.notify([d])
        # exatamente 5% (limite inclusivo) -> pula
        c = s.notify([_deal(ad_id="31", price=1050.0)])
        assert c["pulados"] == 1 and c["re_notificados"] == 0
        # 4% -> pula
        c = s.notify([_deal(ad_id="31", price=1040.0)])
        assert c["pulados"] == 1
        assert len(fake_tg.calls) == 1

    def test_price_rise_over_threshold_also_renotifies(self, isolated_olx, fake_tg):
        s = isolated_olx["olx_scraper"]
        s.notify([_deal(ad_id="32", price=100.0)])
        c = s.notify([_deal(ad_id="32", price=150.0)])  # +50%
        assert c["re_notificados"] == 1
        assert "Preço atualizado" in fake_tg.calls[1]

    def test_custom_threshold_from_config(self, isolated_olx, fake_tg):
        s = isolated_olx["olx_scraper"]
        s.save_config({"notify_price_change_pct": 20})
        assert s.get_notify_price_change_pct() == 20.0
        s.notify([_deal(ad_id="33", price=100.0)])
        # +10% estaria acima do default 5%, mas abaixo do threshold 20% -> pula
        c = s.notify([_deal(ad_id="33", price=110.0)])
        assert c["pulados"] == 1 and c["re_notificados"] == 0

    def test_invalid_pct_falls_back_to_default(self, isolated_olx):
        s = isolated_olx["olx_scraper"]
        s.save_config({"notify_price_change_pct": "abc"})
        assert s.get_notify_price_change_pct() == s.DEFAULT_NOTIFY_PRICE_CHANGE_PCT
        s.save_config({"notify_price_change_pct": 500})
        assert s.get_notify_price_change_pct() == s.DEFAULT_NOTIFY_PRICE_CHANGE_PCT

    def test_price_delta_pct_edges(self, isolated_olx):
        s = isolated_olx["olx_scraper"]
        assert s._price_delta_pct(100, 110) == pytest.approx(10.0)
        assert s._price_delta_pct(None, 100) is None
        assert s._price_delta_pct("x", 100) is None
        assert s._price_delta_pct(0, 0) == 0.0
        assert s._price_delta_pct(0, 50) == float("inf")


# ---------------------------------------------------------------------------
# Caso 4: Telegram retorna erro -> não persiste; próximo run re-tenta
# ---------------------------------------------------------------------------

class TestTelegramFailure:
    def test_failure_does_not_persist_and_retries(self, isolated_olx, fake_tg):
        s = isolated_olx["olx_scraper"]
        deals = [_deal(ad_id="40")]
        fake_tg.fail = True
        c = s.notify(deals)
        assert c["enviados"] == 0
        assert c.get("erro")
        # nada persistido
        assert s.load_notified() == {}

        fake_tg.fail = False
        c2 = s.notify(deals)
        assert c2["enviados"] == 1
        assert not c2.get("erro")
        reg = s.load_notified()
        assert "hd-6-12tb:40" in reg

    def test_partial_split_failure_persists_nothing(self, isolated_olx, fake_tg):
        """Falha na 2ª mensagem -> nenhum registro gravado (all-or-nothing)."""
        s = isolated_olx["olx_scraper"]
        calls = {"n": 0}

        def flaky(token, chat_id, text, timeout=30):
            calls["n"] += 1
            return calls["n"] < 2  # 1ª ok, 2ª falha

        orig = s._telegram_send
        s._telegram_send = flaky
        try:
            big = [_deal(ad_id=f"4{i}", title="X" * 900) for i in range(10)]  # força split
            c = s.notify(big)
            assert calls["n"] >= 2
            assert c.get("erro")
            assert s.load_notified() == {}
        finally:
            s._telegram_send = orig

    def test_telegram_unconfigured_returns_error_no_persist(self, isolated_olx, monkeypatch):
        s = isolated_olx["olx_scraper"]
        monkeypatch.setattr(s, "load_telegram_config", lambda: (None, None))
        c = s.notify([_deal(ad_id="41")])
        assert c.get("erro")
        assert c["enviados"] == 0
        assert s.load_notified() == {}


# ---------------------------------------------------------------------------
# Caso 5: migração — deals.json pré-existente, notified.json ausente
# ---------------------------------------------------------------------------

class TestMigration:
    def test_existing_deals_not_sent_on_first_run(self, isolated_olx, fake_tg):
        s = isolated_olx["olx_scraper"]
        deals = [_deal(ad_id="50"), _deal(ad_id="51")]
        with open(isolated_olx["hist_file"], "w") as f:
            json.dump(deals, f)
        assert not isolated_olx["notified_file"].exists()

        # 1ª execução: migração lazy marca tudo como já notificado
        c = s.notify(deals)
        assert c["enviados"] == 0
        assert c["pulados"] == 2
        assert fake_tg.calls == []
        # registro persistido
        reg = s.load_notified()
        assert set(reg) == {"hd-6-12tb:50", "hd-6-12tb:51"}
        assert isolated_olx["notified_file"].exists()

    def test_migration_inherits_zero_age(self, isolated_olx):
        s = isolated_olx["olx_scraper"]
        with open(isolated_olx["hist_file"], "w") as f:
            json.dump([_deal(ad_id="52")], f)
        reg = s.load_notified()
        at = datetime.fromisoformat(reg["hd-6-12tb:52"]["at"])
        age = datetime.now(timezone.utc) - at.replace(tzinfo=timezone.utc)
        assert abs(age.total_seconds()) < 60  # idade artificial ~zero

    def test_migration_survives_history_wipe(self, isolated_olx, fake_tg):
        s = isolated_olx["olx_scraper"]
        deals = [_deal(ad_id="53")]
        with open(isolated_olx["hist_file"], "w") as f:
            json.dump(deals, f)
        s.load_notified()  # dispara migração
        isolated_olx["hist_file"].unlink()
        c = s.notify(deals)
        assert c["pulados"] == 1 and c["enviados"] == 0


# ---------------------------------------------------------------------------
# Estado: prune, lock, remove/clear
# ---------------------------------------------------------------------------

class TestState:
    def test_prune_entries_older_than_90_days(self, isolated_olx):
        s = isolated_olx["olx_scraper"]
        old_iso = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
        fresh_iso = datetime.now(timezone.utc).isoformat()
        with open(isolated_olx["notified_file"], "w") as f:
            json.dump({"notified": {
                "p:old": {"at": old_iso, "price": 1.0},
                "p:fresh": {"at": fresh_iso, "price": 2.0},
                "p:broke": {"no_at": True},
            }}, f)
        out = s.save_notified({})
        assert set(out) == {"p:fresh"}

    def test_old_entry_pruned_allows_renotification(self, isolated_olx, fake_tg):
        s = isolated_olx["olx_scraper"]
        old_iso = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        with open(isolated_olx["notified_file"], "w") as f:
            json.dump({"notified": {"p:60": {"at": old_iso, "price": 100.0}}}, f)
        s.save_notified({})  # prune acontece na escrita (spec)
        c = s.notify([_deal(pid="p", ad_id="60")])
        assert c["enviados"] == 1  # expirou -> notifica de novo

    def test_remove_and_clear(self, isolated_olx):
        s = isolated_olx["olx_scraper"]
        s.save_notified({"a:1": {"at": datetime.now(timezone.utc).isoformat()},
                         "a:2": {"at": datetime.now(timezone.utc).isoformat()}})
        assert s.remove_notified(["a:1", "a:x"]) == 1
        assert set(s.load_notified()) == {"a:2"}
        assert s.clear_notified() == 1
        assert s.load_notified() == {}
        assert isolated_olx["lock_file"].exists()

    def test_atomic_write_leaves_no_tmp(self, isolated_olx):
        s = isolated_olx["olx_scraper"]
        s.save_notified({"a:1": {"at": datetime.now(timezone.utc).isoformat()}})
        assert not isolated_olx["state"].joinpath("notified.json.tmp").exists()

    def test_concurrent_saves_merge(self, isolated_olx):
        """Merge-on-write: segunda escrita não perde a primeira (mesmo processo)."""
        s = isolated_olx["olx_scraper"]
        now = datetime.now(timezone.utc).isoformat()
        s.save_notified({"a:1": {"at": now}})
        s.save_notified({"a:2": {"at": now}})
        assert set(s.load_notified()) == {"a:1", "a:2"}


# ---------------------------------------------------------------------------
# Split de mensagens longas
# ---------------------------------------------------------------------------

class TestSplitMessages:
    def test_long_batch_splits_into_multiple_messages(self, isolated_olx, fake_tg):
        s = isolated_olx["olx_scraper"]
        deals = [_deal(ad_id=str(i), title="T" * 400) for i in range(30)]  # ~14k chars
        c = s.notify(deals)
        assert c["enviados"] == 30
        assert len(fake_tg.calls) >= 3
        for msg in fake_tg.calls:
            assert len(msg) <= s.TELEGRAM_MSG_LIMIT
        joined = "\n\n".join(fake_tg.calls)
        for i in range(30):
            assert f"/{i}" in joined  # todos os deals presentes (sem truncamento)

    def test_single_huge_block_hard_splits(self, isolated_olx):
        s = isolated_olx["olx_scraper"]
        msgs = s._split_blocks_into_messages(["A" * 8000], limit=3500)
        assert [len(m) for m in msgs] == [3500, 3500, 1000]

    def test_blocks_kept_whole_when_possible(self, isolated_olx):
        s = isolated_olx["olx_scraper"]
        msgs = s._split_blocks_into_messages(["abc", "def"], limit=3500)
        assert msgs == ["abc\n\ndef"]


# ---------------------------------------------------------------------------
# API: /api/deals notified, renotify, /api/notified
# ---------------------------------------------------------------------------

class TestApi:
    @pytest.fixture()
    def seeded(self, isolated_olx, fake_tg):
        s = isolated_olx["olx_scraper"]
        deals = [_deal(ad_id="70"), _deal(ad_id="71")]
        s.save_history(deals)
        s.load_notified()  # migração marca ambos
        s.remove_notified(["hd-6-12tb:70", "hd-6-12tb:71"])
        s.notify([deals[0]])  # envia só 70 -> estado misto
        return s

    def test_deals_have_derived_notified_field(self, isolated_olx, client, seeded):
        r = client.get("/api/deals")
        j = r.get_json()
        by_id = {d["id"]: d for d in j["deals"]}
        assert by_id["70"]["notified"] is True
        assert by_id["71"]["notified"] is False

    def test_deals_filter_notified_zero(self, isolated_olx, client, seeded):
        r = client.get("/api/deals?notified=0")
        ids = {d["id"] for d in r.get_json()["deals"]}
        assert ids == {"71"}
        r = client.get("/api/deals?notified=1")
        ids = {d["id"] for d in r.get_json()["deals"]}
        assert ids == {"70"}

    def test_renotify_removes_key_and_sends(self, isolated_olx, client, seeded):
        s = isolated_olx["olx_scraper"]
        r = client.post("/api/deals/renotify", json={"product_id": "hd-6-12tb", "ad_id": "70"})
        assert r.status_code == 200
        j = r.get_json()
        assert j["ok"] is True and j["removed"] == 1
        assert j["counters"]["enviados"] == 1
        # chave volta ao registro após sucesso
        assert "hd-6-12tb:70" in s.load_notified()

    def test_renotify_missing_deal_404(self, isolated_olx, client, seeded):
        r = client.post("/api/deals/renotify", json={"product_id": "hd-6-12tb", "ad_id": "999"})
        assert r.status_code == 404

    def test_renotify_bad_body_400(self, isolated_olx, client, seeded):
        r = client.post("/api/deals/renotify", json={})
        assert r.status_code == 400

    def test_notified_get_and_delete(self, isolated_olx, client, seeded):
        r = client.get("/api/notified")
        assert r.get_json()["count"] == 1
        r = client.delete("/api/notified")
        assert r.get_json()["cleared"] == 1
        assert client.get("/api/notified").get_json()["count"] == 0

    def test_config_pct_validation(self, isolated_olx, client, seeded):
        r = client.put("/api/config", json={"notify_price_change_pct": 250})
        assert r.status_code == 400
        r = client.put("/api/config", json={"notify_price_change_pct": 10})
        assert r.status_code == 200
        assert r.get_json()["config"]["notify_price_change_pct"] == 10.0
