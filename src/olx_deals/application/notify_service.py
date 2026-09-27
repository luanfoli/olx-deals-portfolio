"""application.notify_service — dedupe Telegram + threshold.
Extraído de olx_scraper.notify + save_history lógica.
"""
from __future__ import annotations

from typing import List, Dict


def _get_notified_repo():
    try:
        import olx_scraper as _os
        return getattr(_os, "_notified_repo", None)
    except Exception:
        return None


def notify(deals: List[Dict]) -> Dict:
    """Delega para olx_scraper.notify mantendo compat de contadores."""
    try:
        import olx_scraper  # type: ignore
        return olx_scraper.notify(deals)  # type: ignore[attr-defined]
    except Exception as e:
        return {"enviados": 0, "pulados": 0, "re_notificados": 0, "erro": str(e)}


def load_notified() -> Dict:
    try:
        import olx_scraper  # type: ignore
        repo = _get_notified_repo()
        if repo:
            return repo.load()
        return olx_scraper.load_notified()  # type: ignore[attr-defined]
    except Exception:
        return {}


def remove_notified(keys: List[str]) -> int:
    try:
        import olx_scraper  # type: ignore
        repo = _get_notified_repo()
        if repo:
            return repo.remove(keys)
        return olx_scraper.remove_notified(keys)  # type: ignore[attr-defined]
    except Exception:
        return 0


def clear_notified() -> int:
    try:
        import olx_scraper  # type: ignore
        repo = _get_notified_repo()
        if repo:
            # repo clear não existe; usa olx_scraper
            return olx_scraper.clear_notified()  # type: ignore[attr-defined]
        return olx_scraper.clear_notified()  # type: ignore[attr-defined]
    except Exception:
        return 0
