"""interfaces.api.schemas — Pydantic v2 fonte única da openapi.yaml (Fase 5).
Substitui _validate_product_payload app.py:570 + _validate_tuner_llm_payload.
Gera openapi.yaml automaticamente via model_json_schema.
"""
from __future__ import annotations

import re
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict

# --- Schedule ---
class ScheduleSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    enabled: bool = False
    mode: str = Field(default="interval", pattern="^(interval|cron)$")
    interval_minutes: int = Field(default=360, ge=15, le=10080)
    cron: str = Field(default="0 */6 * * *")
    jitter_minutes: int = Field(default=0, ge=0, le=60)
    timezone: str = Field(default="America/Sao_Paulo", pattern="^America/Sao_Paulo$")

    @field_validator("cron")
    @classmethod
    def _cron_must_be_5_fields(cls, v: str) -> str:
        v = str(v or "").strip()
        if not v:
            return "0 */6 * * *"
        parts = v.split()
        if len(parts) != 5:
            raise ValueError("schedule.cron deve ter 5 campos (ex: '0 */6 * * *')")
        # tenta validar com CronTrigger quando disponível, mas não falha se sem lib
        try:
            from apscheduler.triggers.cron import CronTrigger  # type: ignore
            CronTrigger.from_crontab(v, timezone="America/Sao_Paulo")
        except ImportError:
            pass
        except Exception as e:
            raise ValueError(f"schedule.cron inválido: {e}")
        return v


# --- Product ---
class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    id: Optional[str] = Field(default=None, max_length=60, pattern=r"^[a-z0-9\-]+$")
    name: str = Field(min_length=1, max_length=80)
    queries: List[str] = Field(min_length=1, max_length=50)
    pages: int = Field(default=2, ge=1, le=10)
    search_url: Optional[str] = None
    playwright_only: bool = False
    min_price: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    max_price: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    junk_pattern: Optional[str] = Field(default=None, max_length=2000)
    exclude_keywords: Optional[List[str]] = Field(default=None, max_length=50)
    include_pattern: Optional[str] = Field(default=None, max_length=500)
    enabled: bool = True
    draft: bool = False
    schedule: Optional[ScheduleSchema] = None

    @field_validator("queries", mode="before")
    @classmethod
    def _coerce_queries(cls, v):
        if isinstance(v, str):
            return [v]
        return v

    @field_validator("queries")
    @classmethod
    def _validate_queries(cls, v: List[str]):
        if not v or not all(isinstance(x, str) and x.strip() for x in v):
            raise ValueError("queries deve ser lista não vazia de strings")
        cleaned = [s.strip() for s in v if s.strip()]
        if len(cleaned) > 50:
            raise ValueError("queries: máximo 50 termos")
        if any(len(s) > 80 for s in cleaned):
            raise ValueError("queries: cada termo max 80 chars")
        return cleaned

    @field_validator("search_url")
    @classmethod
    def _validate_search_url(cls, v):
        if v is None or v == "":
            return v
        s = str(v).strip()
        if "{query}" not in s or "{page}" not in s:
            raise ValueError("search_url deve conter {query} e {page}")
        if not s.startswith("https://"):
            raise ValueError("search_url deve começar com https://")
        return s

    @field_validator("junk_pattern", "include_pattern", mode="before")
    @classmethod
    def _validate_regex(cls, v):
        if v is None or v == "":
            return v
        s = str(v)
        if len(s) > 2000 and "junk" in str(v):
            raise ValueError("junk_pattern muito longo")
        try:
            re.compile(s, re.I)
        except re.error as e:
            raise ValueError(f"regex inválido: {e}")
        return s

    @field_validator("exclude_keywords", mode="before")
    @classmethod
    def _coerce_exclude(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return v

    @field_validator("exclude_keywords")
    @classmethod
    def _validate_exclude(cls, v):
        if v is None:
            return []
        cleaned = [str(s).strip() for s in v if str(s).strip()]
        if len(cleaned) > 50:
            raise ValueError("exclude_keywords: máximo 50 termos")
        if any(len(s) > 40 for s in cleaned):
            raise ValueError("exclude_keywords: cada termo max 40 chars")
        return cleaned

    @field_validator("min_price", "max_price", mode="before")
    @classmethod
    def _coerce_price(cls, v):
        if v in ("", None):
            return None
        try:
            fv = float(v)
        except Exception:
            raise ValueError("deve ser número")
        if not (0 < fv <= 1_000_000):
            raise ValueError("fora de faixa 0-1000000")
        return fv

    @field_validator("id", mode="before")
    @classmethod
    def _slugify_id(cls, v):
        if v is None:
            return v
        s = str(v).strip().lower()
        if not s:
            raise ValueError("id não pode ser vazio")
        if len(s) > 60:
            raise ValueError("id max 60 chars")
        if not re.match(r"^[a-z0-9\-]+$", s):
            raise ValueError("id deve conter só a-z0-9 e hífen")
        return s

    @model_validator(mode="after")
    def _check_min_lt_max(self):
        if self.min_price is not None and self.max_price is not None:
            if self.min_price >= self.max_price:
                raise ValueError("min_price deve ser < max_price")
        return self

    @field_validator("schedule", mode="before")
    @classmethod
    def _coerce_schedule(cls, v):
        if v is None:
            return None
        if not isinstance(v, dict):
            raise ValueError("schedule deve ser objeto")
        # valida jitter/interval via ScheduleSchema
        return v


class ProductUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    id: Optional[str] = Field(default=None, max_length=60, pattern=r"^[a-z0-9\-]+$")
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    queries: Optional[List[str]] = Field(default=None, min_length=1, max_length=50)
    pages: Optional[int] = Field(default=None, ge=1, le=10)
    search_url: Optional[str] = None
    playwright_only: Optional[bool] = None
    min_price: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    max_price: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    junk_pattern: Optional[str] = Field(default=None, max_length=2000)
    exclude_keywords: Optional[List[str]] = Field(default=None, max_length=50)
    include_pattern: Optional[str] = Field(default=None, max_length=500)
    enabled: Optional[bool] = None
    draft: Optional[bool] = None
    schedule: Optional[ScheduleSchema] = None

    @field_validator("queries", mode="before")
    @classmethod
    def _coerce_q(cls, v):
        if v is None:
            return v
        if isinstance(v, str):
            return [v]
        return v

    @field_validator("queries")
    @classmethod
    def _validate_q(cls, v):
        if v is None:
            return v
        cleaned = [str(s).strip() for s in v if str(s).strip()]
        if not cleaned:
            raise ValueError("queries deve ser lista não vazia")
        if len(cleaned) > 50:
            raise ValueError("queries: máximo 50 termos")
        if any(len(s) > 80 for s in cleaned):
            raise ValueError("queries: cada termo max 80 chars")
        return cleaned

    @field_validator("search_url")
    @classmethod
    def _validate_su(cls, v):
        if v is None:
            return v
        s = str(v).strip()
        if "{query}" not in s or "{page}" not in s:
            raise ValueError("search_url deve conter {query} e {page}")
        if not s.startswith("https://"):
            raise ValueError("search_url deve começar com https://")
        return s

    @field_validator("junk_pattern", "include_pattern", mode="before")
    @classmethod
    def _validate_re(cls, v):
        if v is None:
            return v
        s = str(v)
        try:
            re.compile(s, re.I)
        except re.error as e:
            raise ValueError(f"regex inválido: {e}")
        return s

    @field_validator("exclude_keywords", mode="before")
    @classmethod
    def _coerce_ex(cls, v):
        if v is None:
            return v
        if isinstance(v, str):
            return [v]
        return v

    @field_validator("exclude_keywords")
    @classmethod
    def _validate_ex(cls, v):
        if v is None:
            return v
        cleaned = [str(s).strip() for s in v if str(s).strip()]
        if len(cleaned) > 50:
            raise ValueError("exclude_keywords: máximo 50 termos")
        if any(len(s) > 40 for s in cleaned):
            raise ValueError("exclude_keywords: cada termo max 40 chars")
        return cleaned

    @field_validator("min_price", "max_price", mode="before")
    @classmethod
    def _coerce_p(cls, v):
        if v is None or v == "":
            return None
        try:
            fv = float(v)
        except Exception:
            raise ValueError("deve ser número")
        if not (0 < fv <= 1_000_000):
            raise ValueError("fora de faixa 0-1000000")
        return fv

    @field_validator("id", mode="before")
    @classmethod
    def _slug_id(cls, v):
        if v is None:
            return v
        s = str(v).strip().lower()
        if not s:
            raise ValueError("id não pode ser vazio")
        if len(s) > 60:
            raise ValueError("id max 60 chars")
        if not re.match(r"^[a-z0-9\-]+$", s):
            raise ValueError("id deve conter só a-z0-9 e hífen")
        return s


# --- Tuner ---
class TunerCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    descricao: str = Field(min_length=1, max_length=200)
    definicao: Optional[str] = Field(default=None, max_length=200)
    sementes: List[str] = Field(min_length=2, max_length=3)  # 2..3 termos
    obrigatorios: Optional[str | List[str]] = None
    excludentes: Optional[str | List[str]] = None
    faixa: Optional[Dict[str, Any]] = None
    # aliases flexíveis
    definicao_uma_frase: Optional[str] = None
    termos_obrigatorios: Optional[str | List[str]] = None
    termos_a_excluir: Optional[str | List[str]] = None
    preco_min: Optional[float] = None
    preco_max: Optional[float] = None

    @field_validator("descricao", mode="before")
    @classmethod
    def _coerce_desc(cls, v):
        if isinstance(v, str):
            return v.strip()
        # aceita description alias
        return v

    @field_validator("sementes", mode="before")
    @classmethod
    def _coerce_sem(cls, v):
        if isinstance(v, str):
            parts = [p.strip() for p in re.split(r"[,;\n]+", v) if p.strip()]
            return parts
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return v

    @field_validator("sementes")
    @classmethod
    def _valid_sem(cls, v):
        if v is None or len(v) < 2:
            raise ValueError("sementes: informe ao menos 2 termos (2..3)")
        if len(v) > 3:
            raise ValueError("sementes: máximo 3 termos")
        if any(len(t) < 2 or len(t) > 80 for t in v):
            raise ValueError("sementes: cada termo deve ter 2..80 chars")
        return v


class TunerLLMConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    provider: str = Field(default="openrouter", pattern="^(openrouter|opencode|mock)$")
    base_url: Optional[str] = None
    model: Optional[str] = Field(default="openai/gpt-4o-mini", min_length=1, max_length=100)
    api_key: Optional[str] = None

    @field_validator("provider", mode="before")
    @classmethod
    def _norm_provider(cls, v):
        if v is None:
            return "openrouter"
        s = str(v).strip().lower()
        if s in ("openai", "openai-compatible"):
            return "openrouter"
        if s not in ("openrouter", "opencode", "mock"):
            raise ValueError("provider deve ser openrouter|opencode|mock")
        return s

    @field_validator("base_url", mode="before")
    @classmethod
    def _valid_bu(cls, v):
        if v is None or str(v).strip() == "":
            return v
        s = str(v).strip()
        if not (s.startswith("http://") or s.startswith("https://")):
            raise ValueError("base_url deve começar com http:// ou https://")
        return s.rstrip("/")

    @field_validator("api_key", mode="before")
    @classmethod
    def _valid_key(cls, v):
        if v is None or str(v).strip() == "":
            return None
        s = str(v).strip()
        if len(s) < 4:
            raise ValueError("api_key muito curta")
        return s


def _errors_from_pydantic(exc) -> list[str]:
    """Converte ValidationError para lista de strings compat com app._validate_*."""
    errs: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "")
        typ = err.get("type", "")
        # mapeia mensagens para compat com testes existentes (que esperam pt-BR)
        # mantém original mas prefixa loc quando existir
        if loc:
            errs.append(f"{loc}: {msg} ({typ})")
        else:
            errs.append(f"{msg} ({typ})")
    return errs


def validate_product_create(data: dict) -> list[str]:
    try:
        ProductCreate.model_validate(data)
        # valida min<max já está no model_validator; se passou, verifica price invertido manualmente para mensagem pt-BR
        # Pydantic já levanta
        return []
    except Exception as e:
        try:
            from pydantic import ValidationError
            if isinstance(e, ValidationError):
                # mapeia para mensagens próximas do app.py para não quebrar testes que checam substring
                # tenta converter para lista de erros pt-BR quando possível
                errs = _errors_from_pydantic(e)
                # normaliza mensagens comuns
                normalized: list[str] = []
                for msg in errs:
                    # mapeia "Field required" -> "name obrigatório" etc.
                    if "name" in msg and "Field required" in msg:
                        normalized.append("name obrigatório")
                    elif "queries" in msg and "Field required" in msg:
                        normalized.append("queries deve ser lista não vazia de strings")
                    elif "queries" in msg and "maxItems" in msg:
                        normalized.append("queries: máximo 50 termos")
                    elif "pages" in msg and "less than or equal" in msg:
                        normalized.append("pages deve ser 1-10")
                    else:
                        normalized.append(msg)
                return normalized
        except Exception:
            pass
        return [str(e)]


def validate_product_update(data: dict) -> list[str]:
    try:
        ProductUpdate.model_validate(data)
        return []
    except Exception as e:
        try:
            from pydantic import ValidationError
            if isinstance(e, ValidationError):
                return _errors_from_pydantic(e)
        except Exception:
            pass
        return [str(e)]


def generate_openapi_from_schemas() -> dict:
    """Gera fragmento openapi components.schemas a partir dos Pydantic models (para openapi.yaml auto)."""
    return {
        "ProductCreate": ProductCreate.model_json_schema(),
        "ProductUpdate": ProductUpdate.model_json_schema(),
        "Schedule": ScheduleSchema.model_json_schema(),
        "TunerCreate": TunerCreate.model_json_schema(),
        "TunerLLMConfig": TunerLLMConfig.model_json_schema(),
    }
