"""interfaces.api.config — Blueprint config (Fase 5)."""
from __future__ import annotations
from flask import Blueprint, request, jsonify
bp = Blueprint("config", __name__, url_prefix="/api/config")

@bp.route("", methods=["GET"])
def get_config():
    try:
        import app as _app
        return _app.api_get_config()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("", methods=["PUT"])
def put_config():
    try:
        import app as _app
        return _app.api_put_config()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
