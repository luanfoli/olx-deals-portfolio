#!/usr/bin/env python3
"""
app.py — GUI web para olx-deals (genérico, multi-produto)

- Gerenciar produtos (queries + filtros de preço + junk)
- Forçar buscas (com log live + progresso por produto)
- Exibir resultados

Roda em http://127.0.0.1:8765 por padrão.
Expõe API REST sob /api/* e serve SPA em /.
"""

import argparse
import hashlib
import hmac
import json
import os
import re
import threading
import time
import traceback
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone

from flask import Flask, request, jsonify, send_from_directory

import olx_scraper

# Fase 3: repos — usa instâncias do olx_scraper (_cfg_repo etc) para injeção única via monkeypatch
def _get_cfg_repo(): return getattr(olx_scraper, "_cfg_repo", None)
def _get_deals_repo(): return getattr(olx_scraper, "_deals_repo", None)
def _get_runs_repo(): return getattr(olx_scraper, "_runs_repo", None)
def _get_sched_repo(): return getattr(olx_scraper, "_schedules_repo", None)

# Fase 6: re-exports removidos — application/* e schemas são fonte única via src/olx_deals
# app.py mantém apenas orquestração HTTP legada até migração total para interfaces/api.create_app()

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    HAS_SCHEDULER = True
except ImportError:
    BackgroundScheduler = None  # type: ignore
    HAS_SCHEDULER = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"), template_folder=os.path.join(BASE_DIR, "templates"))

# --- auth opt-in por token (PLANO-OPENAPI-MCP Fase 1) ---
# Lido uma vez no startup: mudar o token exige restart (aceitável).
# Sem OLX_API_TOKEN no ambiente a API segue aberta — GUI/cron/testes não mudam.
API_TOKEN = os.environ.get("OLX_API_TOKEN")


@app.before_request
def _api_token_gate():
    """Com OLX_API_TOKEN setado, /api/* exige header X-API-Token igual (exceto GET /api/health)."""
    if not API_TOKEN:
        return None
    if not request.path.startswith("/api/"):
        return None  # SPA (/), /static/* ficam fora do gate
    if request.path == "/api/health" and request.method in ("GET", "HEAD"):
        return None  # liveness para proxy/container
    provided = request.headers.get("X-API-Token", "")
    if not provided or not hmac.compare_digest(provided.encode("utf-8"), API_TOKEN.encode("utf-8")):
        return jsonify({"ok": False, "error": "X-API-Token ausente ou inválido"}), 401
    return None

# --- job state (thread-safe) — hardening: fonte única em job_runner/schedule_service ---
from olx_deals.application.job_runner import (
    job,
    job_lock,
    schedule_queue,
    scheduler,
    HAS_SCHEDULER,
    MAX_LOG_LINES,
    _append_log,
    _reset_job,
    _queue_params,
    _drain_queue,
    _get_scheduler,
    _parse_iso,
)
from olx_deals.application.schedule_service import (
    _get_next_run_for_interval,
    trigger_product,
    reschedule_all,
)

# re-exporta para compat com tests que importam app.job etc. (mesmo objeto)
# (job/job_lock/schedule_queue são os mesmos objetos de job_runner)

def _run_scrape(params):
    # Tier 1 7a: registra 1 run por disparo em runs.json (auditável)
    started = datetime.now(timezone.utc)
    run_id = f"{started.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    requested = None
    source = "manual"
    try:
        if isinstance(params, dict):
            if params.get("product_ids"):
                v = params.get("product_ids")
                if isinstance(v, str):
                    v = [v]
                requested = [str(x).strip() for x in v if str(x).strip()]
            elif params.get("product_id"):
                requested = [str(params.get("product_id")).strip()]
            source = str(params.get("source") or "manual").strip() or "manual"
            if source not in ("manual", "schedule", "trigger", "api", "queue"):
                # mantém valor mas normaliza para manual se desconhecido? Preserva original para auditoria
                source = source
        else:
            requested = None
    except Exception:
        requested = None

    _products_for_persist = []
    _error_for_persist = None
    # stats agregados para o run
    try:
        cfg = (_get_cfg_repo().load() if _get_cfg_repo() else olx_scraper.load_config())
        all_products = cfg.get("products", [])
        # filtra por product_ids se pedido
        product_ids = requested
        if product_ids:
            products = [p for p in all_products if p["id"] in product_ids]
            if not products:
                raise ValueError(f"Nenhum produto encontrado para ids: {', '.join(product_ids)}")
        else:
            # só habilitados por padrão, a menos que params force incluir desabilitados
            products = [p for p in all_products if p.get("enabled", True)]
            if not products:
                raise ValueError("Nenhum produto habilitado para busca")
        _products_for_persist = list(products)

        # draft support: quando product_ids explícito, permite scrape de drafts (enabled:false) sem persistir mudança
        # clona para não mutar config original; scrape() filtra enabled por padrão
        if product_ids:
            products = [dict(p, enabled=True) for p in products]

        # permite override de playwright via params (força para todos)
        force_pw = params.get("playwright_only") if isinstance(params, dict) else None
        if force_pw:
            for p in products:
                p["playwright_only"] = True

        def log_fn(m):
            _append_log(m)

        def progress_cb(info):
            with job_lock:
                job["progress"].update(info)
                # mantém compat com frontend antigo que espera current_query/page
                if "product_id" in info:
                    job["progress"]["current_product"] = info.get("product_id")
                    job["progress"]["current_product_name"] = info.get("product_name")
                if "query" in info:
                    job["progress"]["current_query"] = info.get("query")
                if "page" in info:
                    job["progress"]["current_page"] = info.get("page")
                if "total_products" in info and info["total_products"]:
                    job["progress"]["total_products"] = info["total_products"]

        log_fn(f"Iniciando busca genérica: {len(products)} produto(s)")
        for p in products:
            log_fn(f" - {p['name']} ({p['id']}): {len(p['queries'])} queries, {p['pages']} pág/query, preço {p.get('min_price') or '-'} .. {p.get('max_price') or '∞'}")
            log_fn(f"   Queries: {', '.join(p['queries'][:5])}{' ...' if len(p['queries'])>5 else ''}")

        stats_out = {}
        results = olx_scraper.scrape(
            products,
            log_fn=log_fn,
            progress_cb=progress_cb,
            stats_out=stats_out,
            force_enabled=bool(product_ids),
        )
        matched = [r for r in results if r.get("matched")]
        log_fn(f"=== {len(matched)} anúncios atendem filtro, {len(results)} com preço persistidos ===")
        new = (_get_deals_repo().save(results) if _get_deals_repo() else olx_scraper.save_history(results))
        log_fn(f"=== {len(new)} novos salvos em {olx_scraper.HISTORY_FILE} ===")
        cnt = {"enviados": 0, "pulados": 0, "re_notificados": 0}
        new_matched = [r for r in new if r.get("matched")]
        # não notifica drafts (tuning) — evita spam durante refinamento iterativo (flag draft + legacy prefix)
        try:
            _cfg_draft_ids = {p.get("id") for p in (_get_cfg_repo().load() if _get_cfg_repo() else olx_scraper.load_config()).get("products", []) if p.get("draft")}
        except Exception:
            _cfg_draft_ids = set()
        new_matched = [r for r in new_matched if r.get("product_id") not in _cfg_draft_ids and not str(r.get("product_id", "")).startswith("draft-")]
        if new_matched:
            try:
                cnt = olx_scraper.notify(new_matched)
                msg = (f"Telegram: {cnt.get('enviados', 0)} enviados, "
                       f"{cnt.get('pulados', 0)} já notificados (ignorados), "
                       f"{cnt.get('re_notificados', 0)} re-notificados (mudança de preço)")
                if cnt.get("erro"):
                    msg += f" | erro: {cnt['erro']}"
                log_fn(msg)
            except Exception as e:
                log_fn(f"notify error: {e}")
                cnt = {"enviados": 0, "pulados": 0, "re_notificados": 0, "erro": str(e)}
        else:
            cnt = {"enviados": 0, "pulados": 0, "re_notificados": 0}
        with job_lock:
            job["results_count"] = len(results)
            job["matched_count"] = len(matched)
            job["progress"]["done"] = True

        # monta registro Tier 1
        finished = datetime.now(timezone.utc)
        duration_ms = int((finished - started).total_seconds() * 1000)
        # product_ids_requested: se requested é None, usa ids efetivamente scrapeados
        product_ids_requested = requested if requested is not None else [p["id"] for p in products]
        product_ids_scraped = [p["id"] for p in products]
        counts = {
            "total_cards": int(stats_out.get("total_cards", 0)),
            "matched": int(stats_out.get("matched", len(matched))),
            "discarded_price": int(stats_out.get("discarded_price", 0)),
            "discarded_junk": int(stats_out.get("discarded_junk", 0)),
            "discarded_exclude": int(stats_out.get("discarded_exclude", 0)),
            "discarded_dedupe": int(stats_out.get("discarded_dedupe", 0)),
            "notified_enviados": int(cnt.get("enviados", 0)),
            "notified_pulados": int(cnt.get("pulados", 0)),
            "notified_re_notificados": int(cnt.get("re_notificados", 0)),
        }
        per_product = stats_out.get("per_product", {}) if isinstance(stats_out.get("per_product"), dict) else {}
        # garante chaves inteiras
        for pid, per in list(per_product.items()):
            if isinstance(per, dict):
                for k in ("total_cards", "matched", "discarded_price", "discarded_junk", "discarded_exclude", "discarded_dedupe"):
                    try:
                        per[k] = int(per.get(k, 0))
                    except Exception:
                        per[k] = 0
        entry = {
            "id": run_id,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "duration_ms": duration_ms,
            "source": source,
            "status": "ok",
            "error": None,
            "product_ids_requested": product_ids_requested,
            "product_ids_scraped": product_ids_scraped,
            "counts": counts,
            "per_product": per_product,
        }
        try:
            (_get_runs_repo().append(entry) if _get_runs_repo() else olx_scraper.append_run(entry))
        except Exception as pe:
            _append_log(f"runs: falha ao persistir run ok {run_id}: {pe}")

        # dual-write para schedules.json (mantém compat Fase 2)
        try:
            at_iso = finished.isoformat()
            for prod in products:
                pid = prod["id"]
                per = per_product.get(pid, {}) if isinstance(per_product.get(pid), dict) else {}
                (_get_sched_repo().record(pid, status="ok", at_iso=at_iso, matched_count=int(per.get("matched", 0)), results_count=int(per.get("total_cards", 0))) if _get_sched_repo() else olx_scraper.record_schedule_run(pid, status="ok", at_iso=at_iso, matched_count=int(per.get("matched", 0)), results_count=int(per.get("total_cards", 0))))
        except Exception as pe:
            _append_log(f"schedule: falha ao persistir last_run ok: {pe}")
    except Exception as e:
        tb = traceback.format_exc()
        _append_log(f"ERRO: {e}")
        _append_log(tb[-2000:])
        with job_lock:
            job["error"] = str(e)
        _error_for_persist = str(e)
        # persiste run de erro Tier 1
        try:
            finished = datetime.now(timezone.utc)
            duration_ms = int((finished - started).total_seconds() * 1000)
            error_msg = str(e)[:500]
            # requested pode ser None; product_ids_requested = requested or []
            product_ids_requested = requested if requested is not None else []
            # scraped tenta usar _products_for_persist se disponível
            if _products_for_persist:
                product_ids_scraped = [p["id"] for p in _products_for_persist]
                # se requested era None e tivemos produtos, usa scraped como fallback para requested vazio
                if not product_ids_requested:
                    product_ids_requested = list(product_ids_scraped)
            else:
                product_ids_scraped = list(product_ids_requested) if product_ids_requested else []
            counts_err = {"total_cards": 0, "matched": 0, "discarded_price": 0, "discarded_junk": 0, "discarded_exclude": 0, "discarded_dedupe": 0, "notified_enviados": 0, "notified_pulados": 0, "notified_re_notificados": 0}
            entry_err = {
                "id": run_id,
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
                "duration_ms": duration_ms,
                "source": source,
                "status": "error",
                "error": error_msg,
                "product_ids_requested": product_ids_requested,
                "product_ids_scraped": product_ids_scraped,
                "counts": counts_err,
                "per_product": {},
            }
            try:
                (_get_runs_repo().append(entry_err) if _get_runs_repo() else olx_scraper.append_run(entry_err))
            except Exception as pe2:
                _append_log(f"runs: falha ao persistir run erro {run_id}: {pe2}")
        except Exception as pe:
            _append_log(f"runs: falha ao montar run erro: {pe}")

        # persistência erro por produto (Fase 2 compat)
        try:
            at_iso = datetime.now(timezone.utc).isoformat()
            pids = []
            if _products_for_persist:
                pids = [p["id"] for p in _products_for_persist]
            elif isinstance(params, dict) and params.get("product_ids"):
                pids = [str(x) for x in params["product_ids"]]
            elif requested:
                pids = list(requested)
            for pid in pids:
                (_get_sched_repo().record(pid, status="error", error=_error_for_persist, at_iso=at_iso) if _get_sched_repo() else olx_scraper.record_schedule_run(pid, status="error", error=_error_for_persist, at_iso=at_iso))
        except Exception as pe:
            _append_log(f"schedule: falha ao persistir last_run erro: {pe}")
    finally:
        with job_lock:
            job["running"] = False
            job["finished_at"] = datetime.now(timezone.utc).isoformat()
        # drena fila robusta (Fase 2: suporta dict params)
        _drain_queue()


# --- helpers ---
def _load_deals():
    _dr=_get_deals_repo()
    if _dr:
        try: return _dr.load()
        except: pass
    path = olx_scraper.HISTORY_FILE
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        for d in data:
            if not d.get("product_id"):
                d["product_id"] = "hd-6-12tb"
                d["product_name"] = d.get("product_name") or "HD 6-12TB"
            if "matched" not in d:
                d["matched"] = True
            if "discard_reason" not in d:
                d["discard_reason"] = None if d.get("matched") else "price"
        return data
    except Exception:
        return []


def _cookies_status():
    p = olx_scraper.COOKIE_JAR
    if not os.path.exists(p):
        return {"exists": False, "path": p}
    try:
        st = os.stat(p)
        age_s = time.time() - st.st_mtime
        with open(p) as f:
            ss = json.load(f)
        cookies = ss.get("cookies", []) if isinstance(ss, dict) else []
        cf_bm = None
        for c in cookies:
            if c.get("name") == "__cf_bm":
                cf_bm = c
                break
        return {
            "exists": True,
            "path": p,
            "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            "age_minutes": round(age_s / 60, 1),
            "cookies_count": len(cookies),
            "cf_bm_expires": cf_bm.get("expires") if cf_bm else None,
            "size_bytes": st.st_size,
        }
    except Exception as e:
        return {"exists": True, "path": p, "error": str(e)}


# --- validação de produto — hardening: fonte única em validation.py ---
from olx_deals.interfaces.api.validation import _validate_product_payload


# --- API ---
@app.route("/api/health")
def api_health():
    return jsonify({"ok": True, "time": datetime.now(timezone.utc).isoformat()})


# --- sanitização de segredos (BACKLOG #6) — hardening: fonte única em security.py ---
from olx_deals.interfaces.api.security import (
    _MASK_PREFIX,
    _mask_secret,
    _looks_masked,
    _sanitize_config,
    _sanitize_llm_config,
    _hash_api_key_for_cache,
    _validate_tuner_llm_payload,
)


@app.route("/api/config", methods=["GET"])
def api_get_config():
    cfg = olx_scraper.load_config()
    return jsonify(_sanitize_config(cfg))


@app.route("/api/config", methods=["PUT"])
def api_put_config():
    data = request.get_json(force=True) or {}
    errors = []

    # valor mascarado vindo de um GET significa "manter o armazenado"
    if _looks_masked(data.get("telegram_token")):
        stored = olx_scraper.load_config().get("telegram_token")
        if stored:
            data["telegram_token"] = stored

    # suporta tanto config global (telegram + products) quanto legado flat
    # valida telegram
    for k in ("telegram_token", "telegram_chat_id"):
        if k in data and data[k] not in (None, ""):
            if k == "telegram_token" and not isinstance(data[k], str):
                errors.append("telegram_token deve ser string")
            if k == "telegram_chat_id":
                if isinstance(data[k], str) and data[k].strip().isdigit():
                    try:
                        data[k] = int(data[k].strip())
                    except Exception:
                        pass
        elif k in data and data[k] == "":
            data[k] = None

    # threshold de re-notificação por mudança de preço (%)
    if "notify_price_change_pct" in data and data["notify_price_change_pct"] not in (None, ""):
        try:
            f = float(data["notify_price_change_pct"])
            if not (0 <= f <= 100):
                errors.append("notify_price_change_pct fora de faixa 0-100")
            else:
                data["notify_price_change_pct"] = f
        except Exception:
            errors.append("notify_price_change_pct deve ser número")

    # tuner_llm (usa helper compartilhado — DRY com /api/config/llm)
    if "tuner_llm" in data:
        raw = data["tuner_llm"]
        if raw is None:
            data["tuner_llm"] = None
        elif not isinstance(raw, dict):
            errors.append("tuner_llm deve ser objeto")
        else:
            # api_key mascarado -> mantém stored
            if _looks_masked(raw.get("api_key")):
                stored_llm = olx_scraper.load_config().get("tuner_llm") or {}
                if stored_llm.get("api_key"):
                    raw = dict(raw)
                    raw["api_key"] = stored_llm["api_key"]
                    data["tuner_llm"] = raw
            # alias legado openai -> openrouter
            prov_tmp = str(raw.get("provider") or "").strip().lower() if raw.get("provider") is not None else None
            if prov_tmp in ("openai", "openai-compatible"):
                raw["provider"] = "openrouter"
            errs = _validate_tuner_llm_payload(raw)
            if errs:
                errors.extend(errs)
            # normaliza via helper se sem erros
            if not errors:
                try:
                    # preserva api_key mascarada já resolvida
                    data["tuner_llm"] = olx_scraper._normalize_tuner_llm(raw)
                    # se api_key veio mascarada e foi substituída, garante que normalized mantém real
                    if _looks_masked(raw.get("api_key") if isinstance(raw, dict) else None):
                        # _normalize já teria perdido, restaura
                        stored_llm = olx_scraper.load_config().get("tuner_llm") or {}
                        if stored_llm.get("api_key"):
                            data["tuner_llm"]["api_key"] = stored_llm["api_key"]
                except Exception as e:
                    errors.append(f"tuner_llm inválido: {e}")

    # se veio products, valida cada
    if "products" in data:
        if not isinstance(data["products"], list):
            errors.append("products deve ser lista")
        else:
            for idx, prod in enumerate(data["products"]):
                if not isinstance(prod, dict):
                    errors.append(f"products[{idx}] deve ser objeto")
                    continue
                e = _validate_product_payload(prod, is_update=False)
                if e:
                    errors.extend([f"products[{idx}]: {msg}" for msg in e])
    else:
        # compat legada: se vieram campos de produto flat, valida como produto
        flat_keys = {"queries", "pages", "search_url", "junk_pattern", "playwright_only", "min_price", "max_price", "min_tb", "max_tb", "max_price_per_tb"}
        if flat_keys & set(data.keys()):
            # valida como produto sintético
            tmp = {k: data[k] for k in flat_keys if k in data}
            # mapeia max_price_per_tb para max_price se necessário (só para validar)
            if "max_price_per_tb" in tmp and "max_price" not in tmp:
                try:
                    tmp["max_price"] = float(tmp["max_price_per_tb"]) * 12
                except Exception:
                    pass
            e = _validate_product_payload(tmp, is_update=True)
            errors.extend(e)

    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    saved = olx_scraper.save_config(data)
    try:
        reschedule_all()
    except Exception as e:
        _append_log(f"schedule: reschedule falhou após PUT config: {e}")
    return jsonify({"ok": True, "config": _sanitize_config(saved)})


# --- Tuner LLM config (critério 8) ---
@app.route("/api/config/llm", methods=["GET"])
def api_get_llm():
    cfg = olx_scraper.load_config()
    llm = cfg.get("tuner_llm") or olx_scraper.DEFAULT_LLM_CONFIG
    # env override já está em load? Actually load_config não aplica env para tuner_llm (só get_tuner_llm_config faz). Para UI mostrar efetivo, usa get_tuner_llm_config.
    effective = olx_scraper.get_tuner_llm_config(cfg)
    # mascarar efetivo para resposta (mantém tipo)
    sanitized = _sanitize_llm_config(effective)
    # também informa se vem de env (não editável via config.json sem restart)
    env_keys = []
    for k, env in [("provider", "OLX_TUNER_LLM_PROVIDER"), ("base_url", "OLX_TUNER_LLM_BASE_URL"), ("model", "OLX_TUNER_LLM_MODEL"), ("api_key", "OLX_TUNER_LLM_API_KEY")]:
        if os.environ.get(env) or (env == "OLX_TUNER_LLM_API_KEY" and os.environ.get("OPENAI_API_KEY")):
            env_keys.append(k)
    return jsonify({"ok": True, "llm": sanitized, "effective": sanitized, "env_override": env_keys, "stored": _sanitize_llm_config(llm)})


@app.route("/api/config/llm", methods=["PUT"])
def api_put_llm():
    data = request.get_json(force=True) or {}
    # aceita tanto {"tuner_llm": {...}} quanto flat {"provider":..., "base_url":...}
    raw = data.get("tuner_llm") if isinstance(data.get("tuner_llm"), dict) else data
    if not isinstance(raw, dict):
        return jsonify({"ok": False, "errors": ["tuner_llm deve ser objeto"]}), 400
    # mascarado ou ausente -> mantém stored (PATCH parcial não apaga chave)
    stored = olx_scraper.load_config().get("tuner_llm") or {}
    # se api_key não veio no payload, preserva stored
    if "api_key" not in raw and "apikey" not in raw and "apiKey" not in raw and "token" not in raw:
        if stored.get("api_key"):
            raw = dict(raw)
            raw["api_key"] = stored["api_key"]
    elif _looks_masked(raw.get("api_key")):
        if stored.get("api_key"):
            raw = dict(raw)
            raw["api_key"] = stored["api_key"]
    # valida via helper compartilhado (DRY com api_put_config)
    prov_tmp = str(raw.get("provider") or "").strip().lower() if raw.get("provider") is not None else None
    if prov_tmp in ("openai", "openai-compatible"):
        raw["provider"] = "openrouter"
    errors = _validate_tuner_llm_payload(raw)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    try:
        # merge parcial: preserva campos não enviados (provider/base_url/model/api_key)
        merge_raw = dict(stored) if isinstance(stored, dict) else {}
        # raw contém só o que foi enviado (ou mascarado já tratado acima)
        for k in ("provider", "base_url", "baseUrl", "model", "model_name", "llm_model", "api_key", "apikey", "apiKey", "token", "url"):
            if k in raw:
                merge_raw[k] = raw[k]
        # compat: se veio baseUrl sem base_url
        if "baseUrl" in raw and "base_url" not in raw:
            merge_raw["base_url"] = raw["baseUrl"]
        normalized = olx_scraper._normalize_tuner_llm(merge_raw)
        # se veio mascarado, _normalize perdeu, restaura
        if _looks_masked(data.get("tuner_llm", {}).get("api_key") if isinstance(data.get("tuner_llm"), dict) else data.get("api_key")):
            if stored.get("api_key"):
                normalized["api_key"] = stored["api_key"]
        # também se api_key não veio no payload, _normalize zerou mas merge já preservou; garante novamente
        if "api_key" not in raw and "apikey" not in raw and stored.get("api_key") and not normalized.get("api_key"):
            normalized["api_key"] = stored["api_key"]
        saved = olx_scraper.save_config({"tuner_llm": normalized})
    except Exception as e:
        return jsonify({"ok": False, "errors": [str(e)]}), 400
    return jsonify({"ok": True, "llm": _sanitize_llm_config(saved.get("tuner_llm")), "config": _sanitize_config(saved)})


# cache para modelos: 5 minutos
_tuner_models_cache = {"ts": 0, "data": None, "key": None, "error": None}
_TUNER_MODELS_TTL = 300  # 5min


@app.route("/api/tuner/models", methods=["GET"])
def api_tuner_models():
    """Proxy para {base_url}/models com cache 5m. Query ?provider=&base_url=&force=1.

    Tenta primeiro config efetiva (env > config.json), depois override via query se fornecido.
    Suporta openrouter (https://openrouter.ai/api/v1/models) e opencode (http://127.0.0.1:4096/v1/models).
    Falha não bloqueia criação de pedido — retorna lista vazia com error.
    """
    # determina base_url e provider efetivos
    cfg = olx_scraper.load_config()
    effective = olx_scraper.get_tuner_llm_config(cfg)
    q_prov = (request.args.get("provider") or "").strip().lower()
    q_bu = (request.args.get("base_url") or request.args.get("baseUrl") or "").strip()
    q_force = request.args.get("force") in ("1", "true", "yes")
    provider = effective.get("provider") or "openrouter"
    base_url = effective.get("base_url") or ""
    api_key = effective.get("api_key") or ""
    if q_prov:
        if q_prov in ("openrouter", "opencode", "mock"):
            provider = q_prov
        elif q_prov in ("openai", "openai-compatible"):
            provider = "openrouter"
        if q_prov == "openrouter" and not q_bu:
            base_url = "https://openrouter.ai/api/v1"
        elif q_prov == "opencode" and not q_bu:
            base_url = "http://127.0.0.1:4096/v1"
        elif q_prov == "mock":
            base_url = ""
    if q_bu:
        if q_bu.startswith("http://") or q_bu.startswith("https://"):
            base_url = q_bu.rstrip("/")
    # mock -> lista sintética
    if provider == "mock":
        return jsonify({"ok": True, "provider": provider, "base_url": base_url, "models": [{"id": "mock-model-1", "name": "mock-model-1"}, {"id": "mock-model-2", "name": "mock-model-2"}], "cached": False})
    if not base_url:
        if provider == "openrouter":
            base_url = "https://openrouter.ai/api/v1"
        elif provider == "opencode":
            base_url = "http://127.0.0.1:4096/v1"
    cache_key = f"{provider}|{base_url}|{_hash_api_key_for_cache(api_key)}"
    now = time.time()
    if _tuner_models_cache["data"] is not None and _tuner_models_cache["key"] == cache_key and now - _tuner_models_cache["ts"] < _TUNER_MODELS_TTL and not q_force:
        # limpa error antigo ao servir do cache hit
        _tuner_models_cache["error"] = None
        return jsonify({"ok": True, "provider": provider, "base_url": base_url, "models": _tuner_models_cache["data"], "cached": True})
    # fetch
    url = base_url.rstrip("/") + "/models"
    headers = {}
    if provider == "openrouter":
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        # headers recomendados openrouter
        headers["HTTP-Referer"] = "https://github.com/luanfoli/olx-deals"
        headers["X-Title"] = "olx-deals tuner"
    elif provider == "opencode":
        if api_key and api_key != "not-needed":
            headers["Authorization"] = f"Bearer {api_key}"
    try:
        import httpx  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": f"httpx não disponível: {e}", "provider": provider, "base_url": base_url, "models": []}), 500
    try:
        with httpx.Client(timeout=5.0, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
        j = resp.json()
        # openrouter retorna {"data": [{"id": "...", "name": "..."}]}, opencode/ollama similar, fallback lista
        raw_models = []
        if isinstance(j, dict) and isinstance(j.get("data"), list):
            raw_models = j["data"]
        elif isinstance(j, list):
            raw_models = j
        elif isinstance(j, dict) and isinstance(j.get("models"), list):
            raw_models = j["models"]
        else:
            raw_models = []
        models = []
        for m in raw_models:
            if isinstance(m, dict) and m.get("id"):
                models.append({"id": str(m["id"]), "name": str(m.get("name") or m["id"]), "raw": m})
            elif isinstance(m, str):
                models.append({"id": m, "name": m})
        # fallback: se vazio, tenta lista estática por provider
        if not models:
            raise RuntimeError("resposta sem modelos")
        # ordena por id
        models = sorted(models, key=lambda x: x["id"].lower())
        _tuner_models_cache.update({"ts": now, "data": models, "key": cache_key, "error": None})
        return jsonify({"ok": True, "provider": provider, "base_url": base_url, "models": models, "cached": False})
    except Exception as e:
        # não bloqueia: retorna erro mas com cache antigo se houver
        err = str(e)[:500]
        # se já tem cache antigo, retorna ele com flag cached
        if _tuner_models_cache["data"] is not None and _tuner_models_cache["key"] == cache_key:
            return jsonify({"ok": False, "error": err, "provider": provider, "base_url": base_url, "models": _tuner_models_cache["data"], "cached": True})
        return jsonify({"ok": False, "error": err, "provider": provider, "base_url": base_url, "models": []}), 502


# --- Products CRUD ---
@app.route("/api/products", methods=["GET"])
def api_list_products():
    cfg = olx_scraper.load_config()
    return jsonify({"products": cfg.get("products", [])})


@app.route("/api/products", methods=["POST"])
def api_create_product():
    data = request.get_json(force=True) or {}
    errors = _validate_product_payload(data, is_update=False)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    cfg = olx_scraper.load_config()
    products = cfg.get("products", [])
    # verifica id duplicado se fornecido
    if "id" in data:
        pid = olx_scraper._slugify(str(data["id"]))
        if any(p["id"] == pid for p in products):
            return jsonify({"ok": False, "errors": [f"id '{pid}' já existe"]}), 409
    # normaliza via helper
    new_prod = olx_scraper._normalize_product(data, set(p["id"] for p in products))
    products.append(new_prod)
    saved = olx_scraper.save_config({"products": products})
    try:
        reschedule_all()
    except Exception as e:
        _append_log(f"schedule: reschedule falhou após create: {e}")
    # retorna produto criado com id final
    created = next((p for p in saved["products"] if p["id"] == new_prod["id"]), new_prod)
    return jsonify({"ok": True, "product": created, "config": _sanitize_config(saved)}), 201


@app.route("/api/products/<pid>", methods=["GET"])
def api_get_product(pid):
    cfg = olx_scraper.load_config()
    prod = next((p for p in cfg.get("products", []) if p["id"] == pid), None)
    if not prod:
        return jsonify({"ok": False, "error": "produto não encontrado"}), 404
    return jsonify({"ok": True, "product": prod})


@app.route("/api/products/<pid>", methods=["PUT"])
def api_update_product(pid):
    data = request.get_json(force=True) or {}
    # não permite mudar id via body para outro já existente
    if "id" in data and olx_scraper._slugify(str(data["id"])) != pid:
        # se mudou id, verifica unicidade
        new_id = olx_scraper._slugify(str(data["id"]))
        cfg_tmp = olx_scraper.load_config()
        if any(p["id"] == new_id for p in cfg_tmp.get("products", [])):
            return jsonify({"ok": False, "errors": [f"id '{new_id}' já existe"]}), 409
    errors = _validate_product_payload(data, is_update=True)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    cfg = olx_scraper.load_config()
    products = cfg.get("products", [])
    idx = next((i for i, p in enumerate(products) if p["id"] == pid), None)
    if idx is None:
        return jsonify({"ok": False, "error": "produto não encontrado"}), 404
    # merge
    base = dict(products[idx])
    # preserva id original se não veio novo id
    base.update(data)
    if "id" not in data:
        base["id"] = pid
    # normaliza mantendo unicidade (exclui ele mesmo da lista de ids)
    other_ids = set(p["id"] for i, p in enumerate(products) if i != idx)
    normalized = olx_scraper._normalize_product(base, other_ids)
    # se id foi alterado via normalização mas veio explícito, mantém o solicitado slugificado
    if "id" in data:
        normalized["id"] = olx_scraper._slugify(str(data["id"]))
    else:
        normalized["id"] = pid
    products[idx] = normalized
    saved = olx_scraper.save_config({"products": products})
    try:
        reschedule_all()
    except Exception as e:
        _append_log(f"schedule: reschedule falhou após update {pid}: {e}")
    updated = next((p for p in saved["products"] if p["id"] == normalized["id"]), normalized)
    return jsonify({"ok": True, "product": updated, "config": _sanitize_config(saved)})


@app.route("/api/products/<pid>", methods=["DELETE"])
def api_delete_product(pid):
    cfg = olx_scraper.load_config()
    products = cfg.get("products", [])
    new_products = [p for p in products if p["id"] != pid]
    if len(new_products) == len(products):
        return jsonify({"ok": False, "error": "produto não encontrado"}), 404
    if not new_products:
        return jsonify({"ok": False, "error": "não pode remover o último produto; desative-o em vez disso"}), 400
    saved = olx_scraper.save_config({"products": new_products})
    try:
        reschedule_all()
    except Exception as e:
        _append_log(f"schedule: reschedule falhou após delete {pid}: {e}")
    return jsonify({"ok": True, "config": _sanitize_config(saved)})


@app.route("/api/deals", methods=["GET"])
def api_deals():
    deals = _load_deals()
    matched_only = request.args.get("matched_only") in ("1", "true", "yes")
    q = (request.args.get("q") or "").strip().lower()
    product_id = (request.args.get("product_id") or "").strip()
    notified_filter = request.args.get("notified")  # "0" só não notificados, "1" só notificados
    sort = request.args.get("sort") or "fetched_at"
    order = request.args.get("order") or "desc"
    limit = request.args.get("limit")
    offset = request.args.get("offset")
    # --- re-evaluate / view / adherent (Issue #7 / PLANO-ISSUE-7) ---
    re_evaluate = False
    re_val = (request.args.get("re-evaluate") or "").strip().lower()
    re_val2 = (request.args.get("re_evaluate") or "").strip().lower()
    view_val = (request.args.get("view") or "").strip().lower()
    if re_val in ("1", "true", "yes"):
        re_evaluate = True
    elif re_val2 in ("1", "true", "yes"):
        re_evaluate = True
    elif view_val == "current":
        re_evaluate = True
    elif view_val == "ingested":
        re_evaluate = False
    adherent_raw = (request.args.get("adherent") or "").strip()
    if adherent_raw and adherent_raw not in ("0", "1"):
        # allow empty = no filter; otherwise must be 0/1
        if adherent_raw != "":
            return jsonify({"ok": False, "error": "adherent deve ser 0 ou 1"}), 400
    if adherent_raw in ("0", "1") and not re_evaluate:
        return jsonify({"ok": False, "error": "adherent requer re-evaluate=1 ou view=current"}), 400
    # status filter (Issue #9 compat)
    status_q = None
    status_raw = request.args.get("status")
    removed_raw = request.args.get("removed")
    if status_raw is not None and str(status_raw).strip() != "":
        sq = str(status_raw).strip().lower()
        if sq not in ("active", "removed", "all"):
            return jsonify({"ok": False, "error": "status deve ser active|removed|all"}), 400
        status_q = sq
    elif removed_raw is not None and str(removed_raw).strip() != "":
        rq = str(removed_raw).strip()
        if rq not in ("0", "1"):
            return jsonify({"ok": False, "error": "removed deve ser 0 ou 1"}), 400
        status_q = "removed" if rq == "1" else "active"

    # join com registro de notificações: campo derivado notified por deal
    try:
        notified_reg = olx_scraper.load_notified()
    except Exception:
        notified_reg = {}
    for d in deals:
        d["notified"] = f"{d.get('product_id', '')}:{d.get('id')}" in notified_reg

    filtered = deals
    # status filter (before re-evaluate, sobre status histórico)
    if status_q and status_q != "all":
        filtered = [d for d in filtered if (d.get("status") or "active").lower() == status_q]

    if product_id and product_id != "all":
        filtered = [d for d in filtered if d.get("product_id") == product_id]

    if notified_filter == "0":
        filtered = [d for d in filtered if not d.get("notified")]
    elif notified_filter == "1":
        filtered = [d for d in filtered if d.get("notified")]

    if q:
        def _hit(d):
            hay = f"{d.get('title','')} {d.get('url','')} {d.get('id','')} {d.get('product_name','')} {d.get('product_id','')}".lower()
            return q in hay
        filtered = [d for d in filtered if _hit(d)]

    # filtro ad-hoc por preço (view apenas, não altera config)
    price_min_raw = request.args.get("price_min")
    price_max_raw = request.args.get("price_max")
    pmin = None
    pmax = None
    if price_min_raw not in (None, ""):
        try:
            pmin = float(price_min_raw)
        except Exception:
            return jsonify({"ok": False, "error": "price_min deve ser número"}), 400
        if not (0 < pmin <= 1_000_000):
            return jsonify({"ok": False, "error": "price_min fora de faixa 0-1000000"}), 400
    if price_max_raw not in (None, ""):
        try:
            pmax = float(price_max_raw)
        except Exception:
            return jsonify({"ok": False, "error": "price_max deve ser número"}), 400
        if not (0 < pmax <= 1_000_000):
            return jsonify({"ok": False, "error": "price_max fora de faixa 0-1000000"}), 400
    if pmin is not None and pmax is not None and pmin >= pmax:
        return jsonify({"ok": False, "error": "price_min deve ser < price_max"}), 400
    if pmin is not None or pmax is not None:
        filtered = [d for d in filtered
                    if d.get("price") is not None
                    and (pmin is None or float(d["price"]) >= pmin)
                    and (pmax is None or float(d["price"]) <= pmax)]

    # --- re-evaluate derivation ---
    if re_evaluate:
        try:
            cfg = olx_scraper.load_config()
            active_ids = set(p["id"] for p in cfg.get("products", []))
            product_map = {p["id"]: p for p in cfg.get("products", [])}
            for d in filtered:
                pcfg = product_map.get(d.get("product_id"))
                m_cur, r_cur = olx_scraper._evaluate_current_detailed(d, pcfg, active_ids)
                d["matched_current"] = m_cur
                d["discard_reason_current"] = r_cur
        except Exception:
            # se falhar, mantém sem derivacao
            pass
        # aplica filtros matched_only/adherent sobre view current
        # regra hibrida para satisfazer critérios do backlog:
        # - se adherent presente: matched_only filtra histórico, adherent filtra current
        # - se adherent ausente: matched_only filtra current
        if adherent_raw in ("0", "1"):
            if matched_only:
                filtered = [d for d in filtered if d.get("matched")]
            want = (adherent_raw == "1")
            filtered = [d for d in filtered if bool(d.get("matched_current")) == want]
        elif matched_only:
            filtered = [d for d in filtered if d.get("matched_current")]
    else:
        if matched_only:
            filtered = [d for d in filtered if d.get("matched")]

    reverse = (order == "desc")
    # ordenação genérica: price, fetched_at, title. Mantém compat com price_per_tb/tb mas mapeia para price
    if sort in ("price", "fetched_at"):
        filtered = sorted(filtered, key=lambda d: (d.get(sort) is None, d.get(sort)), reverse=reverse)
    elif sort in ("price_per_tb", "tb"):
        # compat legado: ordena por price para não quebrar
        filtered = sorted(filtered, key=lambda d: (d.get("price") is None, d.get("price")), reverse=reverse)
    elif sort == "title":
        filtered = sorted(filtered, key=lambda d: (d.get("title") or "").lower(), reverse=reverse)
    elif sort == "product":
        filtered = sorted(filtered, key=lambda d: (d.get("product_name") or d.get("product_id") or "").lower(), reverse=reverse)

    total = len(filtered)
    try:
        if offset is not None:
            offset = max(0, int(offset))
            filtered = filtered[offset:]
        if limit is not None:
            limit = int(limit)
            filtered = filtered[: max(0, limit)]
    except Exception:
        pass

    return jsonify({"total": total, "count": len(filtered), "deals": filtered})


@app.route("/api/deals/raw", methods=["GET"])
def api_deals_raw():
    deals = _load_deals()
    return jsonify(deals)


@app.route("/api/deals/renotify", methods=["POST"])
def api_deals_renotify():
    """Reenvia 1 deal: remove a chave do registro de notificados e notifica imediatamente.

    Body: {"product_id": "...", "ad_id": "..."} ou {"key": "product_id:ad_id"}.
    Se o envio falhar, a chave permanece fora do registro -> retry no próximo scrape.
    """
    body = request.get_json(force=True) or {}
    key = body.get("key")
    if not key:
        pid = str(body.get("product_id") or "").strip()
        aid = str(body.get("ad_id") or body.get("id") or "").strip()
        if not pid or not aid:
            return jsonify({"ok": False, "error": "informe product_id e ad_id (ou key)"}), 400
        key = f"{pid}:{aid}"
    key = str(key)
    deal = next((d for d in _load_deals() if f"{d.get('product_id', '')}:{d.get('id')}" == key), None)
    if not deal:
        return jsonify({"ok": False, "error": f"deal não encontrado: {key}"}), 404
    removed = olx_scraper.remove_notified([key])
    counters = olx_scraper.notify([deal])
    _append_log(f"renotify {key}: registro removido={removed}, contadores={counters}")
    return jsonify({"ok": True, "key": key, "removed": removed, "counters": counters})


@app.route("/api/deals/purge", methods=["POST"])
def api_deals_purge():
    # guarda: nao permite purge concorrente com scrape
    with job_lock:
        if job["running"]:
            return jsonify({"ok": False, "error": "Já há uma busca em andamento"}), 409
    body = {}
    if request.is_json:
        try:
            body = request.get_json(force=True) or {}
        except Exception:
            body = {}
    else:
        try:
            body = request.get_json(force=True) or {}
        except Exception:
            body = {}
    dry_run = bool(body.get("dry_run") or body.get("dryRun"))
    confirm = bool(body.get("confirm"))
    # tambem aceita ?dry_run e ?confirm via query para conveniência
    if request.args.get("dry_run") in ("1", "true", "yes"):
        dry_run = True
    if request.args.get("confirm") in ("1", "true", "yes"):
        confirm = True
    if not dry_run and not confirm:
        return jsonify({"ok": False, "error": "expurgo requer confirm=true (ou dry_run=true para preview)"}), 400
    # dry_run tem precedencia sobre confirm se ambos true? considera dry_run
    if dry_run:
        confirm = False
    product_ids = body.get("product_ids")
    if isinstance(product_ids, str):
        product_ids = [product_ids]
    if product_ids is not None:
        if not isinstance(product_ids, list):
            return jsonify({"ok": False, "error": "product_ids deve ser lista"}), 400
        product_ids = [str(x).strip() for x in product_ids if str(x).strip()]
        # lista vazia explicita => nenhum candidato (nao "todos")
        # mantem []
    reasons = body.get("reasons")
    if isinstance(reasons, str):
        reasons = [reasons]
    if reasons is not None:
        if not isinstance(reasons, list):
            return jsonify({"ok": False, "error": "reasons deve ser lista"}), 400
        reasons = [str(x).strip().lower() for x in reasons if str(x).strip()]
        if not reasons:
            reasons = None
    status_q = (body.get("status") or request.args.get("status") or "").strip().lower()
    if status_q and status_q not in ("active", "removed", "all"):
        return jsonify({"ok": False, "error": "status deve ser active|removed|all"}), 400
    include_orphans = bool(body.get("include_orphans") or body.get("includeOrphans"))
    # query param tambem
    if request.args.get("include_orphans") in ("1", "true", "yes"):
        include_orphans = True
    older_than_raw = body.get("older_than") or body.get("olderThan") or request.args.get("older_than")
    older_than_dt = None
    if older_than_raw is not None and str(older_than_raw).strip() != "":
        s = str(older_than_raw).strip()
        # suporta "30d" como atalho relativo
        if s.lower().endswith("d") and s[:-1].strip().isdigit():
            try:
                days = int(s[:-1])
                older_than_dt = datetime.now(timezone.utc) - timedelta(days=days)
            except Exception:
                return jsonify({"ok": False, "error": "older_than inválido (ISO UTC esperado)"}), 400
        else:
            # Flask decodifica + como espaço; restaura
            if " " in s and "+" not in s and "T" in s:
                s = s.replace(" ", "+")
            older_than_dt = _parse_iso(s)
            if older_than_dt is None:
                return jsonify({"ok": False, "error": "older_than inválido (ISO UTC esperado)"}), 400
            if older_than_dt.tzinfo is None:
                older_than_dt = older_than_dt.replace(tzinfo=timezone.utc)
    adherent_filter = body.get("adherent")
    if adherent_filter is not None:
        adherent_str = str(adherent_filter).strip()
        if adherent_str not in ("0", "1"):
            return jsonify({"ok": False, "error": "adherent deve ser 0 ou 1"}), 400
        adherent_str = adherent_str
    else:
        adherent_str = None

    # carrega config e deals
    try:
        cfg = olx_scraper.load_config()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    active_ids = set(p["id"] for p in cfg.get("products", []))
    product_map = {p["id"]: p for p in cfg.get("products", [])}
    deals = _load_deals()
    # enriquece
    enriched = []
    for d in deals:
        pcfg = product_map.get(d.get("product_id"))
        try:
            m_cur, r_cur = olx_scraper._evaluate_current_detailed(d, pcfg, active_ids)
        except Exception:
            m_cur, r_cur = False, olx_scraper.DISCARD_ORPHAN
        enriched.append((d, m_cur, r_cur))

    # filtra candidatos
    candidates = []
    for d, m_cur, r_cur in enriched:
        # status
        if status_q and status_q != "all":
            d_status = (d.get("status") or "active").lower()
            if d_status != status_q:
                continue
        # older_than
        if older_than_dt is not None:
            if status_q == "removed":
                ref = d.get("removed_at")
            else:
                ref = d.get("last_seen_at") or d.get("fetched_at")
            ref_dt = _parse_iso(ref) if ref else None
            if ref_dt is None or not (ref_dt < older_than_dt):
                continue
        if product_ids is not None and d.get("product_id") not in product_ids:
            continue
        if reasons is not None:
            norm_r = (r_cur or "").lower()
            ok = False
            for r in reasons:
                rl = r.lower()
                if rl == norm_r:
                    ok = True
                    break
                # prefix match: "exclude" casa "exclude:wii u"
                if ":" not in rl and norm_r.startswith(rl + ":"):
                    ok = True
                    break
                # also exact prefix before colon?
                if rl == norm_r.split(":")[0]:
                    # ja tratado acima, mas manter
                    if norm_r.startswith(rl):
                        ok = True
                        break
            if not ok:
                continue
        # adherent
        if adherent_str is not None:
            want = (adherent_str == "1")
            if bool(m_cur) != want:
                continue
        else:
            # default purge: só não-aderentes, exceto quando purgando removed antigos (status=removed+older_than)
            if not (status_q == "removed" and older_than_dt is not None):
                if bool(m_cur) != False:
                    continue
                # sem filtro explícito (global vazio) mantém guarda histórico para não apagar price em massa;
                # com produto ou motivo explícito considera só hoje (atende modo antigo wiiu salvo e modo novo que nem salva)
                if reasons is None and product_ids is None and not d.get("matched"):
                    continue
        # gate orfaos para execucao
        is_orphan = (r_cur == olx_scraper.DISCARD_ORPHAN or olx_scraper._is_orphan(d.get("product_id"), active_ids))
        if is_orphan:
            if not dry_run and not include_orphans:
                if product_ids is None or d.get("product_id") not in (product_ids or []):
                    continue
        candidates.append((d, m_cur, r_cur))

    # agregacoes
    by_product = {}
    by_reason = {}
    for d, m_cur, r_cur in candidates:
        pid = d.get("product_id") or "?"
        by_product[pid] = by_product.get(pid, 0) + 1
        key = r_cur or "unknown"
        by_reason[key] = by_reason.get(key, 0) + 1
    # notified affected
    try:
        notified_reg = olx_scraper.load_notified()
    except Exception:
        notified_reg = {}
    notified_entries_affected = sum(1 for d, _, _ in candidates if f"{d.get('product_id')}:{d.get('id')}" in notified_reg)

    if dry_run:
        # preview sem escrita
        cand_deals = [d for d, _, _ in candidates[:200]]
        preview_truncated = len(candidates) > 200
        _append_log(f"purge dry_run candidates={len(candidates)} by_product={by_product} by_reason={by_reason} notified_affected={notified_entries_affected}")
        return jsonify({
            "ok": True,
            "dry_run": True,
            "total_candidates": len(candidates),
            "by_product": by_product,
            "by_reason": by_reason,
            "notified_entries_affected": notified_entries_affected,
            "candidates": cand_deals,
            "preview_truncated": preview_truncated
        })

    # execucao real: backup + purge
    candidate_keys = set(f"{d.get('product_id')}:{d.get('id')}" for d, _, _ in candidates)
    def _pred(deal):
        return f"{deal.get('product_id')}:{deal.get('id')}" in candidate_keys
    try:
        result = olx_scraper.purge_deals(_pred, dry_run=False)
    except Exception as e:
        return jsonify({"ok": False, "error": f"falha ao purgar: {e}"}), 500
    _append_log(f"purge exec purged={result['purged']} remaining={result['remaining']} backup={result['backup_path']} by_product={by_product} by_reason={by_reason} notified_affected={notified_entries_affected}")
    return jsonify({
        "ok": True,
        "dry_run": False,
        "purged": result["purged"],
        "remaining": result["remaining"],
        "backup_path": result["backup_path"],
        "by_product": by_product,
        "by_reason": by_reason,
        "notified_entries_affected": notified_entries_affected
    })


# --- Tuner in-app (Backlog #12) — hardening: fonte única em validation.py ---
from olx_deals.interfaces.api.validation import (
    _validate_tuner_payload,
    _tuner_normalize_briefing,
    _tuner_create_draft_product,
)


tuner_lock = threading.RLock()
tuner_queue = deque()

def _run_tuner(tuner_id: str):
    """Worker de refinamento iterativo para um pedido tuner. Roda em thread. Usa LLM com fallback heurístico."""
    try:
        req = olx_scraper.get_tuner_request(tuner_id)
        if not req:
            return
        # marca em_refinamento com tuner_lock dedicado (não bloqueia job global mas sincroniza HISTORY)
        with tuner_lock:
            req = olx_scraper.get_tuner_request(tuner_id)
            if not req:
                return
            req["status"] = "em_refinamento"
            req["updated_at"] = datetime.now(timezone.utc).isoformat()
            olx_scraper.upsert_tuner_request(req)
        briefing = req.get("briefing") or {}
        draft_pid = req.get("draft_product_id")
        iterations = req.get("iterations") or []
        max_iters = olx_scraper.TUNER_MAX_ITERATIONS
        threshold = olx_scraper.TUNER_PRECISION_THRESHOLD
        for it in range(1, max_iters + 1):
            # verifica se ainda está em refinamento (não foi descartado/ativado)
            cur = olx_scraper.get_tuner_request(tuner_id)
            if not cur or cur.get("status") not in ("pendente", "em_refinamento"):
                break
            # carrega produto atual
            cfg = olx_scraper.load_config()
            prod = next((p for p in cfg.get("products", []) if p["id"] == draft_pid), None)
            if not prod:
                _append_log(f"tuner {tuner_id}: draft {draft_pid} não encontrado, abortando")
                break
            snapshot = dict(prod)
            started = datetime.now(timezone.utc).isoformat()
            # dispara scrape restrito ao draft (usa scrape direto, sem job global)
            def _log(m):
                _append_log(f"tuner {tuner_id} iter{it}: {m}")
            stats = {}
            try:
                results = olx_scraper.scrape([prod], log_fn=_log, progress_cb=None, stats_out=stats, force_enabled=True)
                with job_lock:
                    new = olx_scraper.save_history(results)
                _log(f"iter{it} scrape ok: {len(results)} total, {len([r for r in results if r.get('matched')])} matched, new={len(new)}")
            except Exception as e:
                _log(f"iter{it} scrape erro: {e}")
                # registra iteração de erro com llm placeholder
                iteration = {
                    "n": it,
                    "started_at": started,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "product_snapshot": snapshot,
                    "deals_sample": [],
                    "queries": list(prod.get("queries", [])),
                    "include_pattern": prod.get("include_pattern", ""),
                    "exclude_keywords": list(prod.get("exclude_keywords", [])),
                    "min_price": prod.get("min_price"),
                    "max_price": prod.get("max_price"),
                    "total_cards": int(stats.get("total_cards", 0)),
                    "matched": int(stats.get("matched", 0)),
                    "precision": 0.0,
                    "tp_count": 0,
                    "fp_count": 0,
                    "classification": {"tp": 0, "fp": 0, "precision": 0.0, "examples": {"tp": [], "fp": []}},
                    "llm": {"used": False, "provider": None, "model": None, "latency_ms": None, "prompt_tokens": None, "completion_tokens": None, "error": str(e)[:500]},
                    "refinement": {},
                    "proposed": None,
                    "applied": False,
                    "error": str(e)[:500],
                }
                iterations.append(iteration)
                # precisa recarregar req para não perder concorrência?
                req_cur = olx_scraper.get_tuner_request(tuner_id) or {"iterations": iterations}
                req_cur["iterations"] = iterations
                req_cur["updated_at"] = datetime.now(timezone.utc).isoformat()
                olx_scraper.upsert_tuner_request(req_cur)
                # mantém referência local
                req = req_cur
                continue
            # carrega deals matched para este draft
            deals = _load_deals()
            # filtra por draft_product_id e matched
            matched_deals = [d for d in deals if d.get("product_id") == draft_pid and d.get("matched")]
            # ordena por fetched_at desc, pega últimos 50 para prompt LLM (plano limita 50)
            try:
                matched_deals = sorted(matched_deals, key=lambda d: d.get("fetched_at", ""), reverse=True)[:50]
            except Exception:
                pass
            # fallback quando 0 matched mas houve cards: usa amostra de todos (inclui descartados) para permitir LLM corrigir include/queries
            if not matched_deals and int(stats.get("total_cards", 0) or 0) > 0:
                fallback_all = [d for d in deals if d.get("product_id") == draft_pid]
                if not fallback_all and 'results' in locals() and results:
                    # results é o lote recém-coletado desta iteração (contém matched flag)
                    fallback_all = results[:50]
                if fallback_all:
                    try:
                        fallback_all = sorted(fallback_all, key=lambda d: d.get("fetched_at", ""), reverse=True)[:50]
                    except Exception:
                        fallback_all = fallback_all[:50]
                    matched_deals = fallback_all
                    _log(f"iter{it} 0 matched mas {stats.get('total_cards')} cards totais -> fallback amostra {len(matched_deals)} para classificação")
            # classifica via LLM (ou fallback heurístico) com audit llm_meta
            # deals amostra para LLM: max 50, mas para métrica usa até 30? Usa matched_deals mesmo
            result_llm, llm_meta = olx_scraper.tuner_classify_deals(briefing, matched_deals[:50])
            # result_llm contém classifications, tp/fp, precision, proposed_refinement
            tp = result_llm.get("tp") or []
            fp = result_llm.get("fp") or []
            prec = float(result_llm.get("precision") or 0.0)
            total_matched = int(result_llm.get("total") or len(tp)+len(fp))
            # proposta vem do LLM (ou heurística fallback) já em result
            proposed = result_llm.get("proposed_refinement") or {}
            # se LLM não propsôs nada mas heurística teria algo, tenta fallback propose via fp
            if not proposed or (not proposed.get("add_exclude") and not proposed.get("new_include_pattern")):
                if prec < threshold:
                    # tenta heurística adicional baseada em fp
                    fallback_prop = olx_scraper.tuner_propose_refinement(briefing, fp, prod)
                    if fallback_prop.get("add_exclude") or fallback_prop.get("new_include_pattern"):
                        proposed = fallback_prop
                if not proposed:
                    proposed = {}
            # normaliza proposed para garantir llm + heurística shape
            proposed = dict(proposed) if isinstance(proposed, dict) else {}
            # exemplos para iteration: usa classifications com reason
            classifications = result_llm.get("classifications") or []
            examples_tp = [{"title": d.get("title"), "price": d.get("price"), "url": d.get("url")} for d in tp[:3]]
            examples_fp = []
            for c in [x for x in classifications if x.get("label") == "fp"][:3]:
                # tenta achar deal original para pegar título
                orig = next((d for d in matched_deals if str(d.get("id")) == str(c.get("id"))), {})
                examples_fp.append({"id": c.get("id"), "title": orig.get("title") or "", "price": orig.get("price"), "url": orig.get("url") or "", "reason": c.get("reason") or c.get("reason", ""), "motivo": c.get("reason"), "sugestao": proposed.get("reason") if isinstance(proposed, dict) else ""})
            # monta iteration com campos novos (llm audit) + legados para compat
            iteration = {
                "n": it,
                "started_at": started,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "product_snapshot": snapshot,
                "deals_sample": matched_deals[:50],
                "classification": {"tp": len(tp), "fp": len(fp), "precision": round(prec, 3), "examples": {"tp": [{"id": c.get("id"), "reason": c.get("reason")} for c in classifications if c.get("label")=="tp"][:3], "fp": [{"id": c.get("id"), "reason": c.get("reason")} for c in classifications if c.get("label")=="fp"][:3]}},
                "llm": llm_meta,
                "refinement": proposed,
                "queries": list(prod.get("queries", [])),
                "include_pattern": prod.get("include_pattern", ""),
                "exclude_keywords": list(prod.get("exclude_keywords", [])),
                "min_price": prod.get("min_price"),
                "max_price": prod.get("max_price"),
                "total_cards": int(stats.get("total_cards", 0)),
                "matched": total_matched,
                "precision": round(prec, 3),
                "tp_count": len(tp),
                "fp_count": len(fp),
                "examples_tp": examples_tp,
                "examples_fp": examples_fp,
                "proposed": proposed,
                "applied": False,
            }
            iterations.append(iteration)
            req["iterations"] = iterations
            # verifica convergência: precision >= threshold ou fp==0 and tp>=5
            converged = (prec >= threshold) or (len(fp) == 0 and len(tp) >= 5)
            if converged:
                req["status"] = "aguardando_aprovacao"
                req["updated_at"] = datetime.now(timezone.utc).isoformat()
                # monta final_report
                cfg_final = olx_scraper.load_config()
                prod_final = next((p for p in cfg_final.get("products", []) if p["id"] == draft_pid), prod)
                recomendacao = "ativar" if (prec >= threshold or (len(fp) == 0 and len(tp) >= 5)) else "revisao_manual"
                req["recommendation"] = recomendacao
                req["final_config"] = prod_final
                req["final_report"] = {
                    "briefing": briefing,
                    "evolucao": iterations,
                    "config_final": prod_final,
                    "ofertas_finais": [{"title": d.get("title"), "price": d.get("price"), "url": d.get("url")} for d in tp[:10]],
                    "precision_final": prec,
                    "recomendacao": recomendacao,
                    "total_tp": len(tp),
                    "total_fp": len(fp),
                }
                olx_scraper.upsert_tuner_request(req)
                _log(f"convergiu it{it} prec={prec:.2f} tp={len(tp)} fp={len(fp)} -> {recomendacao}")
                break
            # não convergiu e tem proposta: aplica refinamento antes da próxima iteração
            if proposed:
                # registra proposta antes de aplicar (já está em iteration.proposed/refinement)
                # aplica: só filtros locais (exclude, include, preco) preservando recall
                new_excludes = list(prod.get("exclude_keywords", []))
                for term in proposed.get("add_exclude") or []:
                    term = str(term).strip()
                    if term and term.lower() not in [x.lower() for x in new_excludes] and len(term) <= 40:
                        new_excludes.append(term)
                        if len(new_excludes) >= 50:
                            break
                patch = {}
                if new_excludes != prod.get("exclude_keywords", []):
                    patch["exclude_keywords"] = new_excludes[:50]
                if proposed.get("new_include_pattern") and not prod.get("include_pattern"):
                    # valida regex e limita 500 chars
                    pat = str(proposed["new_include_pattern"])[:500]
                    try:
                        re.compile(pat, re.I) if pat else None
                        patch["include_pattern"] = pat
                    except re.error:
                        pass
                # price_adjust vindo da LLM (opcional)
                pa = proposed.get("price_adjust") or {}
                if isinstance(pa, dict):
                    if "min" in pa and pa["min"] not in (None, ""):
                        try:
                            fv = float(pa["min"])
                            if 0 < fv <= 1000000:
                                patch["min_price"] = fv
                        except Exception:
                            pass
                    if "max" in pa and pa["max"] not in (None, ""):
                        try:
                            fv = float(pa["max"])
                            if 0 < fv <= 1000000:
                                patch["max_price"] = fv
                        except Exception:
                            pass
                    # valida min < max se ambos
                    if "min_price" in patch and "max_price" in patch:
                        if patch["min_price"] >= patch["max_price"]:
                            # descarta min se invertido
                            patch.pop("min_price", None)
                # preço não altera automaticamente sem briefing de preço fora da faixa; manter
                if patch:
                    # valida regex antes
                    if "include_pattern" in patch:
                        try:
                            re.compile(patch["include_pattern"], re.I)
                        except re.error:
                            patch.pop("include_pattern", None)
                    if patch:
                        # atualiza produto via save_config merge
                        cfg2 = olx_scraper.load_config()
                        idx = next((i for i, p in enumerate(cfg2.get("products", [])) if p["id"] == draft_pid), None)
                        if idx is not None:
                            base = dict(cfg2["products"][idx])
                            base.update(patch)
                            # normaliza mantendo id
                            other_ids = set(p["id"] for i, p in enumerate(cfg2["products"]) if i != idx)
                            normalized = olx_scraper._normalize_product(base, other_ids)
                            normalized["id"] = draft_pid
                            normalized["enabled"] = False
                            normalized["schedule"]["enabled"] = False
                            cfg2["products"][idx] = normalized
                            olx_scraper.save_config({"products": cfg2["products"]})
                            iteration["applied"] = True
                            _log(f"iter{it} refinamento aplicado: {patch}")
                req["updated_at"] = datetime.now(timezone.utc).isoformat()
                olx_scraper.upsert_tuner_request(req)
                # se não houve patch mas ainda tem iterações restantes, continua para re-avaliar
            else:
                # sem proposta, mas ainda não convergiu e ainda tem iterações -> continua sem alteração (pode ser amostra pequena)
                req["updated_at"] = datetime.now(timezone.utc).isoformat()
                olx_scraper.upsert_tuner_request(req)
            # se última iteração e não convergiu
            if it == max_iters:
                req = olx_scraper.get_tuner_request(tuner_id) or req
                req["status"] = "aguardando_aprovacao"
                req["updated_at"] = datetime.now(timezone.utc).isoformat()
                # final report com revisao manual
                cfg_final = olx_scraper.load_config()
                prod_final = next((p for p in cfg_final.get("products", []) if p["id"] == draft_pid), prod)
                last_prec = iterations[-1].get("precision", 0) if iterations else 0
                recomendacao = "ativar" if last_prec >= threshold else "revisao_manual"
                # classifica final novamente para ofertas finais
                deals_final = [d for d in _load_deals() if d.get("product_id") == draft_pid and d.get("matched")]
                # tente LLM, fallback para heurística
                try:
                    classified_final, _ = olx_scraper.tuner_classify_deals(briefing, deals_final[:30])
                except Exception:
                    classified_final = olx_scraper.tuner_classify_deals(deals_final[:30], briefing)
                if isinstance(classified_final, tuple):
                    classified_final = classified_final[0]
                req["recommendation"] = recomendacao
                req["final_config"] = prod_final
                req["final_report"] = {
                    "briefing": briefing,
                    "evolucao": iterations,
                    "config_final": prod_final,
                    "ofertas_finais": [{"title": d.get("title"), "price": d.get("price"), "url": d.get("url")} for d in classified_final.get("tp", [])[:10]],
                    "precision_final": last_prec,
                    "recomendacao": recomendacao,
                    "total_tp": len(classified_final.get("tp", [])),
                    "total_fp": len(classified_final.get("fp", [])),
                }
                olx_scraper.upsert_tuner_request(req)
                _log(f"limite {max_iters} atingido prec={last_prec:.2f} -> {recomendacao}")
                break
        # fim loop, se ainda pendente sem final_report (ex: erro total), seta aguardando
        final_req = olx_scraper.get_tuner_request(tuner_id)
        if final_req and final_req.get("status") == "em_refinamento":
            final_req["status"] = "aguardando_aprovacao"
            if "final_report" not in final_req:
                # gera report mínimo
                cfg_f = olx_scraper.load_config()
                prod_f = next((p for p in cfg_f.get("products", []) if p["id"] == draft_pid), None)
                last_prec = final_req.get("iterations", [{}])[-1].get("precision", 0) if final_req.get("iterations") else 0
                final_req["recommendation"] = "revisao_manual" if last_prec < threshold else "ativar"
                final_req["final_config"] = prod_f
                final_req["final_report"] = {
                    "briefing": briefing,
                    "evolucao": final_req.get("iterations", []),
                    "config_final": prod_f,
                    "ofertas_finais": [],
                    "precision_final": last_prec,
                    "recomendacao": final_req["recommendation"],
                }
            if "final_config" not in final_req and "final_report" in final_req:
                try:
                    final_req["final_config"] = final_req["final_report"].get("config_final")
                except Exception:
                    pass
            final_req["updated_at"] = datetime.now(timezone.utc).isoformat()
            olx_scraper.upsert_tuner_request(final_req)
    except Exception as e:
        _append_log(f"tuner {tuner_id} erro fatal: {e} {traceback.format_exc()[-1000:]}")
        try:
            req = olx_scraper.get_tuner_request(tuner_id)
            if req:
                req["status"] = "aguardando_aprovacao"
                req["updated_at"] = datetime.now(timezone.utc).isoformat()
                req["error"] = str(e)[:500]
                olx_scraper.upsert_tuner_request(req)
        except Exception:
            pass


@app.route("/api/tuner/requests", methods=["POST"])
def api_tuner_create():
    data = request.get_json(force=True) or {}
    errors = _validate_tuner_payload(data)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    briefing = _tuner_normalize_briefing(data)
    cfg = olx_scraper.load_config()
    existing_ids = set(p["id"] for p in cfg.get("products", []))
    # cria draft
    draft = _tuner_create_draft_product(briefing, existing_ids)
    # persiste draft via save_config
    products = cfg.get("products", []) + [draft]
    try:
        olx_scraper.save_config({"products": products})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    try:
        reschedule_all()
    except Exception:
        pass
    # cria pedido
    now = datetime.now(timezone.utc).isoformat()
    rid = f"tuner-{olx_scraper._slugify(briefing['descricao'])[:20]}-{uuid.uuid4().hex[:6]}"
    entry = {
        "id": rid,
        "created_at": now,
        "updated_at": now,
        "status": "pendente",
        "briefing": briefing,
        "draft_product_id": draft["id"],
        "draft_product": draft,
        "iterations": [],
        "recommendation": None,
        "final_report": None,
        "final_config": None,
        "history_deals_ref": draft["id"],
    }
    olx_scraper.upsert_tuner_request(entry)
    _append_log(f"tuner criado {rid} draft={draft['id']} queries={draft['queries']}")
    # dispara refinamento em background
    # marca pendente -> em_refinamento via thread
    entry["status"] = "em_refinamento"
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    olx_scraper.upsert_tuner_request(entry)
    t = threading.Thread(target=_run_tuner, args=(rid,), daemon=True)
    t.start()
    return jsonify({"ok": True, "request": entry, "draft": draft}), 201


@app.route("/api/tuner/requests", methods=["GET"])
def api_tuner_list():
    reqs = olx_scraper.load_tuner_requests()
    # filtros: status, q, product_id, sort, limit/offset
    status_q = (request.args.get("status") or "").strip()
    q = (request.args.get("q") or "").strip().lower()
    product_id = (request.args.get("product_id") or "").strip()
    sort = (request.args.get("sort") or "created_at").strip()
    order = (request.args.get("order") or "desc").strip()
    limit = request.args.get("limit")
    offset = request.args.get("offset")
    filtered = reqs
    if status_q and status_q != "all":
        if status_q not in ("pendente", "em_refinamento", "aguardando_aprovacao", "ativado", "descartado"):
            return jsonify({"ok": False, "error": "status inválido"}), 400
        filtered = [r for r in filtered if r.get("status") == status_q]
    if product_id and product_id != "all":
        filtered = [r for r in filtered if r.get("draft_product_id") == product_id]
    if q:
        filtered = [r for r in filtered if q in f"{r.get('briefing', {}).get('descricao','')} {r.get('briefing', {}).get('definicao','')} {r.get('id','')} {r.get('draft_product_id','')}".lower()]
    # sort
    reverse = (order == "desc")
    if sort in ("created_at", "updated_at"):
        filtered = sorted(filtered, key=lambda r: r.get(sort) or "", reverse=reverse)
    elif sort == "status":
        filtered = sorted(filtered, key=lambda r: r.get("status") or "", reverse=reverse)
    else:
        # plan says sort only created_at; fallback to created_at
        filtered = sorted(filtered, key=lambda r: r.get("created_at") or "", reverse=reverse)
    total = len(filtered)
    try:
        off = int(offset) if offset is not None else 0
        lim = int(limit) if limit is not None else 20
        off = max(0, off)
        lim = max(1, min(100, lim))
        filtered = filtered[off: off+lim]
    except Exception:
        return jsonify({"ok": False, "error": "limit/offset inválidos"}), 400
    return jsonify({"total": total, "count": len(filtered), "requests": filtered})


@app.route("/api/tuner/requests/<id>", methods=["GET"])
def api_tuner_get(id):
    req = olx_scraper.get_tuner_request(id)
    if not req:
        return jsonify({"ok": False, "error": "pedido não encontrado"}), 404
    # inclui draft product atual para conveniência
    cfg = olx_scraper.load_config()
    draft = next((p for p in cfg.get("products", []) if p["id"] == req.get("draft_product_id")), None)
    out = dict(req)
    out["draft_product_current"] = draft
    return jsonify({"ok": True, "request": out})


@app.route("/api/tuner/requests/<id>/action", methods=["POST"])
def api_tuner_action(id):
    req = olx_scraper.get_tuner_request(id)
    if not req:
        return jsonify({"ok": False, "error": "pedido não encontrado"}), 404
    body = request.get_json(force=True) or {}
    action = str(body.get("action") or body.get("acao") or "").strip().lower()
    if action not in ("activate", "ativar", "keep_draft", "manter", "discard", "descartar"):
        return jsonify({"ok": False, "error": "action deve ser activate | keep_draft | discard"}), 400
    draft_pid = req.get("draft_product_id")
    cfg = olx_scraper.load_config()
    prod = next((p for p in cfg.get("products", []) if p["id"] == draft_pid), None) if draft_pid else None
    if action in ("activate", "ativar"):
        if not prod:
            return jsonify({"ok": False, "error": "draft não encontrado"}), 404
        # habilita produto e agenda, promove rascunho (remove flag draft e prefixo legacy)
        idx = next((i for i, p in enumerate(cfg["products"]) if p["id"] == draft_pid), None)
        if idx is not None:
            cfg["products"][idx]["enabled"] = True
            cfg["products"][idx]["draft"] = False
            # limpa prefixo legacy [DRAFT] se ainda existir (migração)
            _nm = str(cfg["products"][idx].get("name") or "").strip()
            if _nm.startswith("[DRAFT]"):
                cfg["products"][idx]["name"] = _nm[len("[DRAFT]"):].strip().lstrip("- ").strip()[:80] or _nm
            if "schedule" not in cfg["products"][idx]:
                cfg["products"][idx]["schedule"] = dict(olx_scraper.DEFAULT_SCHEDULE)
            cfg["products"][idx]["schedule"]["enabled"] = True
            olx_scraper.save_config({"products": cfg["products"]})
            try:
                reschedule_all()
            except Exception:
                pass
        req["status"] = "ativado"
        req["updated_at"] = datetime.now(timezone.utc).isoformat()
        olx_scraper.upsert_tuner_request(req)
        _append_log(f"tuner {id} ativado draft={draft_pid}")
        return jsonify({"ok": True, "request": req, "product": cfg["products"][idx] if idx is not None else None})
    elif action in ("discard", "descartar"):
        # remove draft
        if prod:
            new_products = [p for p in cfg["products"] if p["id"] != draft_pid]
            # não permite remover último produto via save_config diretamente se for último? mas draft pode ser último? Força mantendo 1
            if not new_products:
                # cria produto dummy para não violar save_config
                new_products = [dict(olx_scraper.DEFAULT_PRODUCT)]
            olx_scraper.save_config({"products": new_products})
            try:
                reschedule_all()
            except Exception:
                pass
        req["status"] = "descartado"
        req["updated_at"] = datetime.now(timezone.utc).isoformat()
        olx_scraper.upsert_tuner_request(req)
        _append_log(f"tuner {id} descartado draft={draft_pid}")
        return jsonify({"ok": True, "request": req})
    else:  # keep_draft / manter
        req["status"] = "aguardando_aprovacao"
        req["updated_at"] = datetime.now(timezone.utc).isoformat()
        olx_scraper.upsert_tuner_request(req)
        _append_log(f"tuner {id} mantido como rascunho draft={draft_pid}")
        return jsonify({"ok": True, "request": req})


@app.route("/api/tuner/requests/<id>/refine", methods=["POST"])
def api_tuner_refine(id):
    req = olx_scraper.get_tuner_request(id)
    if not req:
        return jsonify({"ok": False, "error": "pedido não encontrado"}), 404
    if req.get("status") == "em_refinamento":
        return jsonify({"ok": False, "error": "já em refinamento"}), 409
    req["status"] = "em_refinamento"
    req["updated_at"] = datetime.now(timezone.utc).isoformat()
    olx_scraper.upsert_tuner_request(req)
    t = threading.Thread(target=_run_tuner, args=(id,), daemon=True)
    t.start()
    return jsonify({"ok": True, "request": req})


@app.route("/api/notified", methods=["GET"])
def api_notified_get():
    try:
        reg = olx_scraper.load_notified()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "count": len(reg), "file": olx_scraper.NOTIFIED_FILE})


@app.route("/api/notified", methods=["DELETE"])
def api_notified_clear():
    """Limpa todo o registro de notificações (notified.json)."""
    try:
        n = olx_scraper.clear_notified()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    _append_log(f"registro de notificações limpo ({n} entradas removidas)")
    return jsonify({"ok": True, "cleared": n})


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    body = {}
    if request.is_json:
        try:
            body = request.get_json(force=True) or {}
        except Exception:
            body = {}
    # suporta product_ids (lista) ou product_id (string) ou sem filtro (todos)
    # Fase 2: suporta ?queue=1 ou body.queue=true para enfileirar em vez de 409
    wants_queue = False
    if request.args.get("queue") in ("1", "true", "yes"):
        wants_queue = True
    if body.get("queue") is True or body.get("enqueue") is True:
        wants_queue = True
    params = {}
    if "product_ids" in body:
        v = body["product_ids"]
        if isinstance(v, str):
            v = [v]
        if isinstance(v, list):
            params["product_ids"] = [str(x).strip() for x in v if str(x).strip()]
    elif "product_id" in body and body["product_id"]:
        params["product_ids"] = [str(body["product_id"]).strip()]
    if "queries" in body:
        return jsonify({"ok": False, "error": "Use /api/products para gerenciar queries por produto. Envie product_ids em vez de queries."}), 400
    if "playwright_only" in body:
        params["playwright_only"] = bool(body["playwright_only"])
    # copia source se vier (schedule vs manual)
    if body.get("source"):
        params["source"] = str(body["source"])

    with job_lock:
        is_running = job["running"]
    if is_running:
        if wants_queue:
            pos = _queue_params(params)
            _append_log(f"api/scrape: enfileirado (queue={pos}) params={params}")
            return jsonify({"ok": True, "queued": True, "position": pos, "params": params, "queue_length": pos}), 202
        return jsonify({"ok": False, "error": "Já há uma busca em andamento", "job": dict(job), "hint": "Use ?queue=1 ou body {queue:true} para enfileirar"}), 409

    _reset_job(params)
    t = threading.Thread(target=_run_scrape, args=(params,), daemon=True)
    t.start()
    return jsonify({"ok": True, "job": {"running": True, "params": params, "started_at": job["started_at"]}})


@app.route("/api/scrape/status", methods=["GET"])
def api_scrape_status():
    with job_lock:
        j = dict(job)
        j["logs"] = list(j["logs"])
        j["progress"] = dict(j["progress"])
    if request.args.get("logs") == "0":
        j.pop("logs", None)
    return jsonify(j)


@app.route("/api/scrape/logs", methods=["GET"])
def api_scrape_logs():
    since = request.args.get("since")
    try:
        since = int(since) if since is not None else 0
    except Exception:
        since = 0
    with job_lock:
        logs = list(job["logs"])
    if since < 0:
        since = 0
    return jsonify({"total": len(logs), "since": since, "logs": logs[since:]})


@app.route("/api/login", methods=["POST"])
def api_login():
    with job_lock:
        if job["running"]:
            return jsonify({"ok": False, "error": "Busca em andamento; tente após terminar"}), 409

    def _do():
        _reset_job({"action": "login"})
        with job_lock:
            job["running"] = True
        try:
            _append_log("Login: capturando cookies via Playwright...")
            cfg = olx_scraper.load_config()
            prods = cfg.get("products") or []
            search_url = prods[0].get("search_url", olx_scraper.SEARCH_URL) if prods else olx_scraper.SEARCH_URL
            ok = olx_scraper.playwright_login(search_url=search_url)
            _append_log(f"Login {'OK' if ok else 'sem cards mas cookies salvos'}")
            with job_lock:
                job["results_count"] = 1 if ok else 0
        except Exception as e:
            tb = traceback.format_exc()
            _append_log(f"Login erro: {e}")
            _append_log(tb[-1500:])
            with job_lock:
                job["error"] = str(e)
        finally:
            with job_lock:
                job["running"] = False
                job["finished_at"] = datetime.now(timezone.utc).isoformat()

    t = threading.Thread(target=_do, daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/cookies/status", methods=["GET"])
def api_cookies_status():
    return jsonify(_cookies_status())


@app.route("/api/schedules/status", methods=["GET"])
def api_schedules_status():
    cfg = olx_scraper.load_config()
    sched = _get_scheduler()
    jobs_by_id = {}
    if sched is not None and HAS_SCHEDULER:
        try:
            for j in sched.get_jobs():
                jobs_by_id[j.id] = j
        except Exception:
            pass
    # carrega last_runs persistidos (Fase 2) + historico Tier 1 (deriva last_run quando existir)
    try:
        sched_data = olx_scraper.load_schedules()
        last_runs = sched_data.get("last_runs", {})
    except Exception:
        last_runs = {}
    try:
        runs_hist = olx_scraper.load_runs()
    except Exception:
        runs_hist = []
    # sort runs desc por started_at para derivacao eficiente
    try:
        runs_sorted = sorted(runs_hist, key=lambda x: x.get("started_at", ""), reverse=True)
    except Exception:
        runs_sorted = list(reversed(runs_hist))
    schedules = []
    for p in cfg.get("products", []):
        s = p.get("schedule") or {}
        j = jobs_by_id.get(p["id"])
        next_run = None
        if j is not None:
            try:
                nrt = getattr(j, "next_run_time", None)
                if nrt:
                    next_run = nrt.isoformat() if hasattr(nrt, "isoformat") else str(nrt)
            except Exception:
                next_run = None
        interval = s.get("interval_minutes")
        pid = p["id"]
        # tenta derivar do historico runs.json
        last_from_runs = None
        for r in runs_sorted:
            pids_scraped = r.get("product_ids_scraped") or []
            pids_requested = r.get("product_ids_requested") or []
            if pid in pids_scraped or pid in pids_requested:
                last_from_runs = r
                break
        if last_from_runs:
            last_run_val = last_from_runs.get("finished_at") or last_from_runs.get("started_at")
            last_status_val = last_from_runs.get("status")
            last_error_val = last_from_runs.get("error")
            per = (last_from_runs.get("per_product") or {}).get(pid, {}) if isinstance(last_from_runs.get("per_product"), dict) else {}
            # per may have matched/total_cards
            last_matched_val = per.get("matched") if isinstance(per, dict) and "matched" in per else None
            last_results_val = per.get("total_cards") if isinstance(per, dict) and "total_cards" in per else None
            # fallback to counts aggregated if per empty and only one product in run
            if last_matched_val is None and len(last_from_runs.get("product_ids_scraped") or []) == 1:
                cnt = last_from_runs.get("counts") or {}
                last_matched_val = cnt.get("matched")
                last_results_val = cnt.get("total_cards")
            schedules.append({
                "product_id": p["id"],
                "product_name": p["name"],
                "enabled": bool(s.get("enabled")),
                "mode": s.get("mode", "interval"),
                "interval_minutes": interval,
                "cron": s.get("cron"),
                "jitter_minutes": s.get("jitter_minutes", 0),
                "timezone": s.get("timezone", "America/Sao_Paulo"),
                "next_run": next_run,
                "last_run": last_run_val,
                "last_status": last_status_val,
                "last_error": last_error_val,
                "last_matched": last_matched_val,
                "last_results": last_results_val,
                "product_enabled": bool(p.get("enabled", True)),
            })
        else:
            last = last_runs.get(pid)
            schedules.append({
                "product_id": p["id"],
                "product_name": p["name"],
                "enabled": bool(s.get("enabled")),
                "mode": s.get("mode", "interval"),
                "interval_minutes": interval,
                "cron": s.get("cron"),
                "jitter_minutes": s.get("jitter_minutes", 0),
                "timezone": s.get("timezone", "America/Sao_Paulo"),
                "next_run": next_run,
                "last_run": last.get("at") if isinstance(last, dict) else None,
                "last_status": last.get("status") if isinstance(last, dict) else None,
                "last_error": last.get("error") if isinstance(last, dict) else None,
                "last_matched": last.get("matched_count") if isinstance(last, dict) else None,
                "last_results": last.get("results_count") if isinstance(last, dict) else None,
                "product_enabled": bool(p.get("enabled", True)),
            })
    with job_lock:
        # queue contém dicts; serializa para resposta
        queue_snapshot = []
        for item in list(schedule_queue):
            if isinstance(item, dict):
                queue_snapshot.append(item)
            elif isinstance(item, str):
                queue_snapshot.append({"product_ids": [item], "source": "schedule"})
            else:
                queue_snapshot.append(str(item))
        running_info = {"running": job["running"], "params": job.get("params", {})}
    sched_running = False
    try:
        sched_running = bool(sched and sched.running) if HAS_SCHEDULER else False
    except Exception:
        sched_running = False
    return jsonify({
        "ok": True,
        "scheduler_running": sched_running,
        "has_scheduler": HAS_SCHEDULER,
        "queue": queue_snapshot,
        "queue_length": len(queue_snapshot),
        "job": running_info,
        "schedules": schedules,
    })


# --- Runs Tier 1 (7a) ---
@app.route("/api/runs", methods=["GET"])
def api_list_runs():
    """Lista runs com paginacao e filtros (product_id, status, source, since/until, sort/order, limit/offset)."""
    try:
        runs = olx_scraper.load_runs()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    product_id = (request.args.get("product_id") or "").strip()
    status_filter = (request.args.get("status") or "").strip()
    source_filter = (request.args.get("source") or "").strip()
    since_str = (request.args.get("since") or "").strip()
    until_str = (request.args.get("until") or "").strip()
    sort = (request.args.get("sort") or "started_at").strip()
    order = (request.args.get("order") or "desc").strip()
    limit_str = request.args.get("limit")
    offset_str = request.args.get("offset")

    # validacao filtros
    if status_filter and status_filter not in ("ok", "error"):
        return jsonify({"ok": False, "error": "status deve ser ok ou error"}), 400
    if source_filter and source_filter not in ("manual", "schedule", "trigger", "api", "queue"):
        # permite qualquer source mas valida conhecidos; se desconhecido filtra literal
        pass
    since_dt = None
    if since_str:
        # Flask decodes '+' as space in query string; restaura para ISO
        if " " in since_str and "+" not in since_str:
            since_str = since_str.replace(" ", "+")
        since_dt = _parse_iso(since_str)
        if since_dt is None:
            return jsonify({"ok": False, "error": "since inválido (ISO esperado)"}), 400
        if since_dt.tzinfo is None:
            since_dt = since_dt.replace(tzinfo=timezone.utc)
    until_dt = None
    if until_str:
        if " " in until_str and "+" not in until_str:
            until_str = until_str.replace(" ", "+")
        until_dt = _parse_iso(until_str)
        if until_dt is None:
            return jsonify({"ok": False, "error": "until inválido (ISO esperado)"}), 400
        if until_dt.tzinfo is None:
            until_dt = until_dt.replace(tzinfo=timezone.utc)
    if sort not in ("started_at", "duration", "duration_ms"):
        return jsonify({"ok": False, "error": "sort deve ser started_at ou duration"}), 400
    if order not in ("asc", "desc"):
        return jsonify({"ok": False, "error": "order deve ser asc ou desc"}), 400

    filtered = runs
    if product_id and product_id != "all":
        # filtra onde produto aparece em scraped ou requested
        tmp = []
        for r in filtered:
            pids = set((r.get("product_ids_scraped") or []) + (r.get("product_ids_requested") or []))
            if product_id in pids:
                tmp.append(r)
        filtered = tmp
    if status_filter:
        filtered = [r for r in filtered if r.get("status") == status_filter]
    if source_filter:
        filtered = [r for r in filtered if r.get("source") == source_filter]
    if since_dt is not None:
        tmp = []
        for r in filtered:
            dt = _parse_iso(r.get("started_at"))
            if dt is None:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= since_dt:
                tmp.append(r)
        filtered = tmp
    if until_dt is not None:
        tmp = []
        for r in filtered:
            dt = _parse_iso(r.get("started_at"))
            if dt is None:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt <= until_dt:
                tmp.append(r)
        filtered = tmp

    reverse = (order == "desc")
    if sort == "started_at":
        try:
            filtered = sorted(filtered, key=lambda d: (_parse_iso(d.get("started_at")) or datetime.min.replace(tzinfo=timezone.utc)), reverse=reverse)
        except Exception:
            filtered = sorted(filtered, key=lambda d: d.get("started_at", ""), reverse=reverse)
    elif sort in ("duration", "duration_ms"):
        filtered = sorted(filtered, key=lambda d: int(d.get("duration_ms") or 0), reverse=reverse)

    total = len(filtered)
    # paginacao
    try:
        offset = int(offset_str) if offset_str is not None else 0
    except Exception:
        return jsonify({"ok": False, "error": "offset deve ser inteiro >=0"}), 400
    if offset < 0:
        return jsonify({"ok": False, "error": "offset deve ser >=0"}), 400
    try:
        limit = int(limit_str) if limit_str is not None else 50
    except Exception:
        return jsonify({"ok": False, "error": "limit deve ser inteiro 1-200"}), 400
    if not (1 <= limit <= 200):
        return jsonify({"ok": False, "error": "limit deve ser 1-200"}), 400

    filtered = filtered[offset: offset + limit] if limit else filtered[offset:]
    return jsonify({"total": total, "count": len(filtered), "runs": filtered})


@app.route("/api/runs/<id>", methods=["GET"])
def api_get_run(id):
    try:
        run = olx_scraper.get_run(id)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    if not run:
        return jsonify({"ok": False, "error": "run não encontrado"}), 404
    return jsonify({"ok": True, "run": run})


@app.route("/api/runs/<id>", methods=["DELETE"])
def api_delete_run_by_id(id):
    # variante futura single-delete (Tier 1 opcional)
    try:
        run = olx_scraper.get_run(id)
        if not run:
            return jsonify({"ok": False, "error": "run não encontrado"}), 404
        # delete via before+product? Simplifica: remove apenas esse id
        with olx_scraper._runs_lock():
            runs = olx_scraper._read_runs_file()
            keep = [r for r in runs if r.get("id") != id]
            removed = len(runs) - len(keep)
            if removed:
                olx_scraper._write_runs_file(keep)
        return jsonify({"ok": True, "cleared": removed})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/runs", methods=["DELETE"])
def api_clear_runs():
    before = (request.args.get("before") or "").strip() or None
    if before and " " in before and "+" not in before:
        before = before.replace(" ", "+")
    product_id = (request.args.get("product_id") or "").strip() or None
    status = (request.args.get("status") or "").strip() or None
    source = (request.args.get("source") or "").strip() or None
    # validacao: ao menos um filtro
    if not before and not product_id and not status and not source:
        return jsonify({"ok": False, "error": "informe before ou filtro (product_id/status/source)"}), 400
    if status and status not in ("ok", "error"):
        return jsonify({"ok": False, "error": "status deve ser ok ou error"}), 400
    if before:
        if _parse_iso(before) is None:
            return jsonify({"ok": False, "error": "before inválido (ISO esperado)"}), 400
    try:
        cleared = olx_scraper.delete_runs(before=before, product_id=product_id, status=status, source=source)
    except ValueError as ve:
        return jsonify({"ok": False, "error": str(ve)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "cleared": cleared})


@app.route("/api/schedules/trigger/<pid>", methods=["POST"])
def api_schedule_trigger(pid):
    """Dispara busca agendada manual imediata para um produto (Fase 2).

    Query ?queue=1 ou body {queue:true} enfileira se ocupado; senão retorna 409.
    """
    cfg = olx_scraper.load_config()
    prod = next((p for p in cfg.get("products", []) if p["id"] == pid), None)
    if not prod:
        return jsonify({"ok": False, "error": "produto não encontrado"}), 404
    body = {}
    if request.is_json:
        try:
            body = request.get_json(force=True) or {}
        except Exception:
            body = {}
    wants_queue = False
    if request.args.get("queue") in ("1", "true", "yes") or body.get("queue") is True or body.get("enqueue") is True:
        wants_queue = True
    params = {"product_ids": [pid], "source": "trigger"}
    if body.get("playwright_only"):
        params["playwright_only"] = True
    with job_lock:
        is_running = job["running"]
    if is_running:
        if wants_queue:
            pos = _queue_params(params)
            _append_log(f"api/trigger {pid}: enfileirado fila={pos}")
            return jsonify({"ok": True, "queued": True, "position": pos, "params": params}), 202
        return jsonify({"ok": False, "error": "Já há busca em andamento", "hint": "Use ?queue=1 para enfileirar"}), 409
    _reset_job(params)
    _append_log(f"api/trigger: disparo manual {pid}")
    t = threading.Thread(target=_run_scrape, args=(params,), daemon=True)
    t.start()
    return jsonify({"ok": True, "job": {"running": True, "params": params}})


@app.route("/api/schedules/queue", methods=["GET", "DELETE"])
def api_schedules_queue():
    """Fase 2: inspeção e limpeza da fila."""
    if request.method == "GET":
        with job_lock:
            q = list(schedule_queue)
        # serializa
        out = []
        for item in q:
            if isinstance(item, dict):
                out.append(item)
            else:
                out.append({"product_ids": [str(item)]})
        return jsonify({"ok": True, "queue": out, "length": len(out)})
    else:  # DELETE -> limpar fila
        with job_lock:
            n = len(schedule_queue)
            schedule_queue.clear()
        _append_log(f"schedule: fila limpa (removidos {n})")
        return jsonify({"ok": True, "cleared": n})


@app.route("/api/schedules/run", methods=["POST"])
def api_schedules_run_now():
    """Dispara produtos específicos imediatamente (alias genérico para POST /api/scrape com queue)."""
    body = request.get_json(force=True) or {}
    pids = body.get("product_ids") or ([body.get("product_id")] if body.get("product_id") else None)
    if pids:
        if isinstance(pids, str):
            pids = [pids]
        pids = [str(x).strip() for x in pids if str(x).strip()]
    params = {}
    if pids:
        params["product_ids"] = pids
    if body.get("playwright_only"):
        params["playwright_only"] = True
    params["source"] = body.get("source") or "trigger"
    wants_queue = bool(body.get("queue") or body.get("enqueue"))
    if request.args.get("queue") in ("1", "true", "yes"):
        wants_queue = True
    with job_lock:
        is_running = job["running"]
    if is_running:
        if wants_queue:
            pos = _queue_params(params)
            return jsonify({"ok": True, "queued": True, "position": pos, "params": params}), 202
        return jsonify({"ok": False, "error": "Já há busca em andamento"}), 409
    _reset_job(params)
    t = threading.Thread(target=_run_scrape, args=(params,), daemon=True)
    t.start()
    return jsonify({"ok": True, "job": {"running": True, "params": params}})


@app.route("/api/config/telegram/test", methods=["POST"])
def api_telegram_test():
    body = request.get_json(force=True) or {}
    cfg = olx_scraper.load_config()
    token = body.get("token")
    if _looks_masked(token):
        token = None  # mascarado = "use o armazenado"
    token = token or cfg.get("telegram_token")
    chat_id = body.get("chat_id") or cfg.get("telegram_chat_id")
    if not token or not chat_id:
        return jsonify({"ok": False, "error": "token/chat_id ausentes"}), 400
    try:
        from curl_cffi import requests as cf
        r = cf.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": "✓ Teste olx-deals: notificação funcionando!"},
            timeout=15,
        )
        if r.status_code == 200:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": f"HTTP {r.status_code}", "body": r.text[:500]}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# --- OpenAPI + Swagger UI (PLANO-OPENAPI-MCP Fase 2) ---
@app.route("/api/openapi.yaml")
def api_openapi_yaml():
    return send_from_directory(BASE_DIR, "openapi.yaml", mimetype="application/yaml")


SWAGGER_HTML = """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <title>olx-deals API — Swagger UI</title>
  <link rel="stylesheet" href="/static/swagger-ui/swagger-ui.css">
  <link rel="icon" type="image/png" href="/static/swagger-ui/favicon-32x32.png">
  <style>body{margin:0}#swagger{max-width:none}</style>
</head>
<body>
<div id="swagger"></div>
<script src="/static/swagger-ui/swagger-ui-bundle.js"></script>
<script>
SwaggerUIBundle({
  url: "/api/openapi.yaml",
  dom_id: "#swagger",
  deepLinking: true,
  tryItOutEnabled: true,
});
</script>
</body>
</html>
"""


@app.route("/docs")
def swagger_docs():
    return SWAGGER_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}


# --- frontend ---
@app.route("/")
def index():
    tmpl = os.path.join(app.template_folder, "index.html")
    if os.path.exists(tmpl):
        return send_from_directory(app.template_folder, "index.html")
    return "index.html não encontrado. Verifique templates/index.html", 404


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)


# Fase 5: factory fino — alvo ~150 LOC (S5 mantém shim compat, S6 removerá legado)
# Expõe create_app() para testes e para geração openapi.yaml a partir de schemas Pydantic.
# Mantém `app` global para compat com `from app import app` legado.
try:  # pragma: no cover
    from olx_deals.interfaces.api import create_app as _new_create_app  # type: ignore

    def create_app(config=None):  # type: ignore[no-redef]
        """Factory Fase 5: delega para interfaces.api.create_app quando disponível."""
        try:
            return _new_create_app(test_config=config if isinstance(config, dict) else None)
        except TypeError:
            return _new_create_app()
except Exception:  # pragma: no cover

    def create_app(config=None):  # type: ignore[no-redef]
        return app


def _generate_openapi_from_schemas():  # helper para `python -m olx_deals.interfaces.api.schemas`
    """Gera fragmento openapi a partir de schemas Pydantic (Fase 5)."""
    try:
        from olx_deals.interfaces.api.schemas import generate_openapi_from_schemas as _gen
        return _gen()
    except Exception as e:
        return {"error": str(e)}


def main():
    p = argparse.ArgumentParser(description="OLX Deals GUI")
    p.add_argument("--host", default=os.environ.get("OLX_GUI_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("OLX_GUI_PORT", "8765")))
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()
    print(f"[olx-gui] http://{args.host}:{args.port}  (templates={app.template_folder}, static={app.static_folder})")
    # inicia scheduler (Fase 1)
    if HAS_SCHEDULER:
        try:
            sched = _get_scheduler()
            if sched is not None and not sched.running:
                sched.start()
                print("[olx-gui] scheduler APScheduler iniciado (America/Sao_Paulo)")
                reschedule_all()
                try:
                    jobs = sched.get_jobs()
                    print(f"[olx-gui] scheduler: {len(jobs)} job(s) agendado(s)")
                except Exception:
                    pass
        except Exception as e:
            print(f"[olx-gui] scheduler falhou ao iniciar: {e}")
    else:
        print("[olx-gui] APScheduler não instalado — agendamento por produto desabilitado (pip install APScheduler)")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
