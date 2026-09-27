"""infrastructure.scraping.fetcher — Port Fetcher (Strategy). Puro, sem Flask/os."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Fetcher(Protocol):
    """Port: fetcher unificado. fetch(url) -> html str | None (None = falha/bloqueio)."""

    def fetch(self, url: str) -> str | None: ...


class FakeFetcher:
    """Fetcher de teste. Substitui catalog dict tests/test_e2e.py:72.

    Uso: FakeFetcher(html="<html>...</html>") ou FakeFetcher(html_map={url: html})
    """

    def __init__(self, html: str = "", html_map: dict | None = None, status_map: dict | None = None):
        self.html = html
        self.html_map = dict(html_map or {})
        self.status_map = dict(status_map or {})
        self.calls: list[str] = []

    def fetch(self, url: str) -> str | None:
        self.calls.append(url)
        if url in self.html_map:
            return self.html_map[url]
        if url in self.status_map:
            # status_map permite simular falha (None) por URL
            return self.status_map[url]
        return self.html
