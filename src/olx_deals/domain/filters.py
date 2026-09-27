"""domain.filters — puro, sem Flask/os/json/playwright. Extraído de olx_scraper.py:71,1840,1860"""
from __future__ import annotations

import re

PRICE_RE = re.compile(r"R\$\s*([\d.]+(?:[.,]\d+)?)")
JUNK_RE = re.compile(
    r"\b(dock|station|suporte|at[eé]|docking|encosto|cabo|clip|gabinete|case|c[aá]mera)\b"
    r"|recupera|reparo|conserto|quebrado|defeito|p/?a/?r/?a?\s?pecas|pecas",
    re.I,
)

DISCARD_PRICE = "price"
DISCARD_INCLUDE = "include"
DISCARD_JUNK = "junk"
DISCARD_EXCLUDE = "exclude"
DISCARD_DEDUPE = "dedupe"
DISCARD_ORPHAN = "orphan"


def _compile_exclude_res(keywords):
    """Compila regex híbrida por termo: (?<![a-z]) + literal escapado, case-insensitive.

    Lookbehind ASCII bloqueia letra imediatamente anterior ("RGB" não casa "GB"),
    mas dígito/espaço/início de título casam ("HD 500GB" casa "GB" — sem boundary
    entre "0" e "G"). re.escape garante termo literal.
    """
    res = []
    for kw in keywords or []:
        try:
            res.append(re.compile(r"(?<![a-z])" + re.escape(str(kw)), re.I))
        except re.error:
            continue
    return res


def _is_orphan(product_id: str, active_ids: set) -> bool:
    return str(product_id or "").strip() not in active_ids


def _evaluate_current(deal: dict, product_cfg: dict | None, active_ids: set) -> tuple:
    """Reavalia deal isolado com filtros vigentes. Não toca disco.

    Retorna (matched_current: bool, discard_reason_current: str|None).
    Ordem: orphan -> include -> junk -> exclude -> price -> match.
    price None => (False, "price").
    """
    pid = str(deal.get("product_id") or "").strip()
    if _is_orphan(pid, active_ids):
        return False, DISCARD_ORPHAN
    if product_cfg is None:
        return False, DISCARD_ORPHAN
    title = str(deal.get("title") or "")
    price = deal.get("price")
    if price is None:
        return False, DISCARD_PRICE
    try:
        pf = float(price)
    except Exception:
        return False, DISCARD_PRICE
    inc = str(product_cfg.get("include_pattern") or "").strip()
    if inc:
        try:
            if not re.compile(inc, re.I).search(title):
                return False, DISCARD_INCLUDE
        except re.error:
            pass
    jp = str(product_cfg.get("junk_pattern") or "").strip()
    if jp:
        try:
            if re.compile(jp, re.I).search(title):
                return False, DISCARD_JUNK
        except re.error:
            pass
    eks = product_cfg.get("exclude_keywords") or []
    ex_res = _compile_exclude_res(eks)
    hit = next((kw for kw, rx in zip(eks, ex_res) if rx.search(title)), None)
    if hit is not None:
        return False, DISCARD_EXCLUDE
    min_p = product_cfg.get("min_price")
    max_p = product_cfg.get("max_price")
    if min_p is not None:
        try:
            if pf < float(min_p):
                return False, DISCARD_PRICE
        except Exception:
            pass
    if max_p is not None:
        try:
            if pf > float(max_p):
                return False, DISCARD_PRICE
        except Exception:
            pass
    return True, None


def _evaluate_current_detailed(deal: dict, product_cfg: dict | None, active_ids: set) -> tuple:
    """Variante que detalha exclude:<term> para by_reason."""
    m, r = _evaluate_current(deal, product_cfg, active_ids)
    if r == DISCARD_EXCLUDE and product_cfg is not None:
        eks = product_cfg.get("exclude_keywords") or []
        ex_res = _compile_exclude_res(eks)
        hit = next((kw for kw, rx in zip(eks, ex_res) if rx.search(str(deal.get("title") or ""))), None)
        if hit:
            r = f"exclude:{str(hit).lower()}"
    return m, r
