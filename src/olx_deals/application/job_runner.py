"""application.job_runner — job state, queue, _run_scrape.
Extraído de app.py:61-515. Mantém compat via re-export em app.py.
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Dict, Any

# estado global — mesmas instâncias compartilhadas com app.py via import lazy
job_lock = threading.RLock()
job: Dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "logs": [],
    "progress": {},
    "results_count": 0,
    "matched_count": 0,
    "error": None,
    "params": {},
}
MAX_LOG_LINES = 800
schedule_queue: deque = deque()
scheduler = None
HAS_SCHEDULER = True
try:
    from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore
except ImportError:  # pragma: no cover
    BackgroundScheduler = None  # type: ignore
    HAS_SCHEDULER = False


def _get_cfg_repo():
    try:
        import olx_scraper as _os
        return getattr(_os, "_cfg_repo", None)
    except Exception:
        return None


def _get_deals_repo():
    try:
        import olx_scraper as _os
        return getattr(_os, "_deals_repo", None)
    except Exception:
        return None


def _get_runs_repo():
    try:
        import olx_scraper as _os
        return getattr(_os, "_runs_repo", None)
    except Exception:
        return None


def _get_sched_repo():
    try:
        import olx_scraper as _os
        return getattr(_os, "_schedules_repo", None)
    except Exception:
        return None


def _append_log(msg: str):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    with job_lock:
        job["logs"].append(line)
        if len(job["logs"]) > MAX_LOG_LINES:
            job["logs"] = job["logs"][-MAX_LOG_LINES:]
    print(line, flush=True)


def _reset_job(params):
    with job_lock:
        job["running"] = True
        job["started_at"] = datetime.now(timezone.utc).isoformat()
        job["finished_at"] = None
        job["logs"] = []
        job["progress"] = {"current_product": None, "current_query": None, "current_page": None, "total_products": 0, "total_queries": 0, "total_pages": 0}
        job["results_count"] = 0
        job["matched_count"] = 0
        job["error"] = None
        job["params"] = params or {}


def _queue_params(params: dict) -> int:
    with job_lock:
        schedule_queue.append(dict(params))
        return len(schedule_queue)


def _drain_queue():
    next_params = None
    with job_lock:
        if schedule_queue:
            next_params = schedule_queue.popleft()
    if next_params is not None:
        if isinstance(next_params, str):
            next_params = {"product_ids": [next_params], "source": "schedule"}
        elif isinstance(next_params, dict) and "product_ids" not in next_params and "product_id" in next_params:
            next_params = {"product_ids": [next_params["product_id"]], "source": next_params.get("source", "queue")}
        _append_log(f"schedule: drenando fila -> {next_params.get('product_ids') or next_params}")
        try:
            _reset_job(next_params)
            t = threading.Thread(target=_run_scrape, args=(next_params,), daemon=True)
            t.start()
        except Exception as e:
            _append_log(f"schedule: falha ao drenar fila {next_params}: {e}")


def _get_scheduler():
    global scheduler
    if scheduler is None and HAS_SCHEDULER:
        scheduler = BackgroundScheduler(timezone="America/Sao_Paulo", daemon=True)  # type: ignore
    return scheduler


def _parse_iso(dt_str):
    if not dt_str:
        return None
    try:
        s = str(dt_str).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        if " " in s and "+" not in s and "T" in s:
            s = s.replace(" ", "+")
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _run_scrape(params):
    # hardening: implementação única aqui (fonte da verdade). App re-exporta esta função para compat.
    # Não delega para app para quebrar ciclo circular.
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
        else:
            requested = None
    except Exception:
        requested = None

    _products_for_persist = []
    _error_for_persist = None
    try:
        import olx_scraper  # type: ignore
        cfg_repo = _get_cfg_repo()
        cfg = cfg_repo.load() if cfg_repo else olx_scraper.load_config()
        all_products = cfg.get("products", [])
        product_ids = requested
        if product_ids:
            products = [p for p in all_products if p["id"] in product_ids]
            if not products:
                raise ValueError(f"Nenhum produto encontrado para ids: {', '.join(product_ids)}")
        else:
            products = [p for p in all_products if p.get("enabled", True)]
            if not products:
                raise ValueError("Nenhum produto habilitado para busca")
        _products_for_persist = list(products)
        if product_ids:
            products = [dict(p, enabled=True) for p in products]
        force_pw = params.get("playwright_only") if isinstance(params, dict) else None
        if force_pw:
            for p in products:
                p["playwright_only"] = True

        def log_fn(m):
            _append_log(m)

        def progress_cb(info):
            with job_lock:
                job["progress"].update(info)
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
            log_fn(f" - {p['name']} ({p['id']}): {len(p['queries'])} queries, {p['pages']} pág/query")
        stats_out: dict = {}
        results = olx_scraper.scrape(products, log_fn=log_fn, progress_cb=progress_cb, stats_out=stats_out, force_enabled=bool(product_ids))
        matched = [r for r in results if r.get("matched")]
        log_fn(f"=== {len(matched)} anúncios atendem filtro, {len(results)} com preço persistidos ===")
        deals_repo = _get_deals_repo()
        new = deals_repo.save(results) if deals_repo else olx_scraper.save_history(results)
        log_fn(f"=== {len(new)} novos salvos em {olx_scraper.HISTORY_FILE} ===")
        cnt = {"enviados": 0, "pulados": 0, "re_notificados": 0}
        new_matched = [r for r in new if r.get("matched")]
        try:
            cfg2 = cfg_repo.load() if cfg_repo else olx_scraper.load_config()
            draft_ids = {p.get("id") for p in cfg2.get("products", []) if p.get("draft")}
        except Exception:
            draft_ids = set()
        new_matched = [r for r in new_matched if r.get("product_id") not in draft_ids and not str(r.get("product_id", "")).startswith("draft-")]
        if new_matched:
            try:
                cnt = olx_scraper.notify(new_matched)
                msg = (f"Telegram: {cnt.get('enviados', 0)} enviados, {cnt.get('pulados', 0)} já notificados, {cnt.get('re_notificados', 0)} re-notificados")
                if cnt.get("erro"):
                    msg += f" | erro: {cnt['erro']}"
                log_fn(msg)
            except Exception as e:
                log_fn(f"notify error: {e}")
        with job_lock:
            job["results_count"] = len(results)
            job["matched_count"] = len(matched)
            job["progress"]["done"] = True
        finished = datetime.now(timezone.utc)
        duration_ms = int((finished - started).total_seconds() * 1000)
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
        runs_repo = _get_runs_repo()
        try:
            (runs_repo.append(entry) if runs_repo else olx_scraper.append_run(entry))  # type: ignore
        except Exception as pe:
            _append_log(f"runs: falha ao persistir run ok {run_id}: {pe}")
        try:
            at_iso = finished.isoformat()
            sched_repo = _get_sched_repo()
            for prod in products:
                pid = prod["id"]
                per = per_product.get(pid, {}) if isinstance(per_product.get(pid), dict) else {}
                (sched_repo.record(pid, status="ok", at_iso=at_iso, matched_count=int(per.get("matched", 0)), results_count=int(per.get("total_cards", 0))) if sched_repo else olx_scraper.record_schedule_run(pid, status="ok", at_iso=at_iso, matched_count=int(per.get("matched", 0)), results_count=int(per.get("total_cards", 0))))  # type: ignore
        except Exception as pe:
            _append_log(f"schedule: falha ao persistir last_run ok: {pe}")
    except Exception as e:
        tb = traceback.format_exc()
        _append_log(f"ERRO: {e}")
        _append_log(tb[-2000:])
        with job_lock:
            job["error"] = str(e)
        _error_for_persist = str(e)
        try:
            finished = datetime.now(timezone.utc)
            duration_ms = int((finished - started).total_seconds() * 1000)
            error_msg = str(e)[:500]
            product_ids_requested = requested if requested is not None else []
            if _products_for_persist:
                product_ids_scraped = [p["id"] for p in _products_for_persist]
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
            runs_repo = _get_runs_repo()
            try:
                (runs_repo.append(entry_err) if runs_repo else olx_scraper.append_run(entry_err))  # type: ignore
            except Exception as pe2:
                _append_log(f"runs: falha ao persistir run erro {run_id}: {pe2}")
        except Exception as pe:
            _append_log(f"runs: falha ao montar run erro: {pe}")
        try:
            at_iso = datetime.now(timezone.utc).isoformat()
            pids = []
            if _products_for_persist:
                pids = [p["id"] for p in _products_for_persist]
            elif isinstance(params, dict) and params.get("product_ids"):
                pids = [str(x) for x in params["product_ids"]]
            elif requested:
                pids = list(requested)
            sched_repo = _get_sched_repo()
            for pid in pids:
                (sched_repo.record(pid, status="error", error=_error_for_persist, at_iso=at_iso) if sched_repo else olx_scraper.record_schedule_run(pid, status="error", error=_error_for_persist, at_iso=at_iso))  # type: ignore
        except Exception as pe:
            _append_log(f"schedule: falha ao persistir last_run erro: {pe}")
    finally:
        with job_lock:
            job["running"] = False
            job["finished_at"] = datetime.now(timezone.utc).isoformat()
        _drain_queue()
