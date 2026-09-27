"""interfaces.api.tuner — Blueprint tuner (Fase 5)."""
from __future__ import annotations
from flask import Blueprint, request, jsonify
bp = Blueprint("tuner", __name__)

@bp.route("/api/tuner/requests", methods=["POST"])
def create():
    try:
        import app as _app
        return _app.api_tuner_create()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/tuner/requests", methods=["GET"])
def list_requests():
    try:
        import app as _app
        return _app.api_tuner_list()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/tuner/requests/<id>", methods=["GET"])
def get_one(id):
    try:
        import app as _app
        return _app.api_tuner_get(id)  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/tuner/requests/<id>/action", methods=["POST"])
def action(id):
    try:
        import app as _app
        return _app.api_tuner_action(id)  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/tuner/requests/<id>/refine", methods=["POST"])
def refine(id):
    try:
        import app as _app
        return _app.api_tuner_refine(id)  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/tuner/models", methods=["GET"])
def models():
    try:
        import app as _app
        return _app.api_tuner_models()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/config/llm", methods=["GET"])
def get_llm():
    try:
        import app as _app
        return _app.api_get_llm()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/config/llm", methods=["PUT"])
def put_llm():
    try:
        import app as _app
        return _app.api_put_llm()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
