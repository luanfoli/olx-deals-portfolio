"""infrastructure.scraping.parser — extraído de olx_scraper.py:1986,2007,2012. Puro."""
from __future__ import annotations

import re

PRICE_RE = re.compile(r"R\$\s*([\d.]+(?:[.,]\d+)?)")


def parse_price(text: str) -> float | None:
    m = PRICE_RE.search(text or "")
    if not m:
        return None
    raw = m.group(1).strip()
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    else:
        if raw.count(".") == 1 and len(raw.split(".")[-1]) == 3:
            raw = raw.replace(".", "")
        elif raw.count(".") > 1:
            raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


def ad_id(href: str) -> str:
    m = re.search(r"-(\d+)(?:\.html|/)?$", href or "")
    return m.group(1) if m else (href or "")


def extract_cards_from_html(html: str) -> list[dict]:
    """Parsa HTML (curl_cffi ou DOM) e devolve lista de dicts {id,title,price,url}."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    cards: list[dict] = []
    for a in soup.select('a[data-testid="adcard-link"]'):
        href = (a.get("href") or "").strip()
        title = (a.get_text(" ", strip=True) or "").strip()
        price = None
        node = a.parent
        for _ in range(6):
            if not node:
                break
            txt = node.get_text(" ", strip=True)
            p = parse_price(txt)
            if p is not None:
                price = p
                break
            node = node.parent
        if not title:
            continue
        cards.append(
            {
                "id": ad_id(href),
                "title": title,
                "price": price,
                "url": href if href.startswith("http") else "https://www.olx.com.br" + href,
            }
        )
    return cards
