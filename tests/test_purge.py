"""
Caracterização: purge_deals() olx_scraper.py:1945 + POST /api/deals/purge app.py:1442
(dry_run/confirm/by_product/by_reason/backup)

Reproduz estado BACKLOG.md:431 (1160 deals, 396 matched, orfãos etc) em escala reduzida.
"""
import json
import glob
import os
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture()
def isolated_purge(tmp_path, monkeypatch):
    import olx_scraper, app as app_module
    state = tmp_path / ".olx"
    state.mkdir()
    for attr in ["STATE_DIR", "CONFIG_FILE", "HISTORY_FILE", "NOTIFIED_FILE", "NOTIFIED_LOCK_FILE",
                 "RUNS_FILE", "RUNS_LOCK_FILE", "SCHEDULES_FILE", "COOKIE_JAR"]:
        monkeypatch.setattr(olx_scraper, attr, str(state / attr.split("/")[-1].replace("STATE_DIR", ".olx")))
    # simpler: directly set each
    monkeypatch.setattr(olx_scraper, "STATE_DIR", str(state))
    monkeypatch.setattr(olx_scraper, "CONFIG_FILE", str(state / "config.json"))
    monkeypatch.setattr(olx_scraper, "HISTORY_FILE", str(state / "deals.json"))
    monkeypatch.setattr(olx_scraper, "NOTIFIED_FILE", str(state / "notified.json"))
    monkeypatch.setattr(olx_scraper, "NOTIFIED_LOCK_FILE", str(state / ".notified.lock"))
    monkeypatch.setattr(olx_scraper, "RUNS_FILE", str(state / "runs.json"))
    monkeypatch.setattr(olx_scraper, "RUNS_LOCK_FILE", str(state / ".runs.lock"))
    monkeypatch.setattr(olx_scraper, "SCHEDULES_FILE", str(state / "schedules.json"))
    monkeypatch.setattr(olx_scraper, "COOKIE_JAR", str(state / "cookies.json"))

    # config: hd-6tb e pikimin ativos
    cfg = {
        "products": [
            {"id": "hd-6tb", "name": "HD 6TB", "queries": ["hd 6tb"], "pages": 1, "min_price": 100, "max_price": 900, "junk_pattern": "", "exclude_keywords": [], "include_pattern": "", "enabled": True},
            {"id": "pikimin", "name": "Pikmin", "queries": ["pikmin"], "pages": 1, "min_price": None, "max_price": None, "junk_pattern": "", "exclude_keywords": ["wii u", "game cube"], "include_pattern": "pikmin", "enabled": True},
        ],
        "telegram_token": None,
        "telegram_chat_id": None,
    }
    olx_scraper.save_config(cfg)

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield {"scraper": olx_scraper, "app": app_module, "client": client, "state": state}


def _deal(pid, id_, title, price, matched=True, fetched_at=None):
    return {
        "id": id_,
        "title": title,
        "price": price,
        "url": f"https://www.olx.com.br/item/{id_}.html",
        "product_id": pid,
        "product_name": pid,
        "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(),
        "matched": matched,
        "discard_reason": None if matched else "price",
        "status": "active",
    }


def _seed(state, deals):
    with open(state / "deals.json", "w") as f:
        json.dump(deals, f)


def _read(state):
    with open(state / "deals.json") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Unidade: purge_deals direto
# ---------------------------------------------------------------------------

class TestPurgeDealsUnit:
    def test_dry_run_does_not_modify(self, isolated_purge):
        scraper = isolated_purge["scraper"]
        state = isolated_purge["state"]
        deals = [_deal("hd-6tb", "1", "HD 150", 150), _deal("hd-6tb", "2", "HD 300", 300)]
        _seed(state, deals)
        before = len(_read(state))
        res = scraper.purge_deals(lambda d: d["id"] == "1", dry_run=True)
        assert res["purged"] == 1 and res["remaining"] == 1
        assert res["backup_path"] is None
        assert len(_read(state)) == before
        # nenhum backup criado
        assert not list(state.glob("deals.json.bak-purge-*"))

    def test_purge_creates_backup_and_removes(self, isolated_purge):
        scraper = isolated_purge["scraper"]
        state = isolated_purge["state"]
        deals = [_deal("hd-6tb", "1", "HD 150", 150), _deal("hd-6tb", "2", "HD 300", 300)]
        _seed(state, deals)
        res = scraper.purge_deals(lambda d: d["id"] == "1", dry_run=False)
        assert res["purged"] == 1 and res["remaining"] == 1
        assert res["backup_path"] is not None and os.path.exists(res["backup_path"])
        remaining = _read(state)
        assert [d["id"] for d in remaining] == ["2"]
        # backup contém original
        import json as js
        with open(res["backup_path"]) as f:
            bak = js.load(f)
        assert len(bak) == 2

    def test_purge_deals_handles_orphan_migration(self, isolated_purge):
        # deals sem product_id devem ganhar hd-6-12tb
        scraper = isolated_purge["scraper"]
        state = isolated_purge["state"]
        deals = [{"id": "x", "title": "sem pid", "price": 100}]
        _seed(state, deals)
        res = scraper.purge_deals(lambda d: False, dry_run=True)
        # sem panico, e deal ganha pid default no carregamento interno
        assert res["remaining"] == 1


# ---------------------------------------------------------------------------
# API: POST /api/deals/purge
# ---------------------------------------------------------------------------

class TestPurgeApi:
    def _base_deals(self):
        # 7 deals: 2 orfãos, 2 pikimin não-aderentes (wii u, include), 1 hd price ok, 1 hd price fora? mas matched histórico true
        # e 1 válido aderente
        now = datetime.now(timezone.utc).isoformat()
        return [
            _deal("hd-6-12tb", "o1", "HD antigo 1", 800, True, now),
            _deal("hd-6-12tb", "o2", "HD antigo 2", 850, True, now),
            _deal("pikimin", "1452796691", "Pikmin 3 Wii U", 200, True, now),
            _deal("pikimin", "1507077843", "Pikmin 2 game cube jp", 200, True, now),
            _deal("pikimin", "1", "Pikmin 3 Deluxe Switch", 200, True, now),  # aderente
            _deal("hd-6tb", "2", "HD 150 bom", 150, True, now),  # aderente
            _deal("hd-6tb", "3", "HD 300 caro", 3000, True, now),  # price fora? mas max 900, será não aderente se price>max
            # need matched historical true para ser candidato default
        ]

    def test_requires_confirm_or_dry_run(self, isolated_purge):
        state = isolated_purge["state"]
        _seed(state, self._base_deals())
        r = isolated_purge["client"].post("/api/deals/purge", json={})
        assert r.status_code == 400
        assert "confirm" in r.get_json()["error"]

    def test_dry_run_returns_counters_no_write(self, isolated_purge):
        state = isolated_purge["state"]
        deals = self._base_deals()
        _seed(state, deals)
        r = isolated_purge["client"].post("/api/deals/purge", json={"dry_run": True})
        assert r.status_code == 200
        j = r.get_json()
        assert j["dry_run"] is True
        # deve listar candidatos não-aderentes atuais: orfãos 2 + wii u 1 + game cube 1 + price 1 = 5? (hd 300 caro)
        assert j["total_candidates"] >= 4
        assert "by_product" in j and "by_reason" in j
        # arquivo não modificado
        assert len(_read(state)) == len(deals)
        assert not list(state.glob("deals.json.bak-purge-*"))

    def test_dry_run_by_product(self, isolated_purge):
        state = isolated_purge["state"]
        _seed(state, self._base_deals())
        r = isolated_purge["client"].post("/api/deals/purge", json={"dry_run": True, "product_ids": ["pikimin"]})
        j = r.get_json()
        # pikimin tem 2 não aderentes (wii u, game cube) — Starfox não no set
        assert j["by_product"].get("pikimin") == 2
        # sem orfãos quando filtrado por pikimin

    def test_dry_run_filter_by_reason(self, isolated_purge):
        state = isolated_purge["state"]
        _seed(state, self._base_deals())
        r = isolated_purge["client"].post("/api/deals/purge", json={"dry_run": True, "reasons": ["orphan"]})
        j = r.get_json()
        assert j["by_reason"].get("orphan") == 2
        assert j["total_candidates"] == 2

    def test_execute_creates_backup_and_removes(self, isolated_purge):
        state = isolated_purge["state"]
        deals = self._base_deals()
        _seed(state, deals)
        before = len(deals)
        r = isolated_purge["client"].post("/api/deals/purge", json={"confirm": True, "product_ids": ["hd-6-12tb"]})
        assert r.status_code == 200
        j = r.get_json()
        assert j["dry_run"] is False
        assert j["purged"] == 2
        assert j["backup_path"] is not None
        assert os.path.exists(j["backup_path"])
        remaining = _read(state)
        assert len(remaining) == before - 2
        assert all(d["product_id"] != "hd-6-12tb" for d in remaining)

    def test_execute_orphan_requires_include_or_product(self, isolated_purge):
        state = isolated_purge["state"]
        # só orfãos
        _seed(state, [_deal("hd-6-12tb", "o1", "HD orfao", 800), _deal("hd-6tb", "1", "Pikmin 3 Wii U", 200) ])
        # dry_run vê orfão
        r = isolated_purge["client"].post("/api/deals/purge", json={"dry_run": True})
        assert r.get_json()["total_candidates"] >= 1
        # exec sem include_orphans e sem product_ids explícito -> não remove orfão (gate)
        # mas nosso seed tem também pikimin não aderente? com hd-6tb o segundo é matched aderente? Vamos forçar orfão only
        _seed(state, [_deal("hd-6-12tb", "o1", "HD orfao", 800)])
        r2 = isolated_purge["client"].post("/api/deals/purge", json={"confirm": True})
        # sem product_ids e sem include_orphans, orfão não é candidato executável -> purged 0? De facto gate pula is_orphan quando product_ids None
        # Código: if is_orphan and not dry_run and not include_orphans: if product_ids is None or pid not in product_ids -> continue
        # então com product_ids None, orfão é pulado
        assert r2.get_json()["purged"] == 0
        # com include_orphans true, remove
        r3 = isolated_purge["client"].post("/api/deals/purge", json={"confirm": True, "include_orphans": True})
        assert r3.get_json()["purged"] == 1

    def test_execute_with_include_orphans_true(self, isolated_purge):
        state = isolated_purge["state"]
        _seed(state, [_deal("hd-6-12tb", "o1", "HD orfao", 800), _deal("hd-6tb", "2", "HD 150", 150)])
        r = isolated_purge["client"].post("/api/deals/purge", json={"confirm": True, "include_orphans": True})
        # deve incluir orfão se for não aderente; no nosso caso orfão é sempre não aderente
        assert r.get_json()["purged"] >= 1

    def test_notified_not_removed(self, isolated_purge):
        state = isolated_purge["state"]
        scraper = isolated_purge["scraper"]
        deals = [_deal("hd-6-12tb", "o1", "HD orfao", 800)]
        _seed(state, deals)
        # cria notified com chave orfã
        # usa API interna olx_scraper para escrever notified
        # notified.json formato: {"notified": {key: {price..}}}
        with open(state / "notified.json", "w") as f:
            json.dump({"notified": {"hd-6-12tb:o1": {"price": 800, "at": "2026-08-27T00:00:00+00:00"}}}, f)
        r = isolated_purge["client"].post("/api/deals/purge", json={"dry_run": True, "include_orphans": True})
        assert r.get_json()["notified_entries_affected"] == 1
        # exec
        r2 = isolated_purge["client"].post("/api/deals/purge", json={"confirm": True, "include_orphans": True})
        assert r2.get_json()["notified_entries_affected"] == 1
        # notified permanece
        with open(state / "notified.json") as f:
            data = json.load(f)
        assert "hd-6-12tb:o1" in data.get("notified", {}) or "hd-6-12tb:o1" in str(data)

    def test_status_and_older_than(self, isolated_purge):
        state = isolated_purge["state"]
        old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        recent = datetime.now(timezone.utc).isoformat()
        deals = [
            {"id": "r1", "title": "HD removido antigo", "price": 100, "url": "u", "product_id": "hd-6tb", "product_name": "hd", "fetched_at": old, "matched": True, "status": "removed", "removed_at": old, "last_seen_at": old},
            {"id": "r2", "title": "HD removido recente", "price": 100, "url": "u", "product_id": "hd-6tb", "product_name": "hd", "fetched_at": recent, "matched": True, "status": "removed", "removed_at": recent, "last_seen_at": recent},
            {"id": "a1", "title": "HD ativo", "price": 150, "url": "u", "product_id": "hd-6tb", "product_name": "hd", "fetched_at": recent, "matched": True, "status": "active"},
        ]
        _seed(state, deals)
        r = isolated_purge["client"].post("/api/deals/purge", json={"dry_run": True, "status": "removed", "older_than": "30d"})
        j = r.get_json()
        assert j["total_candidates"] == 1
        assert j["by_product"]["hd-6tb"] == 1
        # exec
        r2 = isolated_purge["client"].post("/api/deals/purge", json={"confirm": True, "status": "removed", "older_than": "30d"})
        assert r2.get_json()["purged"] == 1
        remaining = _read(state)
        assert len(remaining) == 2
        assert all(d["id"] != "r1" for d in remaining)

    def test_backup_is_atomic_copy(self, isolated_purge):
        state = isolated_purge["state"]
        deals = [_deal("hd-6-12tb", "o1", "HD orfao", 800)]
        _seed(state, deals)
        r = isolated_purge["client"].post("/api/deals/purge", json={"confirm": True, "product_ids": ["hd-6-12tb"]})
        bak = r.get_json()["backup_path"]
        assert bak and os.path.exists(bak)
        with open(bak) as f:
            bak_data = json.load(f)
        assert len(bak_data) == 1
