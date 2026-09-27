"""infrastructure.scraping.curl_fetcher — extraído de olx_scraper.py:2042. """
from __future__ import annotations

import json
import os

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def curl_fetch(url: str, cookies: list | None, proxies=None) -> tuple[int, str]:
    """Fetch via curl_cffi (TLS impersonation). Retorna (status_code, html).

    proxies: None (lê OLX_*_PROXY do env; ausente = rota direta, idêntico ao
    atual), str ou dict curl_cffi. Inválido → ValueError claro.
    """
    from curl_cffi import requests as cf

    try:
        from .proxy import get_proxy_config

        proxy_cfg = get_proxy_config(proxies)
    except ValueError:
        raise
    except Exception:
        proxy_cfg = None

    s = cf.Session(impersonate="chrome124")
    if cookies:
        for c in cookies:
            s.cookies.set(c.get("name"), c.get("value"), domain=c.get("domain", ".olx.com.br"))
    s.headers.update(
        {
            "User-Agent": UA,
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    r = s.get(url, timeout=30, proxies=proxy_cfg or None)
    return r.status_code, r.text


def load_cookies_dict(cookiejar_path: str | None = None) -> list:
    """Carrega cookies do storage_state do Playwright (lista)."""
    if cookiejar_path is None:
        try:
            import olx_scraper  # type: ignore

            cookiejar_path = olx_scraper.COOKIE_JAR  # type: ignore[attr-defined]
        except Exception:
            cookiejar_path = os.path.expanduser("~/.olx/cookies.json")
    try:
        with open(cookiejar_path) as f:
            ss = json.load(f)
        return ss.get("cookies", []) if isinstance(ss, dict) else []
    except Exception:
        return []


class CurlFetcher:
    """Adapter Fetcher via curl_cffi. fetch(url)->html | None (None se status!=200)."""

    def __init__(self, cookies: list | None = None, ua: str | None = None, proxies=None):
        # mantém referência mutável quando lista, para Composite poder mutar
        self.cookies = cookies if cookies is not None else []
        self.ua = ua or UA
        self.proxies = proxies

    def fetch(self, url: str) -> str | None:
        # respeita monkeypatch de olx_scraper.curl_fetch (tests e2e/exclude usam fake)
        try:
            import olx_scraper  # type: ignore

            fn = getattr(olx_scraper, "curl_fetch", None)
            # se olx_scraper.curl_fetch foi monkeypatchado para fake, ele será diferente do local
            if fn is not None and fn is not curl_fetch:
                try:
                    status, html = fn(url, self.cookies)  # type: ignore[misc]
                    if status == 200 and html:
                        return html
                    return None
                except Exception:
                    return None
        except Exception:
            pass
        try:
            status, html = curl_fetch(url, self.cookies, self.proxies)
        except ValueError:
            raise
        except Exception as e:
            # falha da rota alternativa não é silenciosa: log acionável + fallback
            try:
                from .proxy import get_proxy_config

                if get_proxy_config(self.proxies):
                    try:
                        import olx_scraper  # type: ignore

                        olx_scraper.log(f"rota alternativa falhou no curl ({type(e).__name__}: {e}) — caindo para Playwright; verifique OLX_*_PROXY ou use rota direta")  # type: ignore[attr-defined]
                    except Exception:
                        pass
            except Exception:
                pass
            return None
        if status == 200 and html:
            return html
        return None
