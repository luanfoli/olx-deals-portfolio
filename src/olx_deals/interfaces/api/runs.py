"""interfaces.api.runs — Blueprint runs Tier 1 (Fase 5)."""
from __future__ import annotations
from flask import Blueprint, request, jsonify
bp = Blueprint("runs", __name__)

@bp.route("/api/runs", methods=["GET"])
def list_runs():
    try:
        import app as _app
        return _app.api_list_runs()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/runs/<id>", methods=["GET"])
def get_run(id):
    try:
        import app as _app
        return _app.api_get_run(id)  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/runs/<id>", methods=["DELETE"])
def del_run(id):
    try:
        import app as _app
        return _app.api_delete_run_by_id(id)  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/runs", methods=["DELETE"])
def clear_runs():
    try:
        import app as _app
        return _app.api_clear_runs()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
