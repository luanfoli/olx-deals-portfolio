"""interfaces.api.products — Blueprint CRUD produtos (Fase 5 hardening).

Hardening: fonte única de validação é validation.py, sanitização em security.py.
Não importa `app` (quebra ciclo). Delega para application.product_service.
"""
from __future__ import annotations

from flask import Blueprint, request, jsonify

bp = Blueprint("products", __name__)


@bp.route("/api/products", methods=["GET"])
def list_products():
    try:
        from olx_deals.application.product_service import list_products as _list

        products = _list()
    except Exception:
        import olx_scraper

        cfg = olx_scraper.load_config()
        products = cfg.get("products", [])
    return jsonify({"products": products})


@bp.route("/api/products", methods=["POST"])
def create_product():
    data = request.get_json(force=True) or {}
    # validação: tenta Pydantic primeiro, mas usa legacy para mensagens pt-BR
    try:
        from olx_deals.interfaces.api.schemas import validate_product_create

        errs = validate_product_create(data)
        if errs:
            from olx_deals.interfaces.api.validation import _validate_product_payload

            legacy_errs = _validate_product_payload(dict(data), is_update=False)
            if legacy_errs:
                return jsonify({"ok": False, "errors": legacy_errs}), 400
            return jsonify({"ok": False, "errors": errs}), 400
    except Exception:
        pass
    try:
        from olx_deals.interfaces.api.validation import _validate_product_payload

        errors = _validate_product_payload(dict(data), is_update=False)
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
    except Exception as e:
        return jsonify({"ok": False, "errors": [str(e)]}), 400

    try:
        from olx_deals.application.product_service import create_product as _create

        created, saved = _create(data)
    except LookupError as e:
        return jsonify({"ok": False, "errors": [str(e)]}), 409
    except ValueError as ve:
        msg = str(ve)
        return jsonify({"ok": False, "errors": [msg]}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    try:
        from olx_deals.interfaces.api.security import _sanitize_config

        sanitized = _sanitize_config(saved)
    except Exception:
        sanitized = saved
    return jsonify({"ok": True, "product": created, "config": sanitized}), 201


@bp.route("/api/products/<pid>", methods=["GET"])
def get_product(pid):
    try:
        from olx_deals.application.product_service import get_product as _get

        prod = _get(pid)
    except Exception:
        import olx_scraper

        cfg = olx_scraper.load_config()
        prod = next((p for p in cfg.get("products", []) if p["id"] == pid), None)
    if not prod:
        return jsonify({"ok": False, "error": "produto não encontrado"}), 404
    return jsonify({"ok": True, "product": prod})


@bp.route("/api/products/<pid>", methods=["PUT"])
def update_product(pid):
    data = request.get_json(force=True) or {}
    if "id" in data:
        try:
            import olx_scraper

            new_id = olx_scraper._slugify(str(data["id"]))
            if new_id != pid:
                import olx_scraper as _os

                cfg_tmp = _os.load_config()
                if any(p["id"] == new_id for p in cfg_tmp.get("products", [])):
                    return jsonify({"ok": False, "errors": [f"id '{new_id}' já existe"]}), 409
        except Exception:
            pass
    try:
        from olx_deals.interfaces.api.validation import _validate_product_payload

        errors = _validate_product_payload(dict(data), is_update=True)
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
    except Exception as e:
        return jsonify({"ok": False, "errors": [str(e)]}), 400

    try:
        from olx_deals.application.product_service import update_product as _upd

        updated, saved = _upd(pid, data)
    except LookupError as e:
        return jsonify({"ok": False, "errors": [str(e)]}), 409
    except ValueError as ve:
        return jsonify({"ok": False, "errors": [str(ve)]}), 400
    if updated is None:
        return jsonify({"ok": False, "error": "produto não encontrado"}), 404
    try:
        from olx_deals.interfaces.api.security import _sanitize_config

        sanitized = _sanitize_config(saved)
    except Exception:
        sanitized = saved
    return jsonify({"ok": True, "product": updated, "config": sanitized})


@bp.route("/api/products/<pid>", methods=["DELETE"])
def delete_product(pid):
    try:
        from olx_deals.application.product_service import delete_product as _del

        saved = _del(pid)
    except ValueError as ve:
        return jsonify({"ok": False, "error": str(ve)}), 400
    except LookupError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    if saved is None:
        return jsonify({"ok": False, "error": "produto não encontrado"}), 404
    try:
        from olx_deals.interfaces.api.security import _sanitize_config

        sanitized = _sanitize_config(saved)
    except Exception:
        sanitized = saved
    return jsonify({"ok": True, "config": sanitized})
