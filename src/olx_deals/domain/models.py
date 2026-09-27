"""domain.models — entidades puras (sem Flask/os/json/playwright)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_JUNK_PATTERN = (
    r"\b(dock|station|suporte|at[eé]|docking|encosto|cabo|clip|gabinete|case|c[aá]mera)\b"
    r"|recupera|reparo|conserto|quebrado|defeito|p/?a/?r/?a?\s?pecas|pecas"
)
_SEARCH_URL = "https://www.olx.com.br/brasil?q={query}&opst=2&page={page}"


@dataclass
class Schedule:
    enabled: bool = False
    mode: str = "interval"
    interval_minutes: int = 360
    jitter_minutes: int = 0
    cron: str = "0 */6 * * *"
    timezone: str = "America/Sao_Paulo"


@dataclass
class Product:
    id: str
    name: str
    queries: List[str]
    pages: int = 2
    search_url: str = _SEARCH_URL
    playwright_only: bool = False
    min_price: Optional[float] = None
    max_price: Optional[float] = 900.0
    junk_pattern: str = _JUNK_PATTERN
    exclude_keywords: List[str] = field(default_factory=list)
    include_pattern: str = ""
    enabled: bool = True
    draft: bool = False
    schedule: Schedule = field(default_factory=Schedule)


@dataclass
class Deal:
    id: str
    title: str
    price: Optional[float] = None
    url: str = ""
    query: str = ""
    page: int = 1
    product_id: str = ""
    product_name: str = ""
    fetched_at: str = ""
    matched: bool = True
    discard_reason: Optional[str] = None


@dataclass
class Run:
    id: str
    started_at: str
    finished_at: Optional[str] = None
    product_ids: List[str] = field(default_factory=list)
    status: str = "ok"
    duration_ms: Optional[int] = None
    matched_count: int = 0
    results_count: int = 0
    error: Optional[str] = None


@dataclass
class TunerRequest:
    id: str
    definicao: str
    descricao: str
    sementes: List[str]
    status: str = "pendente"
    draft_product_id: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    obrigatorios: Optional[str] = None
    excludentes: List[str] = field(default_factory=list)
    faixa: Optional[Dict[str, Any]] = None
