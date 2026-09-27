"""
Testes de auth opt-in por token (PLANO-OPENAPI-MCP Fase 1).

- Sem OLX_API_TOKEN: API segue aberta (regressão — GUI/cron/testes intocados).
- Com token setado: toda /api/* exige header X-API-Token igual ao valor
  (comparação em tempo constante), exceto GET /api/health (liveness).
- Rotas não-API (/ e /static/*) ficam fora do gate.

O hook lê o global app.API_TOKEN (capturado do env no startup); os testes
trocam esse atributo via monkeypatch para simular os dois modos.
"""

import pytest


def _isolate_state(tmp_path, monkeypatch):
    """Estado isolado em tmpdir (mesma ideia do fixture `e2e` de test_e2e.py)."""
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


PRODUTO = {
    "name": "Auth Teste", "id": "auth-test",
    "queries": ["hd 8tb"], "pages": 1,
    "min_price": None, "max_price": None,
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Client de teste com estado isolado e API_TOKEN limpo (modo aberto)."""
    import app as app_module

    _isolate_state(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module, "API_TOKEN", None)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def token_client(client, monkeypatch):
    """Mesmo client, mas com o gate ativo (simula OLX_API_TOKEN no startup)."""
    import app as app_module

    monkeypatch.setattr(app_module, "API_TOKEN", "s3cret-token")
    return client


# ---------------------------------------------------------------------------
# Sem token no ambiente: tudo aberto (regressão)
# ---------------------------------------------------------------------------

class TestSemToken:
    def test_leitura_aberta(self, client):
        r = client.get("/api/products")
        assert r.status_code == 200
        assert "products" in r.get_json()

    def test_escrita_aberta(self, client):
        r = client.post("/api/products", json=PRODUTO)
        assert r.status_code == 201, r.get_json()

    def test_health_aberta(self, client):
        assert client.get("/api/health").status_code == 200


# ---------------------------------------------------------------------------
# Com token no ambiente: gate ativo em /api/*
# ---------------------------------------------------------------------------

class TestComToken:
    def test_health_permanece_aberta(self, token_client):
        assert token_client.get("/api/health").status_code == 200

    def test_post_sem_header_401(self, token_client):
        r = token_client.post("/api/products", json=PRODUTO)
        assert r.status_code == 401
        j = r.get_json()
        assert j["ok"] is False
        assert "X-API-Token" in j["error"]

    def test_get_sem_header_401(self, token_client):
        # leitura também é gated
        assert token_client.get("/api/products").status_code == 401

    def test_header_errado_401(self, token_client):
        r = token_client.post("/api/products", json=PRODUTO,
                              headers={"X-API-Token": "errado"})
        assert r.status_code == 401
        assert r.get_json()["ok"] is False

    def test_header_certo_201(self, token_client):
        r = token_client.post("/api/products", json=PRODUTO,
                              headers={"X-API-Token": "s3cret-token"})
        assert r.status_code == 201, r.get_json()
        assert r.get_json()["product"]["id"] == "auth-test"

    def test_header_em_rota_de_leitura_funciona(self, token_client):
        h = {"X-API-Token": "s3cret-token"}
        assert token_client.get("/api/products", headers=h).status_code == 200
        assert token_client.get("/api/deals", headers=h).status_code == 200


# ---------------------------------------------------------------------------
# Gate cobre só /api/*: SPA e estáticos continuam abertos mesmo com token
# ---------------------------------------------------------------------------

class TestRotasNaoApi:
    def test_spa_aberta_com_token_ativo(self, token_client):
        r = token_client.get("/")
        assert r.status_code == 200
        assert b"<script" in r.data  # modularizado Fase 6: pode ser <script src=...></script> ou inline
