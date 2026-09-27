"""interfaces.api.security — hardening: sanitização de segredos e validação LLM.

Extraído de app.py:793-871 para ser fonte única (DRY + hardening).
App re-exporta para compat com testes legados.
"""
from __future__ import annotations

import hashlib
import re

_MASK_PREFIX = "***"


def _mask_secret(value):
    """Mascara segredo preservando os 4 últimos chars para identificação."""
    if not isinstance(value, str) or len(value) <= 8:
        return _MASK_PREFIX
    return _MASK_PREFIX + value[-4:]


def _looks_masked(value):
    return isinstance(value, str) and value.startswith(_MASK_PREFIX)


def _sanitize_config(cfg):
    """Cópia da config com telegram_token e tuner_llm.api_key mascarados — nunca devolve o valor real."""
    out = dict(cfg or {})
    if out.get("telegram_token"):
        out["telegram_token"] = _mask_secret(out["telegram_token"])
    llm = out.get("tuner_llm")
    if isinstance(llm, dict) and llm.get("api_key"):
        llm = dict(llm)
        llm["api_key"] = _mask_secret(str(llm["api_key"]))
        out["tuner_llm"] = llm
    return out


def _sanitize_llm_config(llm_cfg):
    """Sanitiza só o bloco tuner_llm para resposta."""
    if not isinstance(llm_cfg, dict):
        return llm_cfg
    out = dict(llm_cfg)
    if out.get("api_key"):
        out["api_key"] = _mask_secret(str(out["api_key"]))
    return out


def _hash_api_key_for_cache(api_key: str) -> str:
    """Hash curto (8 hex) de api_key para cache_key sem expor prefixo."""
    if not api_key:
        return ""
    try:
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8]
    except Exception:
        return ""


def _validate_tuner_llm_payload(raw: dict) -> list:
    """Valida payload tuner_llm parcial (provider/base_url/model/api_key). Retorna lista de erros."""
    errors: list[str] = []
    if not isinstance(raw, dict):
        return ["tuner_llm deve ser objeto"]
    prov = str(raw.get("provider") or "").strip().lower() if raw.get("provider") is not None else None
    if prov is not None and prov not in ("openrouter", "opencode", "mock"):
        if prov in ("openai", "openai-compatible"):
            pass
        else:
            errors.append("tuner_llm.provider deve ser openrouter|opencode|mock")
    if "base_url" in raw or "baseUrl" in raw:
        bu = raw.get("base_url") or raw.get("baseUrl")
        if bu is not None and str(bu).strip() != "":
            bus = str(bu).strip()
            if not (bus.startswith("http://") or bus.startswith("https://")):
                errors.append("tuner_llm.base_url deve começar com http:// ou https://")
    if "model" in raw and raw["model"] not in (None, ""):
        ms = str(raw["model"]).strip()
        if not (1 <= len(ms) <= 100):
            errors.append("tuner_llm.model deve ter 1..100 chars")
    if "api_key" in raw and raw["api_key"] not in (None, ""):
        if _looks_masked(raw["api_key"]):
            pass
        elif not isinstance(raw["api_key"], str):
            errors.append("tuner_llm.api_key deve ser string")
        elif len(str(raw["api_key"]).strip()) < 4 and str(raw["api_key"]).strip() != "":
            errors.append("tuner_llm.api_key muito curta")
    return errors
