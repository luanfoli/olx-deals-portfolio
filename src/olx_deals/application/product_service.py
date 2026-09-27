"""application.product_service — CRUD + normalize. Extraído de app.py:1155 + olx_scraper._normalize_product"""
from __future__ import annotations

import re
from typing import Optional

try:
    import olx_scraper  # type: ignore
except Exception:  # pragma: no cover
    olx_scraper = None  # type: ignore


def _get_cfg_repo():
    try:
        import olx_scraper as _os
        return getattr(_os, "_cfg_repo", None)
    except Exception:
        return None


def list_products():
    repo = _get_cfg_repo()
    if repo:
        cfg = repo.load()
    else:
        cfg = olx_scraper.load_config()  # type: ignore
    return cfg.get("products", [])


def get_product(pid: str):
    pid = str(pid or "").strip()
    for p in list_products():
        if p.get("id") == pid:
            return p
    return None


def _validate_payload(data: dict, is_update: bool = False) -> list[str]:
    # hardening: fonte única em validation.py, sem depender de app (quebra ciclo)
    try:
        from olx_deals.interfaces.api.validation import _validate_product_payload as _val  # type: ignore
        import copy
        d2 = copy.deepcopy(data)
        return _val(d2, is_update=is_update)
    except Exception:
        pass
    try:
        import app as _app  # type: ignore
        import copy
        d2 = copy.deepcopy(data)
        return _app._validate_product_payload(d2, is_update=is_update)  # type: ignore[attr-defined]
    except Exception:
        pass
    # fallback mínimo (se app não importável)
    errors: list[str] = []
    if not is_update and "name" not in data:
        errors.append("name obrigatório")
    if "name" in data:
        n = str(data["name"]).strip()
        if not n:
            errors.append("name não pode ser vazio")
        elif len(n) > 80:
            errors.append("name max 80 chars")
    if "queries" in data:
        q = data["queries"]
        if isinstance(q, str):
            q = [q]
        if not isinstance(q, list) or not q or not all(isinstance(x, str) and x.strip() for x in q):
            errors.append("queries deve ser lista não vazia de strings")
    if "pages" in data:
        try:
            p = int(data["pages"])
            if not (1 <= p <= 10):
                errors.append("pages deve ser 1-10")
        except Exception:
            errors.append("pages deve ser inteiro 1-10")
    return errors


def create_product(data: dict):
    """Cria produto; retorna (product, config) ou levanta ValueError(409) / ValueError(400)."""
    errors = _validate_payload(data, is_update=False)
    if errors:
        raise ValueError(f"validation: {errors}")
    repo = _get_cfg_repo()
    cfg = repo.load() if repo else olx_scraper.load_config()  # type: ignore
    products = cfg.get("products", [])
    # verifica duplicado se id fornecido
    if "id" in data:
        pid = olx_scraper._slugify(str(data["id"]))  # type: ignore
        if any(p["id"] == pid for p in products):
            raise LookupError(f"id '{pid}' já existe")
    # normaliza
    new_prod = olx_scraper._normalize_product(data, set(p["id"] for p in products))  # type: ignore
    products.append(new_prod)
    saved = repo.save({"products": products}) if repo else olx_scraper.save_config({"products": products})  # type: ignore
    # reschedule delega para schedule_service (hardening: sem fallback app)
    try:
        from olx_deals.application.schedule_service import reschedule_all
        reschedule_all()
    except Exception:
        pass
    created = next((p for p in saved["products"] if p["id"] == new_prod["id"]), new_prod)
    return created, saved


def update_product(pid: str, data: dict):
    errors = _validate_payload(data, is_update=True)
    if errors:
        raise ValueError(f"validation: {errors}")
    repo = _get_cfg_repo()
    cfg = repo.load() if repo else olx_scraper.load_config()  # type: ignore
    products = cfg.get("products", [])
    idx = next((i for i, p in enumerate(products) if p["id"] == pid), None)
    if idx is None:
        return None, None
    # verifica colisão se id mudou
    if "id" in data and olx_scraper._slugify(str(data["id"])) != pid:  # type: ignore
        new_id = olx_scraper._slugify(str(data["id"]))  # type: ignore
        if any(p["id"] == new_id for p in products):
            raise LookupError(f"id '{new_id}' já existe")
    base = dict(products[idx])
    base.update(data)
    if "id" not in data:
        base["id"] = pid
    other_ids = set(p["id"] for i, p in enumerate(products) if i != idx)
    normalized = olx_scraper._normalize_product(base, other_ids)  # type: ignore
    if "id" in data:
        normalized["id"] = olx_scraper._slugify(str(data["id"]))  # type: ignore
    else:
        normalized["id"] = pid
    products[idx] = normalized
    saved = repo.save({"products": products}) if repo else olx_scraper.save_config({"products": products})  # type: ignore
    try:
        from olx_deals.application.schedule_service import reschedule_all
        reschedule_all()
    except Exception:
        pass
    updated = next((p for p in saved["products"] if p["id"] == normalized["id"]), normalized)
    return updated, saved


def delete_product(pid: str):
    repo = _get_cfg_repo()
    cfg = repo.load() if repo else olx_scraper.load_config()  # type: ignore
    products = cfg.get("products", [])
    new_products = [p for p in products if p["id"] != pid]
    if len(new_products) == len(products):
        return None
    if not new_products:
        raise ValueError("não pode remover o último produto; desative-o em vez disso")
    saved = repo.save({"products": new_products}) if repo else olx_scraper.save_config({"products": new_products})  # type: ignore
    try:
        from olx_deals.application.schedule_service import reschedule_all
        reschedule_all()
    except Exception:
        pass
    return saved
