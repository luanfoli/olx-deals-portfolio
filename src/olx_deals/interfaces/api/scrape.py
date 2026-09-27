"""interfaces.api.scrape — Blueprint scrape/login/cookies (Fase 5)."""
from __future__ import annotations
from flask import Blueprint, request, jsonify
bp = Blueprint("scrape", __name__)

@bp.route("/api/scrape", methods=["POST"])
def trigger_scrape():
    try:
        import app as _app
        return _app.api_scrape()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/scrape/status", methods=["GET"])
def scrape_status():
    try:
        import app as _app
        return _app.api_scrape_status()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/scrape/logs", methods=["GET"])
def scrape_logs():
    try:
        import app as _app
        return _app.api_scrape_logs()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/login", methods=["POST"])
def login():
    try:
        import app as _app
        return _app.api_login()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/cookies/status", methods=["GET"])
def cookies_status():
    try:
        import app as _app
        return _app.api_cookies_status()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/config/telegram/test", methods=["POST"])
def telegram_test():
    try:
        import app as _app
        return _app.api_telegram_test()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
