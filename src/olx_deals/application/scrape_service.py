"""application.scrape_service — orquestra Fetcher→Parser→Filters→dedupe.

Extraído de olx_scraper.py:2180 (_scrape_single_product) e olx_scraper.py:2325 (scrape).
Elimina o `if not use_pw_only` e o flag list[bool] via CompositeFetcher.
Mantém compat de assinatura com olx_scraper (shim).
"""
from __future__ import annotations

import random
import re
import time
from datetime import datetime, timezone

# imports lazy dentro das funções para evitar ciclo com olx_scraper

try:
    from olx_deals.domain.filters import DISCARD_PRICE, JUNK_RE, _compile_exclude_res
except Exception:  # fallback se domain não carregado (tests isolados)
    DISCARD_PRICE = "price"
    JUNK_RE = re.compile(
        r"\b(dock|station|suporte|at[eé]|docking|encosto|cabo|clip|gabinete|case|c[aá]mera)\b"
        r"|recupera|reparo|conserto|quebrado|defeito|p/?a/?r/?a?\s?pecas|pecas",
        re.I,
    )

    def _compile_exclude_res(keywords):  # type: ignore[no-redef]
        res = []
        for kw in keywords or []:
            try:
                res.append(re.compile(r"(?<![a-z])" + re.escape(str(kw)), re.I))
            except re.error:
                continue
        return res


def _make_fetcher(product: dict, cookies: list, pw_handle, browser, cookies_refreshed_flag, search_url: str):
    """Factory: retorna Fetcher apropriado (Strategy). Elimina if use_pw_only espalhado."""
    use_pw_only = bool(product.get("playwright_only", False))
    if use_pw_only:
        try:
            from olx_deals.infrastructure.scraping.playwright_fetcher import PlaywrightFetcher

            return PlaywrightFetcher(pw=pw_handle, browser=browser)
        except Exception:
            # fallback: tenta importar direto de olx_scraper se infra não disponível
            import olx_scraper  # type: ignore

            class _PwShim:  # type: ignore[no-redef]
                def fetch(self, url):  # type: ignore[no-untyped-def]
                    html = olx_scraper.playwright_fetch(url, pw=pw_handle, browser=browser)  # type: ignore[attr-defined]
                    return html

            return _PwShim()
    # composite: curl -> PW fallback (encapsula refresh)
    try:
        from olx_deals.infrastructure.scraping.curl_fetcher import CurlFetcher
        from olx_deals.infrastructure.scraping.playwright_fetcher import PlaywrightFetcher
        from olx_deals.infrastructure.scraping.composite_fetcher import CompositeFetcher

        curl_f = CurlFetcher(cookies=cookies)
        pw_f = PlaywrightFetcher(pw=pw_handle, browser=browser)
        return CompositeFetcher(
            curl_fetcher=curl_f,
            playwright_fetcher=pw_f,
            search_url=search_url,
            cookies=cookies,
            cookies_refreshed_flag=cookies_refreshed_flag,
        )
    except Exception:
        # fallback shim que replica lógica antiga mas ainda usa Fetcher-like
        import olx_scraper  # type: ignore

        class _CompositeShim:  # type: ignore[no-redef]
            def __init__(self):  # type: ignore[no-untyped-def]
                self._ref = cookies_refreshed_flag

            def fetch(self, url):  # type: ignore[no-untyped-def]
                try:
                    status, html = olx_scraper.curl_fetch(url, cookies)  # type: ignore[attr-defined]
                    if status == 200 and html:
                        # checa se tem cards? Deixa para caller decidir fallback por cards vazio
                        return html
                except Exception:
                    pass
                if not self._ref[0]:  # type: ignore[index]
                    try:
                        ok = olx_scraper.playwright_login(search_url=search_url)  # type: ignore[attr-defined]
                        self._ref[0] = True  # type: ignore[index]
                        if ok:
                            cookies[:] = olx_scraper.load_cookies_dict()  # type: ignore[attr-defined]
                    except Exception:
                        self._ref[0] = True  # type: ignore[index]
                try:
                    return olx_scraper.playwright_fetch(url, pw=pw_handle, browser=browser)  # type: ignore[attr-defined]
                except Exception:
                    return None

        return _CompositeShim()


def _scrape_single_product(
    product,
    cookies,
    pw_handle,
    browser,
    cookies_refreshed_flag,
    log_fn,
    progress_cb,
    global_seen,
    stats=None,
    fetcher=None,
):
    """Scrape de um produto. Se fetcher for passado, usa-o; senão cria via factory (compat)."""
    # imports de parser
    try:
        from olx_deals.infrastructure.scraping.parser import extract_cards_from_html
    except Exception:
        import olx_scraper  # type: ignore

        extract_cards_from_html = olx_scraper.extract_cards_from_html  # type: ignore[attr-defined]

    queries = product.get("queries") or []
    pages = product.get("pages", 2)
    search_url = product.get("search_url", "https://www.olx.com.br/brasil?q={query}&opst=2&page={page}")
    min_price = product.get("min_price")
    max_price = product.get("max_price")
    junk_pattern = product.get("junk_pattern", "")
    junk_re = None
    if junk_pattern:
        try:
            junk_re = re.compile(junk_pattern, re.I)
        except re.error:
            try:
                junk_re = JUNK_RE  # type: ignore[assignment]
            except Exception:
                junk_re = None
    exclude_keywords = product.get("exclude_keywords") or []
    try:
        exclude_res = _compile_exclude_res(exclude_keywords)  # type: ignore[operator]
    except Exception:
        exclude_res = []
    excluded_count = 0
    include_pattern = product.get("include_pattern") or ""
    include_re = None
    if include_pattern:
        try:
            include_re = re.compile(include_pattern, re.I)
        except re.error:
            include_re = None

    product_id = product.get("id")
    product_name = product.get("name")
    results: list[dict] = []
    seen_local: set[str] = set()
    if stats is not None:
        for k in ("total_cards", "matched", "discarded_price", "discarded_junk", "discarded_exclude", "discarded_dedupe"):
            stats.setdefault(k, 0)

    # fetcher: se não veio, cria via factory (elimina if fora)
    if fetcher is None:
        fetcher = _make_fetcher(product, cookies, pw_handle, browser, cookies_refreshed_flag, search_url)

    for q in queries:
        for page in range(1, pages + 1):
            if progress_cb:
                try:
                    progress_cb(
                        {
                            "product_id": product_id,
                            "product_name": product_name,
                            "query": q,
                            "page": page,
                            "total_products": None,
                            "total_queries": len(queries),
                            "total_pages": pages,
                        }
                    )
                except Exception:
                    pass
            url = search_url.format(query=q.replace(" ", "+"), page=page)
            html = None
            used = "curl"
            # usa fetcher unificado; determina used para log heurístico
            try:
                html = fetcher.fetch(url)  # type: ignore[attr-defined]
                # heurística: se fetcher é Playwright puro, marca
                try:
                    from olx_deals.infrastructure.scraping.playwright_fetcher import PlaywrightFetcher

                    if isinstance(fetcher, PlaywrightFetcher):
                        used = "playwright"
                    elif html is None:
                        used = "playwright"
                except Exception:
                    if html is None:
                        used = "playwright"
            except Exception as e:
                try:
                    log_fn(f"fetch error [{product_name} / {q} p{page}]: {e}")
                except Exception:
                    pass
                html = None
                used = "playwright"

            cards: list[dict] = []
            if html:
                try:
                    cards = extract_cards_from_html(html)
                except Exception:
                    cards = []

            # fallback legado: se curl retornou html mas 0 cards, composite já teria feito fallback?
            # Para compat, se cards vazio e fetcher é Composite, ele já tentou PW dentro do fetch.
            # Se ainda vazio, loga vazio.

            if not cards:
                try:
                    log_fn(f"  [vazio] {product_name} | {q} p{page}")
                except Exception:
                    pass
            else:
                try:
                    log_fn(f"  {used}: {product_name} | {q} p{page} -> {len(cards)} cards")
                except Exception:
                    pass

            for c in cards:
                c["query"] = q
                c["page"] = page
                c["product_id"] = product_id
                c["product_name"] = product_name
                c["fetched_at"] = datetime.now(timezone.utc).isoformat()
                c["matched"] = False
                c["discard_reason"] = None
                if stats is not None:
                    stats["total_cards"] += 1
                if c["price"] is None:
                    if stats is not None:
                        stats["discarded_price"] += 1
                    continue
                if include_re and not include_re.search(c["title"]):
                    if stats is not None:
                        stats["discarded_junk"] += 1
                    continue
                if junk_re and junk_re.search(c["title"]):
                    if stats is not None:
                        stats["discarded_junk"] += 1
                    continue
                hit = next((kw for kw, rex in zip(exclude_keywords, exclude_res) if rex.search(c["title"])), None)
                if hit is not None:
                    excluded_count += 1
                    if stats is not None:
                        stats["discarded_exclude"] += 1
                    try:
                        log_fn(f"    [exclude] {c['title']} -> termo '{hit}'")
                    except Exception:
                        pass
                    continue
                if min_price is not None:
                    try:
                        if float(c["price"]) < float(min_price):
                            if stats is not None:
                                stats["discarded_price"] += 1
                            c["discard_reason"] = DISCARD_PRICE
                            results.append(c)
                            continue
                    except Exception:
                        pass
                if max_price is not None:
                    try:
                        if float(c["price"]) > float(max_price):
                            if stats is not None:
                                stats["discarded_price"] += 1
                            c["discard_reason"] = DISCARD_PRICE
                            results.append(c)
                            continue
                    except Exception:
                        pass
                key = f"{product_id}:{c['id']}"
                if key in global_seen or key in seen_local:
                    if stats is not None:
                        stats["discarded_dedupe"] += 1
                    continue
                seen_local.add(key)
                global_seen.add(key)
                c["matched"] = True
                c["discard_reason"] = None
                if stats is not None:
                    stats["matched"] += 1
                results.append(c)
                try:
                    log_fn(f"    >>> MATCH [{product_name}] {c['title']} | R${c['price']:.2f} | {c['url']}")
                except Exception:
                    pass
            try:
                time.sleep(random.uniform(1.5, 3.5))
            except Exception:
                pass
    if excluded_count:
        try:
            log_fn(f"[{product_name}] {excluded_count} cards descartados por exclude_keywords")
        except Exception:
            pass
    return results


def scrape(
    products_or_queries=None,
    pages=None,
    use_playwright_only=None,
    min_price=None,
    max_price=None,
    junk_pattern=None,
    search_url=None,
    log_fn=None,
    progress_cb=None,
    stats_out=None,
    fetcher=None,
    **kwargs,
):
    """Busca genérica. Assinatura compatível com olx_scraper.scrape. Aceita fetcher injetado para testes."""
    if log_fn is None:

        def _log(msg):  # type: ignore[no-redef]
            try:
                import olx_scraper  # type: ignore

                olx_scraper.log(msg)  # type: ignore[attr-defined]
            except Exception:
                print(msg)

        log_fn = _log

    # rota alternativa opt-in (backlog 2): valida 1x no início; inválida aborta
    # com erro claro (app persiste em job["error"] + run de erro, sem loop silencioso).
    route_info: dict = {"via_proxy": False, "proxy": None, "ip": None}
    try:
        from olx_deals.infrastructure.scraping.proxy import get_egress_ip, get_proxy_config, mask_proxy_url

        _proxy_cfg = get_proxy_config()
        if _proxy_cfg:
            _proxy_url = _proxy_cfg.get("https") or _proxy_cfg.get("http") or _proxy_cfg.get("all")
            route_info["via_proxy"] = True
            route_info["proxy"] = mask_proxy_url(_proxy_url)
            try:
                _egress = get_egress_ip(_proxy_cfg)
                route_info["ip"] = _egress.get("ip")
                log_fn(f"rota alternativa ativa ({route_info['proxy']}) — saída {_egress.get('ip')} colo={_egress.get('colo')} warp={_egress.get('warp')}")
            except Exception as e_diag:
                log_fn(f"rota alternativa configurada ({route_info['proxy']}) mas diagnóstico falhou: {e_diag} — seguindo para coleta")
        else:
            log_fn("rota direta (sem OLX_*_PROXY)")
    except ValueError as e_route:
        log_fn(f"rota alternativa inválida: {e_route} — corrija OLX_HTTP_PROXY/OLX_HTTPS_PROXY/OLX_ALL_PROXY ou remova para rota direta")
        raise
    except Exception:
        pass
    if stats_out is not None:
        try:
            stats_out["route"] = dict(route_info)
        except Exception:
            pass

    # imports necessários
    try:
        import olx_scraper  # type: ignore

        _load_config = olx_scraper.load_config  # type: ignore[attr-defined]
        _normalize_product = olx_scraper._normalize_product  # type: ignore[attr-defined]
        _slugify = olx_scraper._slugify  # type: ignore[attr-defined]
        _load_cookies_dict = olx_scraper.load_cookies_dict  # type: ignore[attr-defined]
        SEARCH_URL = olx_scraper.SEARCH_URL  # type: ignore[attr-defined]
        JUNK_RE_LOCAL = olx_scraper.JUNK_RE  # type: ignore[attr-defined]
    except Exception:
        # fallback hardcoded se olx_scraper não disponível (isolado)
        def _load_config():  # type: ignore[no-redef]
            return {"products": []}

        def _normalize_product(raw, seen):  # type: ignore[no-redef]
            return raw

        def _slugify(s):  # type: ignore[no-redef]
            import re as _re

            ss = _re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
            return ss or "produto"

        def _load_cookies_dict():  # type: ignore[no-redef]
            return []

        SEARCH_URL = "https://www.olx.com.br/brasil?q={query}&opst=2&page={page}"
        JUNK_RE_LOCAL = JUNK_RE

    legacy_ptb = kwargs.get("max_price_per_tb")
    if legacy_ptb is not None and max_price is None:
        try:
            max_price = float(legacy_ptb) * 12
        except Exception:
            pass

    is_legacy_queries = False
    if isinstance(products_or_queries, list) and products_or_queries:
        if isinstance(products_or_queries[0], str):
            is_legacy_queries = True
        elif isinstance(products_or_queries[0], dict) and "queries" in products_or_queries[0]:
            is_legacy_queries = False
        elif all(isinstance(x, str) for x in products_or_queries):
            is_legacy_queries = True

    if is_legacy_queries:
        queries = products_or_queries  # type: ignore[assignment]
        if pages is None:
            pages = 2
        if search_url is None:
            try:
                cfg = _load_config()
                prods = cfg.get("products") or []
                search_url = prods[0].get("search_url", SEARCH_URL) if prods else SEARCH_URL  # type: ignore[attr-defined]
            except Exception:
                search_url = SEARCH_URL
        synthetic = {
            "id": "legacy",
            "name": "legacy",
            "queries": queries,
            "pages": pages,
            "search_url": search_url,
            "playwright_only": bool(use_playwright_only) if use_playwright_only is not None else False,
            "min_price": min_price,
            "max_price": max_price,
            "junk_pattern": junk_pattern or JUNK_RE_LOCAL.pattern,  # type: ignore[attr-defined]
            "enabled": True,
        }
        products = [synthetic]
    else:
        if products_or_queries is None:
            try:
                cfg = _load_config()
                products = [p for p in cfg.get("products", []) if p.get("enabled", True)]
            except Exception:
                products = []
        else:
            products = products_or_queries  # type: ignore[assignment]
        if not isinstance(products, list):
            products = [products]

    force_enabled = bool(kwargs.get("force_enabled") or kwargs.get("force"))
    normalized: list[dict] = []
    seen_ids: set[str] = set()
    for raw in products:
        if not isinstance(raw, dict):
            continue
        if not raw.get("enabled", True) and not force_enabled:
            continue
        try:
            np = _normalize_product(raw, seen_ids)  # type: ignore[operator]
        except Exception:
            np = dict(raw)
        if raw.get("id"):
            try:
                np["id"] = _slugify(str(raw["id"]))  # type: ignore[operator]
            except Exception:
                pass
        seen_ids.add(np["id"])
        normalized.append(np)

    if not normalized:
        log_fn("Nenhum produto habilitado para busca")
        if stats_out is not None:
            stats_out.update(
                {
                    "total_cards": 0,
                    "matched": 0,
                    "discarded_price": 0,
                    "discarded_junk": 0,
                    "discarded_exclude": 0,
                    "discarded_dedupe": 0,
                    "per_product": {},
                }
            )
        return []

    # cookies carregados uma vez
    try:
        cookies = _load_cookies_dict()  # type: ignore[operator]
    except Exception:
        cookies = []
    cookies_refreshed: list[bool] = [False]

    # decide se precisa de playwright global (otimização: inicia browser compartilhado se algum produto exigir)
    any_pw_only = any(p.get("playwright_only") for p in normalized)
    pw_handle, browser = None, None
    if (any_pw_only or bool(use_playwright_only)) and fetcher is None:
        try:
            from playwright.sync_api import sync_playwright

            pw_handle = sync_playwright().start()
            browser = pw_handle.chromium.launch(headless=True)
        except Exception:
            pw_handle, browser = None, None

    try:
        all_results: list[dict] = []
        global_seen: set[str] = set()
        total_products = len(normalized)
        aggregated = {
            "total_cards": 0,
            "matched": 0,
            "discarded_price": 0,
            "discarded_junk": 0,
            "discarded_exclude": 0,
            "discarded_dedupe": 0,
            "per_product": {},
        }
        for idx, prod in enumerate(normalized):
            if progress_cb:
                try:
                    progress_cb(
                        {
                            "product_id": prod["id"],
                            "product_name": prod["name"],
                            "query": prod["queries"][0] if prod["queries"] else "",
                            "page": 1,
                            "total_products": total_products,
                            "current_product_index": idx + 1,
                        }
                    )
                except Exception:
                    pass
            per = {
                "total_cards": 0,
                "matched": 0,
                "discarded_price": 0,
                "discarded_junk": 0,
                "discarded_exclude": 0,
                "discarded_dedupe": 0,
            }
            # se fetcher injetado (teste com FakeFetcher), usa-o diretamente; senão factory por produto
            fetcher_for_product = fetcher
            # se fetcher for FakeFetcher único, reutiliza para todos os produtos
            res = _scrape_single_product(
                prod, cookies, pw_handle, browser, cookies_refreshed, log_fn, progress_cb, global_seen, stats=per, fetcher=fetcher_for_product
            )
            aggregated["total_cards"] += per["total_cards"]
            aggregated["matched"] += per["matched"]
            aggregated["discarded_price"] += per["discarded_price"]
            aggregated["discarded_junk"] += per["discarded_junk"]
            aggregated["discarded_exclude"] += per["discarded_exclude"]
            aggregated["discarded_dedupe"] += per["discarded_dedupe"]
            aggregated["per_product"][prod["id"]] = per  # type: ignore[index]
            all_results.extend(res)
        if stats_out is not None:
            stats_out.update(aggregated)
        return all_results
    finally:
        if pw_handle:
            try:
                browser.close()  # type: ignore[union-attr]
            except Exception:
                pass
            try:
                pw_handle.stop()  # type: ignore[union-attr]
            except Exception:
                pass
