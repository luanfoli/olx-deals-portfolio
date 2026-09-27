"""infrastructure.scraping.playwright_fetcher — extraído de olx_scraper.py:2057,2138."""
from __future__ import annotations

import json
import os

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def playwright_fetch(url: str, pw=None, browser=None, cookiejar_path: str | None = None, proxy=None) -> str | None:
    """Fetch via Playwright DOM. Se pw/browser None, cria e fecha os próprios.

    proxy: None (lê OLX_*_PROXY do env; ausente = rota direta), str ou dict.
    Inválido → ValueError claro. Aplicado por contexto (funciona com browser
    compartilhado); modo TUN global do WARP não precisa deste param.
    """
    from playwright.sync_api import sync_playwright

    try:
        from .proxy import build_playwright_proxy, get_proxy_config

        pw_proxy = build_playwright_proxy(get_proxy_config(proxy))
    except ValueError:
        raise
    except Exception:
        pw_proxy = None

    if cookiejar_path is None:
        try:
            import olx_scraper  # type: ignore

            cookiejar_path = olx_scraper.COOKIE_JAR  # type: ignore[attr-defined]
        except Exception:
            cookiejar_path = os.path.expanduser("~/.olx/cookies.json")
    own_pw, own_browser = False, False
    if pw is None:
        pw = sync_playwright().start()
        own_pw = True
    if browser is None:
        browser = pw.chromium.launch(headless=True)
        own_browser = True
    ctx_kwargs: dict = dict(
        user_agent=UA,
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
        viewport={"width": 1440, "height": 900},
    )
    if pw_proxy:
        ctx_kwargs["proxy"] = pw_proxy
    if cookiejar_path and os.path.exists(cookiejar_path):
        try:
            with open(cookiejar_path) as f:
                ctx_kwargs["storage_state"] = json.load(f)
        except Exception:
            pass
    ctx = browser.new_context(**ctx_kwargs)
    pg = ctx.new_page()
    try:
        pg.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')
    except Exception:
        pass
    try:
        resp = pg.goto(url, timeout=45000, wait_until="domcontentloaded")
        if resp and getattr(resp, "status", None) == 200:
            try:
                pg.wait_for_selector('[data-testid="adcard-link"]', timeout=20000)
            except Exception:
                # log shim: evita importar olx_scraper.log circular
                try:
                    import olx_scraper  # type: ignore

                    olx_scraper.log("aviso: selector de cards não apareceu em 20s")
                except Exception:
                    pass
            try:
                pg.wait_for_timeout(1500)
            except Exception:
                pass
            try:
                html = pg.content()
            except Exception:
                html = None
            return html
        return None
    except Exception as e:
        try:
            import olx_scraper  # type: ignore

            olx_scraper.log(f"playwright error: {e}")
        except Exception:
            pass
        return None
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        if own_browser:
            try:
                browser.close()
            except Exception:
                pass
        if own_pw:
            try:
                pw.stop()
            except Exception:
                pass


def playwright_login(cookies_path: str | None = None, search_url: str | None = None) -> bool:
    from playwright.sync_api import sync_playwright

    if cookies_path is None:
        try:
            import olx_scraper  # type: ignore

            cookies_path = olx_scraper.COOKIE_JAR  # type: ignore[attr-defined]
        except Exception:
            cookies_path = os.path.expanduser("~/.olx/cookies.json")
    if search_url is None:
        try:
            import olx_scraper  # type: ignore

            cfg = olx_scraper.load_config()  # type: ignore[attr-defined]
            prods = cfg.get("products") or []
            search_url = prods[0].get("search_url", "https://www.olx.com.br/brasil?q={query}&opst=2&page={page}") if prods else "https://www.olx.com.br/brasil?q={query}&opst=2&page={page}"
        except Exception:
            search_url = "https://www.olx.com.br/brasil?q={query}&opst=2&page={page}"
    try:
        import olx_scraper  # type: ignore

        olx_scraper.log("Login: abrindo navegador para resolver Cloudflare e capturar cookies...")
    except Exception:
        pass
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=UA,
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            viewport={"width": 1440, "height": 900},
        )
        pg = ctx.new_page()
        try:
            pg.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')
        except Exception:
            pass
        try:
            resp = pg.goto(search_url.format(query="hd", page=1), timeout=45000, wait_until="domcontentloaded")
        except Exception:
            resp = None
        try:
            pg.wait_for_selector('[data-testid="adcard-link"]', timeout=20000)
        except Exception:
            try:
                import olx_scraper  # type: ignore

                olx_scraper.log("aviso: selector de cards não apareceu em 20s")
            except Exception:
                pass
        try:
            pg.wait_for_timeout(1500)
        except Exception:
            pass
        try:
            cards = pg.locator('[data-testid="adcard-link"]').count()
        except Exception:
            cards = 0
        try:
            import olx_scraper  # type: ignore

            olx_scraper.log(f"status={getattr(resp, 'status', None) if resp else None} cards={cards}")
        except Exception:
            pass
        try:
            storage = ctx.storage_state()
        except Exception:
            storage = {"cookies": []}
        try:
            os.makedirs(os.path.dirname(cookies_path) or ".", exist_ok=True)
            with open(cookies_path, "w") as f:
                json.dump(storage, f)
        except Exception:
            pass
        try:
            import olx_scraper  # type: ignore

            olx_scraper.log(f"cookies salvos em {cookies_path}")
        except Exception:
            pass
        try:
            ctx.close()
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass
    return cards > 0


class PlaywrightFetcher:
    """Adapter Fetcher via Playwright DOM."""

    def __init__(self, pw=None, browser=None, cookiejar_path: str | None = None, proxy=None):
        self.pw = pw
        self.browser = browser
        if cookiejar_path is None:
            try:
                import olx_scraper  # type: ignore

                self.cookiejar_path = olx_scraper.COOKIE_JAR  # type: ignore[attr-defined]
            except Exception:
                self.cookiejar_path = os.path.expanduser("~/.olx/cookies.json")
        else:
            self.cookiejar_path = cookiejar_path
        self.proxy = proxy

    def fetch(self, url: str) -> str | None:
        # respeita monkeypatch de olx_scraper.playwright_fetch (tests podem mockar)
        try:
            import olx_scraper  # type: ignore

            fn = getattr(olx_scraper, "playwright_fetch", None)
            if fn is not None and fn is not playwright_fetch:
                try:
                    return fn(url, pw=self.pw, browser=self.browser, cookiejar_path=self.cookiejar_path)  # type: ignore[misc]
                except TypeError:
                    # fake pode ter assinatura (url) simpler
                    try:
                        return fn(url)  # type: ignore[misc]
                    except Exception:
                        return None
                except Exception:
                    return None
        except Exception:
            pass
        try:
            return playwright_fetch(url, pw=self.pw, browser=self.browser, cookiejar_path=self.cookiejar_path, proxy=self.proxy)
        except ValueError:
            raise
        except Exception as e:
            try:
                from .proxy import get_proxy_config

                if get_proxy_config(self.proxy):
                    try:
                        import olx_scraper  # type: ignore

                        olx_scraper.log(f"rota alternativa falhou no Playwright ({type(e).__name__}: {e}) — verifique OLX_*_PROXY ou use rota direta")  # type: ignore[attr-defined]
                    except Exception:
                        pass
            except Exception:
                pass
            return None
