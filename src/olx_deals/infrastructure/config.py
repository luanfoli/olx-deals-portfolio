"""infrastructure.config — env + defaults, get_tuner_llm_config wrapper (Fase 5).

Mantém compat com olx_scraper.get_tuner_llm_config; expõe via PORT para application.
"""
from __future__ import annotations

import os
from typing import Dict, Any

try:
    import olx_scraper  # type: ignore
except Exception:  # pragma: no cover
    olx_scraper = None  # type: ignore


def get_tuner_llm_config(cfg: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Delega para olx_scraper.get_tuner_llm_config mantendo compat env > config.json."""
    try:
        if olx_scraper is not None:
            return olx_scraper.get_tuner_llm_config(cfg)  # type: ignore
    except Exception:
        pass
    # fallback manual
    defaults = {"provider": "openrouter", "base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini", "api_key": None}
    if cfg and isinstance(cfg.get("tuner_llm"), dict):
        base = dict(cfg["tuner_llm"])
        for k in defaults:
            base.setdefault(k, defaults[k])
        # env override
        for env_key, cfg_key in [("OLX_TUNER_LLM_PROVIDER", "provider"), ("OLX_TUNER_LLM_BASE_URL", "base_url"), ("OLX_TUNER_LLM_MODEL", "model"), ("OLX_TUNER_LLM_API_KEY", "api_key")]:
            if os.environ.get(env_key):
                base[cfg_key] = os.environ.get(env_key)
        return base
    return defaults


def get_notify_price_change_pct(cfg: Dict[str, Any] | None = None) -> float:
    try:
        if olx_scraper is not None:
            return olx_scraper.get_notify_price_change_pct(cfg)  # type: ignore
    except Exception:
        pass
    if cfg is None:
        return 5.0
    try:
        v = float(cfg.get("notify_price_change_pct", 5.0))  # type: ignore
        if 0 <= v <= 100:
            return v
    except Exception:
        pass
    return 5.0
