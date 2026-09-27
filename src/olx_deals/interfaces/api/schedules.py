"""interfaces.api.schedules — Blueprint schedules (Fase 5)."""
from __future__ import annotations
from flask import Blueprint, request, jsonify
bp = Blueprint("schedules", __name__)

@bp.route("/api/schedules/status", methods=["GET"])
def status():
    try:
        import app as _app
        return _app.api_schedules_status()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/schedules/trigger/<pid>", methods=["POST"])
def trigger(pid):
    try:
        import app as _app
        return _app.api_schedule_trigger(pid)  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/schedules/queue", methods=["GET", "DELETE"])
def queue():
    try:
        import app as _app
        return _app.api_schedules_queue()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/schedules/run", methods=["POST"])
def run_now():
    try:
        import app as _app
        return _app.api_schedules_run_now()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
