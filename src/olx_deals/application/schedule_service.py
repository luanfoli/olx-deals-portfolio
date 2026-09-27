"""application.schedule_service — reschedule_all, trigger_product, _get_next_run_for_interval.
Extraído de app.py:182-261.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    import olx_scraper  # type: ignore
except Exception:
    olx_scraper = None  # type: ignore


def _get_cfg_repo():
    try:
        import olx_scraper as _os
        return getattr(_os, "_cfg_repo", None)
    except Exception:
        return None


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


def _get_next_run_for_interval(pid: str, interval_minutes: int):
    try:
        data = olx_scraper.load_schedules()  # type: ignore
        info = data.get("last_runs", {}).get(pid)
        if not info or not info.get("at"):
            return None
        last_dt = _parse_iso(info["at"])
        if not last_dt:
            return None
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        nxt = last_dt + timedelta(minutes=int(interval_minutes))
        now = datetime.now(timezone.utc)
        if nxt < now - timedelta(days=1):
            return None
        return nxt
    except Exception:
        return None


def _get_scheduler():
    # hardening: fonte única é job_runner; sem delegação para app (quebra ciclo)
    try:
        from olx_deals.application.job_runner import _get_scheduler as _jr_get  # type: ignore
        return _jr_get()
    except Exception:
        return None


def reschedule_all():
    """Re-registra jobs APScheduler a partir de load_config() — fonte única (hardening)."""
    # hardening: implementação única aqui; app re-exporta para compat.
    # Não delega para app para quebrar ciclo.
    try:
        from olx_deals.application.job_runner import _get_scheduler as _jr_get, _append_log  # type: ignore
        sched = _jr_get()
        HAS = True
        try:
            from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore
        except Exception:
            HAS = False
            sched = None
    except Exception:
        sched = _get_scheduler()
        HAS = sched is not None
        _append_log = lambda m: print(m)  # type: ignore

    if not HAS or sched is None:
        return
    try:
        if not sched.running:
            sched.start()
            try:
                _append_log("schedule: scheduler iniciado automaticamente")
            except Exception:
                pass
    except Exception:
        pass
    try:
        sched.remove_all_jobs()
    except Exception:
        pass
    # import lazy para evitar ciclo
    try:
        cfg_repo = _get_cfg_repo()
        cfg = cfg_repo.load() if cfg_repo else olx_scraper.load_config()  # type: ignore
    except Exception:
        cfg = {"products": []}
    for p in cfg.get("products", []):
        s = p.get("schedule") or {}
        if not s.get("enabled"):
            continue
        if not p.get("enabled", True):
            continue
        mode = s.get("mode", "interval")
        pid = p["id"]
        try:
            if mode == "interval":
                interval = int(s.get("interval_minutes", 360))
                interval = max(15, min(10080, interval))
                jitter = int(s.get("jitter_minutes", 0) or 0)
                jitter = max(0, min(60, jitter))
                kwargs = dict(id=pid, replace_existing=True, args=[pid], max_instances=1, coalesce=True, misfire_grace_time=600)
                if jitter:
                    kwargs["jitter"] = jitter * 60
                nxt = _get_next_run_for_interval(pid, interval)
                if nxt is not None:
                    kwargs["next_run_time"] = nxt
                    _append_log(f"schedule: agendado {pid} a cada {interval}min" + (f" jitter {jitter}min" if jitter else "") + f" next={nxt.isoformat()}")
                else:
                    _append_log(f"schedule: agendado {pid} a cada {interval}min" + (f" jitter {jitter}min" if jitter else ""))
                sched.add_job(trigger_product, "interval", minutes=interval, **kwargs)
            elif mode == "cron":
                cron_expr = str(s.get("cron") or "0 */6 * * *").strip()
                jitter = int(s.get("jitter_minutes", 0) or 0)
                jitter = max(0, min(60, jitter))
                try:
                    from apscheduler.triggers.cron import CronTrigger  # type: ignore
                    trig = CronTrigger.from_crontab(cron_expr, timezone="America/Sao_Paulo")
                    kwargs = dict(id=pid, replace_existing=True, args=[pid], max_instances=1, coalesce=True, misfire_grace_time=600)
                    if jitter:
                        kwargs["jitter"] = jitter * 60
                    sched.add_job(trigger_product, trig, **kwargs)
                    _append_log(f"schedule: agendado {pid} cron {cron_expr}" + (f" jitter {jitter}min" if jitter else ""))
                except Exception as ce:
                    _append_log(f"schedule: falha cron {pid} ({cron_expr}): {ce}")
        except Exception as e:
            _append_log(f"schedule: falha ao agendar {pid}: {e}")


def trigger_product(pid: str):
    """Disparado pelo APScheduler; fonte única (hardening) — app re-exporta."""
    try:
        from olx_deals.application.job_runner import job, job_lock, schedule_queue, _append_log, _reset_job, _run_scrape  # type: ignore
        import threading
        params = {"product_ids": [pid], "source": "schedule"}
        with job_lock:
            if job["running"]:
                schedule_queue.append(params)
                _append_log(f"schedule: {pid} enfileirado (busy) — fila={len(schedule_queue)}")
                return
        _reset_job(params)
        _append_log(f"schedule: disparando busca agendada para {pid}")
        t = threading.Thread(target=_run_scrape, args=(params,), daemon=True)
        t.start()
    except Exception as e:
        try:
            print(f"trigger fallback error {e}")
        except Exception:
            pass
