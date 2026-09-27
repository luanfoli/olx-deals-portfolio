"""
Testes E2E — fluxo completo via API HTTP (item 3: dedupe Telegram + integração geral).

Exercita o stack real de ponta a ponta: POST /api/scrape -> thread _run_scrape ->
scrape() com parsing real de HTML (BeautifulSoup) -> filtros -> save_history ->
notify() com dedupe em notified.json -> GET /api/deals / renotify / clear.

Apenas as bordas externas são simuladas (sem rede):
- olx_scraper.curl_fetch     -> HTML de cards OLX controlado por catálogo mutável
- olx_scraper._telegram_send -> grava chamadas; falha programável
- load_telegram_config       -> token/chat fixos
- random.uniform             -> elimina o sleep anti-ban entre fetches

Cenários:
1. 1º scrape notifica; 2º scrape igual -> 0 enviados (dedupe ponta a ponta)
2. deals.json apagado entre runs -> registro segura o reenvio
3. Mudança de preço > threshold (com histórico zerado) -> "Preço atualizado"
4. Telegram fora do ar -> não persiste; run seguinte re-tenta
5. Limpar registro (+ histórico zerado) -> reenvia tudo
6. Renotify via API -> remove chave, envia 1, re-persiste
7. Filtro ?notified=0|1 reflete o registro
8. Upgrade path: deals.json pré-existente -> migração impede spam na 1ª run
9. Threshold configurável via PUT /api/config altera comportamento
10. Concorrência: 409 quando ocupado; ?queue=1 enfileira
"""

import json
import time

import pytest


# ---------------------------------------------------------------------------
# Infraestrutura
# ---------------------------------------------------------------------------

def _card_html(ad_id, title, price):
    """Card no formato que extract_cards_from_html parseia (âncora + preço no pai)."""
    return (f'<div><a data-testid="adcard-link" '
            f'href="https://www.olx.com.br/item/hd-{ad_id}.html">{title}</a> '
            f'R$ {price}</div>')


class FakeTg:
    def __init__(self):
        self.calls = []
        self.fail = False

    def __call__(self, token, chat_id, text, timeout=30):
        self.calls.append(text)
        return not self.fail


@pytest.fixture()
def e2e(tmp_path, monkeypatch):
    """Stack isolado em tmpdir com OLX e Telegram simulados."""
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
    monkeypatch.setattr(olx_scraper, "RUNS_FILE", str(state / "runs.json"))
    monkeypatch.setattr(olx_scraper, "RUNS_LOCK_FILE", str(state / ".runs.lock"))

    catalog = {}  # ad_id -> (title, price); mutável entre runs

    def fake_curl_fetch(url, cookies):
        cards = "".join(_card_html(aid, t, p) for aid, (t, p) in sorted(catalog.items()))
        return 200, f"<html><body>{cards}</body></html>"

    monkeypatch.setattr(olx_scraper, "curl_fetch", fake_curl_fetch)

    fake_tg = FakeTg()
    monkeypatch.setattr(olx_scraper, "_telegram_send", fake_tg)
    monkeypatch.setattr(olx_scraper, "load_telegram_config", lambda: ("tok-e2e", "chat-e2e"))
    # remove o sleep anti-ban (1.5–3.5s por fetch) para e2e rápido
    monkeypatch.setattr(olx_scraper.random, "uniform", lambda a, b: 0.001)

    # registro vazio já existente = estado pós-deploy da feature
    # (sem isso, a migração lazy engoliria a 1ª run quando deals.json surgir)
    (state / "notified.json").write_text('{"notified": {}}')

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        r = client.post("/api/products", json={
            "name": "E2E HD", "id": "e2e-hd",
            "queries": ["hd 8tb"], "pages": 1,
            "min_price": None, "max_price": None,
        })
        assert r.status_code == 201, r.get_json()
        # remove o produto default para o scrape ser determinístico
        r = client.delete("/api/products/hd-6-12tb")
        assert r.status_code == 200, r.get_json()

        yield {
            "client": client,
            "app": app_module,
            "scraper": olx_scraper,
            "catalog": catalog,
            "fake_tg": fake_tg,
            "hist_file": state / "deals.json",
            "notified_file": state / "notified.json",
            "state": state,
        }

        # segurança: não soltar monkeypatch com scrape em voo
        deadline = time.time() + 30
        while time.time() < deadline:
            with app_module.job_lock:
                if not app_module.job["running"]:
                    break
            time.sleep(0.05)


def _wait_done(e2e, timeout=30):
    """Espera o job de scrape terminar e devolve (status, logs concatenados)."""
    app_mod = e2e["app"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        with app_mod.job_lock:
            running = app_mod.job["running"]
            err = app_mod.job.get("error")
        if not running:
            assert not err, f"scrape falhou: {err}"
            logs = e2e["client"].get("/api/scrape/logs").get_json()["logs"]
            return "\n".join(logs)
        time.sleep(0.05)
    raise TimeoutError("scrape não terminou em tempo")


def _scrape(e2e, **kw):
    r = e2e["client"].post("/api/scrape", json=kw or {})
    assert r.status_code == 200, r.get_json()
    return _wait_done(e2e)


def _seed(e2e, *items):
    """items: (ad_id, título, preço)."""
    e2e["catalog"].clear()
    for aid, title, price in items:
        e2e["catalog"][aid] = (title, price)


# ---------------------------------------------------------------------------
# 1) Dedupe ponta a ponta: 1ª run envia, 2ª run igual não envia
# ---------------------------------------------------------------------------

class TestFluxoCompleto:
    def test_primeira_run_envia_segunda_pula(self, e2e):
        _seed(e2e, ("101", "HD Oito TB Western", 800), ("102", "HD Dez TB Seagate", 900))
        log1 = _scrape(e2e)
        assert "2 enviados" in log1
        assert len(e2e["fake_tg"].calls) == 1
        msg = e2e["fake_tg"].calls[0]
        assert "📦 E2E HD" in msg
        assert "HD Oito TB Western" in msg and "HD Dez TB Seagate" in msg
        assert "https://www.olx.com.br/item/hd-101.html" in msg

        # estado persistido
        reg = e2e["scraper"].load_notified()
        assert set(reg) == {"e2e-hd:101", "e2e-hd:102"}
        hist = json.loads(e2e["hist_file"].read_text())
        assert len(hist) == 2

        # segunda run idêntica: histórico segura tudo -> notify nem é chamado
        log2 = _scrape(e2e)
        assert "0 novos salvos" in log2
        assert len(e2e["fake_tg"].calls) == 1

    def test_deals_api_flags_pos_scrape(self, e2e):
        _seed(e2e, ("110", "HD Doze TB Toshiba", 950))
        _scrape(e2e)
        j = e2e["client"].get("/api/deals").get_json()
        assert j["total"] == 1
        d = j["deals"][0]
        assert d["notified"] is True
        assert d["matched"] is True
        assert d["product_id"] == "e2e-hd"


# ---------------------------------------------------------------------------
# 2) deals.json apagado -> registro segura
# ---------------------------------------------------------------------------

class TestHistoricoApagado:
    def test_sem_deals_json_nao_reenvia(self, e2e):
        _seed(e2e, ("120", "HD Oito TB Western", 800))
        _scrape(e2e)
        assert len(e2e["fake_tg"].calls) == 1

        e2e["hist_file"].unlink()  # reset/debug apagou o histórico
        log2 = _scrape(e2e)
        assert "0 enviados" in log2
        assert "1 já notificados" in log2
        assert len(e2e["fake_tg"].calls) == 1


# ---------------------------------------------------------------------------
# 3) Mudança de preço > threshold -> "Preço atualizado"
# ---------------------------------------------------------------------------

class TestMudancaPreco:
    def test_preco_novo_apos_historico_zerado_renotifica(self, e2e):
        _seed(e2e, ("130", "HD Oito TB Western", 1000))
        _scrape(e2e)

        # anúncio volta ao ar como "novo" (histórico zerado) com preço -11%
        e2e["hist_file"].unlink()
        _seed(e2e, ("130", "HD Oito TB Western", 890))
        log2 = _scrape(e2e)
        assert "1 re-notificados" in log2
        assert "Preço atualizado" in e2e["fake_tg"].calls[1]
        # registro atualizado com o preço novo
        assert e2e["scraper"].load_notified()["e2e-hd:130"]["price"] == 890.0

    def test_threshold_configuravel_via_api(self, e2e):
        r = e2e["client"].put("/api/config", json={"notify_price_change_pct": 20})
        assert r.status_code == 200

        _seed(e2e, ("131", "HD Oito TB Western", 1000))
        _scrape(e2e)

        # +10% estaria acima do default 5%, mas abaixo do novo 20% -> pula
        e2e["hist_file"].unlink()
        _seed(e2e, ("131", "HD Oito TB Western", 1100))
        log2 = _scrape(e2e)
        assert "1 já notificados" in log2
        assert len(e2e["fake_tg"].calls) == 1


# ---------------------------------------------------------------------------
# 4) Telegram fora do ar -> não persiste; run seguinte re-tenta
# ---------------------------------------------------------------------------

class TestTelegramFora:
    def test_falha_nao_persiste_e_retry_funciona(self, e2e):
        _seed(e2e, ("140", "HD Oito TB Western", 800))
        e2e["fake_tg"].fail = True
        log1 = _scrape(e2e)
        assert "erro" in log1.lower()
        assert e2e["scraper"].load_notified() == {}
        assert len(e2e["fake_tg"].calls) >= 1  # tentou enviar

        # recupera; simula eviction/reset do histórico para o deal voltar como 'new'
        e2e["fake_tg"].fail = False
        e2e["hist_file"].unlink()
        log2 = _scrape(e2e)
        assert "1 enviados" in log2
        assert set(e2e["scraper"].load_notified()) == {"e2e-hd:140"}


# ---------------------------------------------------------------------------
# 5) Limpar registro -> reenvio no próximo scrape (com histórico zerado)
# ---------------------------------------------------------------------------

class TestLimparRegistro:
    def test_clear_then_scrape_resends(self, e2e):
        _seed(e2e, ("150", "HD Oito TB Western", 800), ("151", "HD Dez TB Seagate", 900))
        _scrape(e2e)
        assert len(e2e["fake_tg"].calls) == 1

        r = e2e["client"].delete("/api/notified")
        assert r.get_json()["cleared"] == 2
        assert e2e["client"].get("/api/notified").get_json()["count"] == 0

        e2e["hist_file"].unlink()  # sem isso os deals não chegam como 'new'
        log2 = _scrape(e2e)
        assert "2 enviados" in log2
        assert len(e2e["fake_tg"].calls) == 2


# ---------------------------------------------------------------------------
# 6) Renotify via API
# ---------------------------------------------------------------------------

class TestRenotifyEndpoint:
    def test_renotify_envia_um_e_repersiste(self, e2e):
        _seed(e2e, ("160", "HD Oito TB Western", 800), ("161", "HD Dez TB Seagate", 900))
        _scrape(e2e)

        r = e2e["client"].post("/api/deals/renotify",
                               json={"product_id": "e2e-hd", "ad_id": "160"})
        assert r.status_code == 200
        j = r.get_json()
        assert j["removed"] == 1
        assert j["counters"]["enviados"] == 1
        assert "HD Oito TB Western" in e2e["fake_tg"].calls[-1]
        # chave volta ao registro após sucesso
        assert "e2e-hd:160" in e2e["scraper"].load_notified()

    def test_renotify_inexistente_404(self, e2e):
        _seed(e2e, ("162", "HD Oito TB Western", 800))
        _scrape(e2e)
        r = e2e["client"].post("/api/deals/renotify",
                               json={"product_id": "e2e-hd", "ad_id": "999999"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# 7) Filtro notified na API
# ---------------------------------------------------------------------------

class TestFiltroNotificado:
    def test_filtro_reflete_registro(self, e2e):
        _seed(e2e, ("170", "HD Oito TB Western", 800), ("171", "HD Dez TB Seagate", 900))
        _scrape(e2e)
        # simula 171 pendente (ex.: enviado antes do registro existir)
        e2e["scraper"].remove_notified(["e2e-hd:171"])

        j0 = e2e["client"].get("/api/deals?notified=0").get_json()
        assert [d["id"] for d in j0["deals"]] == ["171"]
        j1 = e2e["client"].get("/api/deals?notified=1").get_json()
        assert [d["id"] for d in j1["deals"]] == ["170"]
        jall = e2e["client"].get("/api/deals").get_json()
        flags = {d["id"]: d["notified"] for d in jall["deals"]}
        assert flags == {"170": True, "171": False}


# ---------------------------------------------------------------------------
# 8) Upgrade path: deals.json pré-existente, sem notified.json
# ---------------------------------------------------------------------------

class TestUpgradePath:
    def test_migracao_impede_spam_na_primeira_run(self, e2e):
        # estado pré-feature: sem registro e com histórico legado já existente
        e2e["notified_file"].unlink()
        legacy = [{
            "id": "180", "title": "HD Oito TB Western", "price": 800.0,
            "url": "https://www.olx.com.br/item/hd-180.html",
            "product_id": "e2e-hd", "product_name": "E2E HD",
            "fetched_at": "2026-08-01T12:00:00+00:00", "matched": True,
        }]
        e2e["hist_file"].write_text(json.dumps(legacy))

        # qualquer leitura dispara a migração lazy
        assert e2e["client"].get("/api/notified").get_json()["count"] == 1

        # mesmo anúncio continua no ar -> NADA é enviado na 1ª execução
        # (histórico segura: new=[] e notify nem roda; registro é a 2ª linha de defesa)
        _seed(e2e, ("180", "HD Oito TB Western", 800))
        log = _scrape(e2e)
        assert "0 novos salvos" in log
        assert e2e["fake_tg"].calls == []

        # anúncio realmente novo é notificado normalmente
        _seed(e2e, ("180", "HD Oito TB Western", 800), ("181", "HD Novo TB Seagate", 700))
        log2 = _scrape(e2e)
        assert "1 enviados" in log2
        assert len(e2e["fake_tg"].calls) == 1


# ---------------------------------------------------------------------------
# 9) Concorrência: 409 ocupado, fila com ?queue=1
# ---------------------------------------------------------------------------

class TestConcorrencia:
    def test_ocupado_409_e_fila_202(self, e2e):
        app_mod = e2e["app"]
        _seed(e2e, ("190", "HD Oito TB Western", 800))
        with app_mod.job_lock:
            app_mod.job["running"] = True  # simula job em andamento

        try:
            r = e2e["client"].post("/api/scrape", json={})
            assert r.status_code == 409
            r = e2e["client"].post("/api/scrape?queue=1", json={})
            assert r.status_code == 202
            assert r.get_json()["queued"] is True
        finally:
            with app_mod.job_lock:
                app_mod.job["running"] = False

        # libera e drena a fila pelo próprio mecanismo -> scrape enfileirado roda
        # e notifica ponta a ponta
        app_mod._drain_queue()
        log = _wait_done(e2e)
        assert "1 enviados" in log
        assert len(e2e["fake_tg"].calls) == 1
        assert "e2e-hd:190" in e2e["scraper"].load_notified()
