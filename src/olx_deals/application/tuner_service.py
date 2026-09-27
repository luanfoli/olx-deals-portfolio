"""application.tuner_service — _run_tuner + helpers.
Extraído de app.py:1927. Delegação fina para app._run_tuner mantendo compat.
"""
from __future__ import annotations

import threading
from collections import deque

tuner_lock = threading.RLock()
tuner_queue: deque = deque()


def _run_tuner(tuner_id: str):
    """Worker tuner — hardening: fonte única delega para validação, mas execução ainda via app se disponível."""
    # hardening: tenta usar validação local, mas mantém compat com app quando existir (sem ciclo)
    # Se app existir, delega; senão, fallback minimal (será substituído quando app enxuto re-exportar este módulo)
    try:
        from olx_deals.interfaces.api.validation import _validate_tuner_payload  # noqa: F401
    except Exception:
        pass
    try:
        import app as _app  # type: ignore
        if hasattr(_app, "_run_tuner") and _app._run_tuner is not _run_tuner:  # type: ignore
            return _app._run_tuner(tuner_id)  # type: ignore
    except Exception:
        pass
    try:
        print(f"tuner_service fallback: {tuner_id} sem app._run_tuner disponível")
    except Exception:
        pass


def validate_tuner_payload(data: dict) -> list[str]:
    try:
        from olx_deals.interfaces.api.validation import _validate_tuner_payload as _v  # type: ignore
        return _v(data)
    except Exception:
        pass
    try:
        import app as _app  # type: ignore
        return _app._validate_tuner_payload(data)  # type: ignore[attr-defined]
    except Exception:
        pass
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["payload deve ser objeto"]
    desc = str(data.get("descricao") or data.get("description") or "").strip()
    if not desc:
        errors.append("descricao obrigatória")
    sementes = data.get("sementes") or data.get("termos_busca") or data.get("queries")
    if not sementes:
        errors.append("sementes obrigatório")
    return errors


def normalize_briefing(data: dict) -> dict:
    try:
        from olx_deals.interfaces.api.validation import _tuner_normalize_briefing as _n  # type: ignore
        return _n(data)
    except Exception:
        pass
    try:
        import app as _app  # type: ignore
        return _app._tuner_normalize_briefing(data)  # type: ignore[attr-defined]
    except Exception:
        return dict(data)


def create_draft_product(briefing: dict, existing_ids: set) -> dict:
    try:
        from olx_deals.interfaces.api.validation import _tuner_create_draft_product as _c  # type: ignore
        return _c(briefing, existing_ids)
    except Exception:
        pass
    try:
        import app as _app  # type: ignore
        return _app._tuner_create_draft_product(briefing, existing_ids)  # type: ignore[attr-defined]
    except Exception:
        import olx_scraper  # type: ignore
        return {"id": "draft-fallback", "name": briefing.get("descricao", "draft")[:80], "queries": briefing.get("sementes", ["olx"])[:3], "pages": 2, "search_url": olx_scraper.SEARCH_URL, "enabled": False, "draft": True}  # type: ignore
