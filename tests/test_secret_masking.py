"""
Segredos não vazam nas respostas da API/MCP (BACKLOG #6).

- GET /api/config e respostas de escrita trazem telegram_token mascarado
  ("***" + últimos 4 chars), nunca o valor real.
- PUT /api/config com token mascarado preserva o armazenado (a SPA reenvia
  o valor do input, que vem mascarado do GET).
- POST /api/config/telegram/test com token mascarado/ausente usa o armazenado.
"""

import pytest

REAL_TOKEN = "123456789:AAAbbbCCCdddEEE"
MASKED_TOKEN = "***dEEE"

PRODUTO = {
    "name": "Mascara Teste", "id": "mascara-test",
    "queries": ["hd 8tb"], "pages": 1,
}


def _isolate_state(tmp_path, monkeypatch):
    """Estado isolado em tmpdir (mesmo padrão de test_auth.py)."""
    import olx_scraper

    state = tmp_path / ".olx"
    state.mkdir()
    monkeypatch.setattr(olx_scraper, "STATE_DIR", str(state))
    monkeypatch.setattr(olx_scraper, "CONFIG_FILE", str(state / "config.json"))
    monkeypatch.setattr(olx_scraper, "SCHEDULES_FILE", str(state / "schedules.json"))
    monkeypatch.setattr(olx_scraper, "COOKIE_JAR", str(state / "cookies.json"))
    monkeypatch.setattr(olx_scraper, "HISTORY_FILE", str(state / "deals.json"))
    monkeypatch.setattr(olx_scraper, "NOTIFIED_FILE", str(state / "notified.json"))
    monkeypatch.setattr(olx_scraper, "NOTIFIED_LOCK_FILE", str(state / ".notified.lock"))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Client com estado isolado, config semeada com token real, gate aberto."""
    import olx_scraper
    import app as app_module

    _isolate_state(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module, "API_TOKEN", None)
    olx_scraper.save_config({"telegram_token": REAL_TOKEN, "telegram_chat_id": 42})
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestGetConfig:
    def test_get_mascara_telegram_token(self, client):
        j = client.get("/api/config").get_json()
        assert j["telegram_token"] == MASKED_TOKEN
        assert REAL_TOKEN not in client.get("/api/config").get_data(as_text=True)

    def test_maska_apenas_o_segredo(self, client):
        """chat_id não é segredo — segue intacto."""
        j = client.get("/api/config").get_json()
        assert j["telegram_chat_id"] == 42


class TestPutConfig:
    def test_put_com_valor_mascarado_preserva_armazenado(self, client, monkeypatch):
        import olx_scraper

        r = client.put("/api/config", json={"telegram_token": MASKED_TOKEN})
        assert r.status_code == 200
        assert r.get_json()["config"]["telegram_token"] == MASKED_TOKEN
        # disco continua com o valor real
        assert olx_scraper.load_config()["telegram_token"] == REAL_TOKEN

    def test_put_com_token_novo_substitui(self, client, monkeypatch):
        import olx_scraper

        novo = "987654321:ZZZyyyXXXwwwVVV"
        r = client.put("/api/config", json={"telegram_token": novo})
        assert r.status_code == 200
        assert olx_scraper.load_config()["telegram_token"] == novo
        assert novo not in r.get_data(as_text=True)

    def test_put_sem_chave_nao_altera(self, client, monkeypatch):
        import olx_scraper

        client.put("/api/config", json={"notify_price_change_pct": 10})
        assert olx_scraper.load_config()["telegram_token"] == REAL_TOKEN


class TestRespostasDeEscritaSemSegredo:
    def test_create_update_delete_nao_ecoam_token_real(self, client):
        r = client.post("/api/products", json=PRODUTO)
        assert r.status_code == 201
        pid = PRODUTO["id"]
        assert REAL_TOKEN not in r.get_data(as_text=True)

        r = client.put(f"/api/products/{pid}", json={"max_price": 500})
        assert REAL_TOKEN not in r.get_data(as_text=True)

        r = client.delete(f"/api/products/{pid}")
        assert r.status_code == 200
        assert REAL_TOKEN not in r.get_data(as_text=True)
        assert MASKED_TOKEN in r.get_json()["config"]["telegram_token"]


class TestTelegramTestFallback:
    def test_token_mascarado_usa_o_armazenado(self, client, monkeypatch):
        import curl_cffi.requests as cf_requests

        captured = {}

        def fake_post(url, **kw):
            captured["url"] = url

            class R:
                status_code = 200
                text = ""
            return R()

        monkeypatch.setattr(cf_requests, "post", fake_post)
        r = client.post(
            "/api/config/telegram/test",
            json={"token": MASKED_TOKEN, "chat_id": "42"},
        )
        assert r.get_json()["ok"] is True
        assert REAL_TOKEN in captured["url"]

    def test_token_ausente_usa_o_armazenado(self, client, monkeypatch):
        import curl_cffi.requests as cf_requests

        captured = {}

        def fake_post(url, **kw):
            captured["url"] = url

            class R:
                status_code = 200
                text = ""
            return R()

        monkeypatch.setattr(cf_requests, "post", fake_post)
        r = client.post("/api/config/telegram/test", json={"chat_id": "42"})
        assert r.get_json()["ok"] is True
        assert REAL_TOKEN in captured["url"]
