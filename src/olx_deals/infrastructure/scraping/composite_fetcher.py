"""infrastructure.scraping.composite_fetcher — curl→fallback PW + auto-refresh cookies.
Elimina if use_pw_only olx_scraper.py:2142 e list[bool] hack.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .fetcher import Fetcher


class CompositeFetcher:
    """Tenta Curl; se falhar (None), faz 1× refresh de cookies via playwright_login e tenta PW.

    Encapsula o flag list[bool] antigo como estado interno _refreshed.
    Compartilha a lista mutável de cookies com CurlFetcher quando possível.
    """

    def __init__(
        self,
        curl_fetcher: "Fetcher | None" = None,
        playwright_fetcher: "Fetcher | None" = None,
        search_url: str = "",
        cookies: list | None = None,
        cookies_refreshed_flag: list | None = None,
    ):
        # lazy import para evitar ciclo
        if curl_fetcher is None:
            try:
                from .curl_fetcher import CurlFetcher

                curl_fetcher = CurlFetcher(cookies=cookies if cookies is not None else [])
            except Exception:
                curl_fetcher = None  # type: ignore[assignment]
        if playwright_fetcher is None:
            try:
                from .playwright_fetcher import PlaywrightFetcher

                playwright_fetcher = PlaywrightFetcher()
            except Exception:
                playwright_fetcher = None  # type: ignore[assignment]
        self.curl = curl_fetcher
        self.pw = playwright_fetcher
        self.search_url = search_url or "https://www.olx.com.br/brasil?q={query}&opst=2&page={page}"
        # mantém referência mutável compartilhada
        self.cookies = cookies if cookies is not None else []
        # compat com flag externo legado (list[bool]): sincroniza
        self._external_flag = cookies_refreshed_flag
        if isinstance(cookies_refreshed_flag, list) and cookies_refreshed_flag and cookies_refreshed_flag[0]:
            self._refreshed = True
        else:
            self._refreshed = False

    def _do_refresh(self) -> None:
        if self._refreshed:
            return
        # marca antes para evitar reentrada
        self._refreshed = True
        if isinstance(self._external_flag, list):
            try:
                self._external_flag[0] = True
            except Exception:
                pass
        try:
            from .playwright_fetcher import playwright_login
            from .curl_fetcher import load_cookies_dict

            ok = playwright_login(search_url=self.search_url)
            if ok:
                new_cookies = load_cookies_dict()
                # muta listas compartilhadas
                try:
                    self.cookies[:] = new_cookies  # type: ignore[attr-defined]
                except Exception:
                    # se não for lista mutável, substitui
                    self.cookies = new_cookies  # type: ignore[assignment]
                # também muta CurlFetcher se tiver referência distinta
                if self.curl is not None and hasattr(self.curl, "cookies"):
                    try:
                        cur = getattr(self.curl, "cookies")
                        if cur is not self.cookies and isinstance(cur, list):
                            cur[:] = new_cookies
                        else:
                            setattr(self.curl, "cookies", self.cookies)
                    except Exception:
                        pass
        except Exception:
            pass

    def fetch(self, url: str) -> str | None:
        # tenta curl primeiro
        html = None
        if self.curl is not None:
            try:
                html = self.curl.fetch(url)  # type: ignore[attr-defined]
            except Exception:
                html = None
            if html:
                return html
        # fallback: refresh 1× + PW
        if not self._refreshed:
            self._do_refresh()
        if self.pw is not None:
            try:
                return self.pw.fetch(url)  # type: ignore[attr-defined]
            except Exception:
                return None
        return None
