"""Rota alternativa opt-in — spike backlog 2 (issue #3).

Cobre sem rede real:
- Desabilitado (padrão): get_proxy_config → None; curl/playwright chamados sem proxy.
- Habilitado via env OLX_*_PROXY: curl recebe proxies, playwright recebe server.
- Inválido: ValueError claro (sem loop silencioso).
- Diagnóstico get_egress_ip parseia trace da Cloudflare.
"""
import pytest


def test_desabilitado_por_padrao(monkeypatch):
    from olx_deals.infrastructure.scraping.proxy import get_proxy_config

    monkeypatch.delenv("OLX_HTTP_PROXY", raising=False)
    monkeypatch.delenv("olx_http_proxy", raising=False)
    monkeypatch.delenv("OLX_HTTPS_PROXY", raising=False)
    monkeypatch.delenv("olx_https_proxy", raising=False)
    monkeypatch.delenv("OLX_ALL_PROXY", raising=False)
    monkeypatch.delenv("olx_all_proxy", raising=False)
    assert get_proxy_config(env={}) is None


def test_env_all_proxy_vale_para_http_e_https():
    from olx_deals.infrastructure.scraping.proxy import get_proxy_config

    cfg = get_proxy_config(env={"OLX_ALL_PROXY": "http://127.0.0.1:8888"})
    assert cfg == {"all": "http://127.0.0.1:8888", "http": "http://127.0.0.1:8888", "https": "http://127.0.0.1:8888"}


def test_explicit_str_tem_precedencia_sobre_env():
    from olx_deals.infrastructure.scraping.proxy import get_proxy_config

    cfg = get_proxy_config("socks5://127.0.0.1:1080", env={"OLX_HTTP_PROXY": "http://outro:1"})
    assert cfg == {"http": "socks5://127.0.0.1:1080", "https": "socks5://127.0.0.1:1080"}


@pytest.mark.parametrize("bad", ["notaurl", "ftp://host:21", "http://", "://x"])
def test_proxy_invalido_erro_claro(bad):
    from olx_deals.infrastructure.scraping.proxy import get_proxy_config

    with pytest.raises(ValueError, match="rota alternativa inválida"):
        get_proxy_config(bad, env={})
    with pytest.raises(ValueError, match="rota alternativa inválida"):
        get_proxy_config(env={"OLX_HTTP_PROXY": bad})


def test_env_vazio_e_rota_direta():
    from olx_deals.infrastructure.scraping.proxy import get_proxy_config

    assert get_proxy_config(env={"OLX_HTTP_PROXY": ""}) is None
    with pytest.raises(ValueError, match="rota alternativa inválida"):
        get_proxy_config("", env={})


def test_build_playwright_proxy_com_auth():
    from olx_deals.infrastructure.scraping.proxy import build_playwright_proxy

    assert build_playwright_proxy(None) is None
    out = build_playwright_proxy({"https": "http://user:pw@127.0.0.1:8888"})
    assert out == {"server": "http://127.0.0.1:8888", "username": "user", "password": "pw"}


def test_curl_fetch_usa_rota_quando_habilitado(monkeypatch):
    import olx_deals.infrastructure.scraping.curl_fetcher as curl_mod

    seen = {}

    class FakeResp:
        status_code = 200
        text = "<html></html>"

    class FakeSession:
        def __init__(self, *a, **k):
            pass

        @property
        def cookies(self):
            class C:
                def set(self, *a, **k):
                    pass

            return C()

        @property
        def headers(self):
            class H:
                def update(self, *a, **k):
                    pass

            return H()

        def get(self, url, timeout=None, proxies=None):
            seen["proxies"] = proxies
            return FakeResp()

    import curl_cffi.requests as cf

    monkeypatch.setattr(cf, "Session", FakeSession)
    status, _ = curl_mod.curl_fetch("https://x", [], proxies="http://127.0.0.1:8888")
    assert status == 200
    assert seen["proxies"] == {"http": "http://127.0.0.1:8888", "https": "http://127.0.0.1:8888"}


def test_curl_fetch_direto_sem_env(monkeypatch):
    import olx_deals.infrastructure.scraping.curl_fetcher as curl_mod
    import curl_cffi.requests as cf

    seen = {}

    class FakeResp:
        status_code = 200
        text = "<html></html>"

    class FakeSession:
        def __init__(self, *a, **k):
            pass

        @property
        def cookies(self):
            class C:
                def set(self, *a, **k):
                    pass

            return C()

        @property
        def headers(self):
            class H:
                def update(self, *a, **k):
                    pass

            return H()

        def get(self, url, timeout=None, proxies=None):
            seen["proxies"] = proxies
            return FakeResp()

    monkeypatch.setattr(cf, "Session", FakeSession)
    monkeypatch.delenv("OLX_HTTP_PROXY", raising=False)
    monkeypatch.delenv("OLX_HTTPS_PROXY", raising=False)
    monkeypatch.delenv("OLX_ALL_PROXY", raising=False)
    monkeypatch.delenv("olx_http_proxy", raising=False)
    monkeypatch.delenv("olx_https_proxy", raising=False)
    monkeypatch.delenv("olx_all_proxy", raising=False)
    status, _ = curl_mod.curl_fetch("https://x", [])
    assert status == 200
    assert seen["proxies"] is None


def test_get_egress_ip_parse(monkeypatch):
    import olx_deals.infrastructure.scraping.proxy as proxy_mod
    import curl_cffi.requests as cf

    class FakeResp:
        status_code = 200
        text = "ip=1.2.3.4\ncolo=GRU\nloc=BR\nwarp=off\n"

    monkeypatch.setattr(cf, "get", lambda *a, **k: FakeResp())
    out = proxy_mod.get_egress_ip()
    assert out["ip"] == "1.2.3.4"
    assert out["colo"] == "GRU"
    assert out["warp"] == "off"
    assert out["via_proxy"] is False


def test_mask_proxy_url_esconde_senha():
    from olx_deals.infrastructure.scraping.proxy import mask_proxy_url

    assert mask_proxy_url(None) is None
    assert mask_proxy_url("http://127.0.0.1:8888") == "http://127.0.0.1:8888"
    assert mask_proxy_url("http://user:segredo@127.0.0.1:8888") == "http://user:***@127.0.0.1:8888"


def _produto_minimo():
    return {"id": "x", "name": "X", "queries": ["q"], "pages": 1}


class _FakeFetcherOk:
    def __init__(self, html):
        self.html = html

    def fetch(self, url):
        return self.html


def test_scrape_direto_loga_rota_e_stats(monkeypatch):
    import olx_scraper

    for v in ("OLX_HTTP_PROXY", "OLX_HTTPS_PROXY", "OLX_ALL_PROXY", "olx_http_proxy", "olx_https_proxy", "olx_all_proxy"):
        monkeypatch.delenv(v, raising=False)
    logs = []
    html = '<div><a data-testid="adcard-link" href="https://www.olx.com.br/item/an-1.html">Hd 6TB</a> R$ 1000</div>'
    stats = {}
    res = olx_scraper.scrape(
        [_produto_minimo()], log_fn=logs.append, progress_cb=None, stats_out=stats, fetcher=_FakeFetcherOk(html)
    )
    assert any("rota direta" in m for m in logs)
    assert stats.get("route", {}).get("via_proxy") is False
    assert isinstance(res, list)


def test_scrape_proxy_invalido_aborta_com_erro_claro(monkeypatch):
    import olx_scraper

    monkeypatch.setenv("OLX_HTTP_PROXY", "notaurl")
    logs = []
    with pytest.raises(ValueError, match="rota alternativa inválida"):
        olx_scraper.scrape([_produto_minimo()], log_fn=logs.append, progress_cb=None, stats_out={})
    assert any("rota alternativa inválida" in m for m in logs)
