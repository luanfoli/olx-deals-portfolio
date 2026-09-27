"""interfaces.api — create_app + blueprints (Fase 5).

Mantém compat: `from app import app` continua funcionando; `create_app()` é factory para testes
e para gerar openapi.yaml a partir de schemas Pydantic.

Estrangulamento: app.py importa create_app e blueprints; se import falhar, app.py mantém rotas legadas.
"""
from __future__ import annotations

import os
import hmac
from flask import Flask, request, jsonify

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root
# src/olx_deals/interfaces/api/__init__.py -> repo root = 4 levels up

def _api_token_gate(API_TOKEN):
    from flask import request, jsonify
    def gate():
        if not API_TOKEN:
            return None
        if not request.path.startswith("/api/"):
            return None
        if request.path == "/api/health" and request.method in ("GET", "HEAD"):
            return None
        provided = request.headers.get("X-API-Token", "")
        if not provided or not hmac.compare_digest(provided.encode("utf-8"), API_TOKEN.encode("utf-8")):
            return jsonify({"ok": False, "error": "X-API-Token ausente ou inválido"}), 401
        return None
    return gate

def create_app(test_config: dict | None = None):
    # importa Flask com pastas corretas
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    # fallback: usa repo root detectado via BASE_DIR
    try:
        from app import BASE_DIR as APP_BASE
        base_dir = APP_BASE
    except Exception:
        pass
    app = Flask(__name__, static_folder=os.path.join(base_dir, "static"), template_folder=os.path.join(base_dir, "templates"))

    API_TOKEN = (test_config or {}).get("API_TOKEN", os.environ.get("OLX_API_TOKEN"))
    if test_config and "API_TOKEN" in test_config:
        API_TOKEN = test_config["API_TOKEN"]
    else:
        API_TOKEN = os.environ.get("OLX_API_TOKEN") if test_config is None else API_TOKEN

    @app.before_request
    def _gate():
        if not API_TOKEN:
            return None
        if not request.path.startswith("/api/"):
            return None
        if request.path == "/api/health" and request.method in ("GET", "HEAD"):
            return None
        provided = request.headers.get("X-API-Token", "")
        if not provided or not hmac.compare_digest(provided.encode("utf-8"), API_TOKEN.encode("utf-8")):
            return jsonify({"ok": False, "error": "X-API-Token ausente ou inválido"}), 401
        return None

    # registra blueprints se disponíveis; falha silenciosa mantém compat legacy
    try:
        from olx_deals.interfaces.api.products import bp as products_bp
        app.register_blueprint(products_bp)
    except Exception as e:
        print(f"[api] products blueprint skip: {e}")
    try:
        from olx_deals.interfaces.api.deals import bp as deals_bp
        app.register_blueprint(deals_bp)
    except Exception as e:
        print(f"[api] deals blueprint skip: {e}")
    try:
        from olx_deals.interfaces.api.scrape import bp as scrape_bp
        app.register_blueprint(scrape_bp)
    except Exception as e:
        print(f"[api] scrape blueprint skip: {e}")
    try:
        from olx_deals.interfaces.api.schedules import bp as schedules_bp
        app.register_blueprint(schedules_bp)
    except Exception as e:
        print(f"[api] schedules blueprint skip: {e}")
    try:
        from olx_deals.interfaces.api.runs import bp as runs_bp
        app.register_blueprint(runs_bp)
    except Exception as e:
        print(f"[api] runs blueprint skip: {e}")
    try:
        from olx_deals.interfaces.api.tuner import bp as tuner_bp
        app.register_blueprint(tuner_bp)
    except Exception as e:
        print(f"[api] tuner blueprint skip: {e}")
    try:
        from olx_deals.interfaces.api.config import bp as config_bp
        app.register_blueprint(config_bp)
    except Exception as e:
        # config blueprint opcional
        pass
    # registra openapi/docs/swagger legacy se não vieram dos blueprints
    try:
        if not any(r.rule == "/api/health" for r in app.url_map.iter_rules()):
            @app.route("/api/health")
            def _health():
                from datetime import datetime, timezone
                return jsonify({"ok": True, "time": datetime.now(timezone.utc).isoformat()})
        if not any(r.rule == "/api/openapi.yaml" for r in app.url_map.iter_rules()):
            @app.route("/api/openapi.yaml")
            def _openapi():
                from flask import send_from_directory
                return send_from_directory(base_dir, "openapi.yaml", mimetype="application/yaml")
        if not any(r.rule == "/docs" for r in app.url_map.iter_rules()):
            @app.route("/docs")
            def _docs():
                from flask import send_from_directory
                html = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>olx-deals API — Swagger UI</title><link rel="stylesheet" href="/static/swagger-ui/swagger-ui.css"></head><body><div id="swagger"></div><script src="/static/swagger-ui/swagger-ui-bundle.js"></script><script>SwaggerUIBundle({url:"/api/openapi.yaml",dom_id:"#swagger"});</script></body></html>"""
                return html, 200, {"Content-Type": "text/html; charset=utf-8"}
        if not any(r.rule == "/" for r in app.url_map.iter_rules()):
            @app.route("/")
            def _index():
                from flask import send_from_directory
                tmpl = os.path.join(app.template_folder, "index.html")
                if os.path.exists(tmpl):
                    return send_from_directory(app.template_folder, "index.html")
                return "index.html não encontrado", 404
        if not any(r.rule.startswith("/static/") for r in app.url_map.iter_rules()):
            @app.route("/static/<path:filename>")
            def _static(filename):
                from flask import send_from_directory
                return send_from_directory(app.static_folder, filename)
    except Exception:
        pass
    return app
