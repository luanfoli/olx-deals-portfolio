"""
Smoke test do MCP server (PLANO-OPENAPI-MCP Fase 3).

Sobe o app Flask real em porta efêmera (thread, estado isolado em tmpdir),
aponta o mcp_server para ela e fala com o FastMCP in-memory via
fastmcp.Client — mesmo caminho que o stdio do opencode percorre.

- Conjunto de tools == 24 operações da spec MENOS putConfig (excluída).
- getHealth funciona ponta a ponta (spec → tool → HTTP → Flask).
- Ciclo createProduct → updateProduct → deleteProduct com id smoke-test-*.
"""

import asyncio
import importlib
import json
import threading

import pytest

EXPECTED_TOOLS = {
    "getHealth", "testTelegram",
    "getConfig",
    "listProducts", "createProduct", "getProduct", "updateProduct", "deleteProduct",
    "getDeals", "getRawDeals", "renotifyDeals", "listNotified", "clearNotified", "purgeDeals",
    "triggerScrape", "getScrapeStatus", "getScrapeLogs", "olxLogin", "getCookiesStatus",
    "getSchedulesStatus", "triggerScheduleById", "getScheduleQueue",
    "clearScheduleQueue", "runDueSchedules",
    "getOpenapiSpec",
    "listRuns", "getRun", "clearRuns", "deleteRun",
    "listTunerRequests", "createTunerRequest", "getTunerRequest", "tunerAction", "refineTunerRequest",
    "listTunerModels", "getTunerLlmConfig", "putTunerLlmConfig",
}
EXCLUDED = {"putConfig"}


def _isolate_state(tmp_path, monkeypatch):
    """Estado isolado em tmpdir (mesmo padrão de test_auth.py/test_e2e.py)."""
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
    monkeypatch.setattr(olx_scraper, "RUNS_FILE", str(state / "runs.json"))
    monkeypatch.setattr(olx_scraper, "RUNS_LOCK_FILE", str(state / ".runs.lock"))


@pytest.fixture(scope="module")
def mcp_server(tmp_path_factory):
    """App HTTP real (porta efêmera) + módulo mcp_server apontando pra ele."""
    import os

    import app as app_module

    tmp = tmp_path_factory.mktemp("mcp-smoke")

    class _MP:
        """monkeypatch mínimo module-scope (sem depender de pytest fixture)."""
        def __init__(self):
            self._undo = []
        def setattr(self, target, name, value):
            old = getattr(target, name)
            setattr(target, name, value)
            self._undo.append((target, name, old))
        def undo(self):
            for target, name, old in reversed(self._undo):
                setattr(target, name, old)

    mp = _MP()
    _isolate_state(tmp, mp)
    app_module.app.config["TESTING"] = True

    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", 0, app_module.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    old_url = os.environ.get("OLX_DEALS_URL")
    os.environ["OLX_DEALS_URL"] = f"http://127.0.0.1:{server.server_port}"
    try:
        yield importlib.import_module("mcp_server")
    finally:
        if old_url is None:
            os.environ.pop("OLX_DEALS_URL", None)
        else:
            os.environ["OLX_DEALS_URL"] = old_url
        server.shutdown()
        mp.undo()


def _json(result):
    """Extrai JSON da resposta da tool (data já vem parseado no fastmcp 3.x)."""
    data = getattr(result, "data", None)
    if isinstance(data, (dict, list)):
        return data
    text = result.content[0].text
    return json.loads(text)


def _run(coro):
    return asyncio.run(coro)


class TestConjuntoDeTools:
    def test_tools_sao_exatamente_as_da_spec_menos_putconfig(self, mcp_server):
        async def go():
            from fastmcp import Client

            async with Client(mcp_server.mcp) as c:
                return {t.name for t in await c.list_tools()}

        tools = _run(go())
        assert tools == EXPECTED_TOOLS - EXCLUDED
        assert EXCLUDED.isdisjoint(tools), "putConfig não pode virar tool"


class TestPontoAPonto:
    def test_gethealth(self, mcp_server):
        async def go():
            from fastmcp import Client

            async with Client(mcp_server.mcp) as c:
                return _json(await c.call_tool("getHealth", {}))

        j = _run(go())
        assert j["ok"] is True and "time" in j

    def test_ciclo_create_update_delete(self, mcp_server):
        pid = "smoke-test-mcp"

        async def go():
            from fastmcp import Client

            async with Client(mcp_server.mcp) as c:
                criado = _json(await c.call_tool("createProduct", {
                    "id": pid,
                    "name": "Smoke MCP",
                    "queries": ["hd 8tb"],
                    "pages": 1,
                    "enabled": False,
                }))
                atualizado = _json(await c.call_tool("updateProduct", {
                    "pid": pid,
                    "max_price": 900,
                }))
                obtido = _json(await c.call_tool("getProduct", {"pid": pid}))
                deletado = _json(await c.call_tool("deleteProduct", {"pid": pid}))
                try:
                    await c.call_tool("getProduct", {"pid": pid})
                    pos = "tool deveria ter falhado com 404"
                except Exception as e:
                    pos = e
            return criado, atualizado, obtido, deletado, pos

        criado, atualizado, obtido, deletado, pos = _run(go())
        assert criado["ok"] is True, criado
        assert criado["product"]["id"] == pid
        assert criado["product"]["enabled"] is False
        assert atualizado["ok"] is True and atualizado["product"]["max_price"] == 900
        assert obtido["product"]["queries"] == ["hd 8tb"]
        assert deletado["ok"] is True
        # fastmcp converte erro HTTP da API em exceção (404 após o delete)
        assert isinstance(pos, Exception) and "404" in str(pos), pos
