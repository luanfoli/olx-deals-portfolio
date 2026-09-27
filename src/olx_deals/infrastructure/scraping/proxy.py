"""infrastructure.scraping.proxy — rota alternativa opt-in (backlog 2 / issue #3).

Comportamento padrão: desabilitado (retorna None) → fetchers usam rede direta,
idêntico ao atual, sem regressão.

Quando habilitado via env, todas as coletas saem pela rota alternativa:
  OLX_HTTP_PROXY / OLX_HTTPS_PROXY / OLX_ALL_PROXY
  (aceita minúsculas; ex.: http://127.0.0.1:8888 ou socks5://127.0.0.1:1080)

WARP: o Cloudflare WARP em modo proxy (ex.: warp-proxy, sing-box, ou
`warp-cli --accept-tos mode proxy`) expõe um endpoint local desses — basta
apontar as vars acima. WARP em modo TUN global (default route) não precisa
deste módulo, mas perde o toggle por coleta e o diagnóstico.

Diagnóstico: get_egress_ip() retorna ip/colo/warp via cloudflare trace.
Falha da rota: ValueError claro (config inválida) ou RequestsError do
curl_cffi — nunca loop silencioso.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

VALID_SCHEMES = ("http", "https", "socks5", "socks5h")

ENV_MAP = (
    ("http", ("OLX_HTTP_PROXY", "olx_http_proxy")),
    ("https", ("OLX_HTTPS_PROXY", "olx_https_proxy")),
    ("all", ("OLX_ALL_PROXY", "olx_all_proxy")),
)


def _validate_proxy_url(url: str) -> str:
    """Valida URL de proxy; retorna normalizada ou levanta ValueError claro."""
    if not url or not isinstance(url, str):
        raise ValueError(
            "rota alternativa inválida (vazia): defina OLX_HTTP_PROXY/OLX_HTTPS_PROXY "
            "como http(s)://host:porta ou socks5(h)://host:porta, ou desdefina para rota direta"
        )
    url = url.strip()
    try:
        p = urlparse(url)
    except Exception:
        raise ValueError(f"rota alternativa inválida ({url!r}): URL ilegível")
    if p.scheme not in VALID_SCHEMES or not p.hostname:
        raise ValueError(
            f"rota alternativa inválida ({url!r}): use http(s)://host:porta ou "
            "socks5(h)://host:porta com host válido"
        )
    return url


def get_proxy_config(explicit=None, env: dict | None = None) -> dict | None:
    """Resolve config de proxy. Retorna None (rota direta) ou dict curl_cffi.

    explicit: None | str | dict — tem precedência sobre env.
      str → vale para http+https; dict aceita chaves http/https/all.
    env: mapping opcional (default os.environ) — útil em testes.
    """
    if explicit is not None:
        if isinstance(explicit, str):
            url = _validate_proxy_url(explicit)
            return {"http": url, "https": url}
        if isinstance(explicit, dict):
            norm = {}
            for k in ("http", "https", "all"):
                if explicit.get(k):
                    norm[k] = _validate_proxy_url(explicit[k])
            if not norm:
                return None
            if "all" in norm:
                norm.setdefault("http", norm["all"])
                norm.setdefault("https", norm["all"])
            return norm
        raise ValueError(
            f"rota alternativa inválida ({explicit!r}): use str ou dict http/https/all"
        )
    e = os.environ if env is None else env
    found: dict = {}
    for key, names in ENV_MAP:
        for n in names:
            v = e.get(n)
            if v and str(v).strip():
                found[key] = _validate_proxy_url(str(v))
                break
    if not found:
        return None
    if "all" in found:
        found.setdefault("http", found["all"])
        found.setdefault("https", found["all"])
    return found


def mask_proxy_url(url: str | None) -> str | None:
    """Mascara senha em URL de proxy para logs (user:***@host:porta)."""
    if not url:
        return None
    try:
        from urllib.parse import urlparse

        p = urlparse(url)
        if p.password:
            host = p.hostname or ""
            if p.port:
                host += f":{p.port}"
            auth = f"{p.username}:***@" if p.username else "***@"
            return f"{p.scheme}://{auth}{host}"
        return url
    except Exception:
        return "***"


def build_playwright_proxy(cfg: dict | None) -> dict | None:
    """Converte config curl_cffi → proxy do Playwright (server + auth)."""
    if not cfg:
        return None
    url = cfg.get("https") or cfg.get("http") or cfg.get("all")
    if not url:
        return None
    p = urlparse(url)
    server = f"{p.scheme}://{p.hostname}" + (f":{p.port}" if p.port else "")
    out: dict = {"server": server}
    if p.username:
        out["username"] = p.username
    if p.password:
        out["password"] = p.password
    return out


def get_egress_ip(proxies: dict | str | None = None, timeout: int = 15) -> dict:
    """Diagnóstico: qual IP de saída (direto ou via rota). Levanta erro claro."""
    from curl_cffi import requests as cf

    cfg = get_proxy_config(proxies)
    try:
        r = cf.get(
            "https://cloudflare.com/cdn-cgi/trace",
            timeout=timeout,
            impersonate="chrome124",
            proxies=cfg or None,
        )
    except Exception as e:
        via = "rota alternativa" if cfg else "rota direta"
        raise RuntimeError(f"diagnóstico via {via} falhou: {e}") from e
    if r.status_code != 200:
        raise RuntimeError(f"diagnóstico falhou: trace HTTP {r.status_code}")
    data = {}
    for line in r.text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()
    return {
        "ip": data.get("ip"),
        "colo": data.get("colo"),
        "loc": data.get("loc"),
        "warp": data.get("warp"),
        "via_proxy": bool(cfg),
        "raw": r.text,
    }
