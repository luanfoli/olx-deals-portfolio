#!/usr/bin/env python3
"""
olx_scraper.py — busca genérica na OLX Brasil.

Estratégia (2 caminhos):
  1. curl_cffi (impersonação de Chrome TLS) com cookies capturados por Playwright   [rápido]
  2. Fallback: Playwright DOM (navegador real -> resolve Cloudflare)                 [robusto]

Uso:
  python olx_scraper.py --login               # captura cookies
  python olx_scraper.py --scrape --pages 3    # busca todos os produtos

Config genérica: ~/.olx/config.json -> {"products": [...], "telegram_token":..., "telegram_chat_id":...}
Cada produto tem: id, name, queries[], pages, search_url, playwright_only, min_price, max_price, junk_pattern, exclude_keywords[], enabled
"""

import argparse
import fcntl
import json
import os
import random
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

DEFAULT_QUERIES = [
    "hd 6tb", "hd 8tb", "hd 10tb", "hd 12tb",
    "disco 6tb", "disco 8tb", "disco 10tb",
    "wd 8tb", "western digital 8tb", "western digital 10tb",
    "seagate 8tb", "seagate 10tb", "seagate 12tb",
    "hd 6 tb", "hd 8 tb", "hd 10 tb",
]
SEARCH_URL = "https://www.olx.com.br/brasil?q={query}&opst=2&page={page}"
COOKIE_JAR = os.path.expanduser("~/.olx/cookies.json")
STATE_DIR = os.path.expanduser("~/.olx")
HISTORY_FILE = os.path.join(STATE_DIR, "deals.json")
CONFIG_FILE = os.path.join(STATE_DIR, "config.json")
SCHEDULES_FILE = os.path.join(STATE_DIR, "schedules.json")
NOTIFIED_FILE = os.path.join(STATE_DIR, "notified.json")
NOTIFIED_LOCK_FILE = os.path.join(STATE_DIR, ".notified.lock")
NOTIFIED_RETENTION_DAYS = 90
DEFAULT_NOTIFY_PRICE_CHANGE_PCT = 5.0

HISTORY_MAX = 5000
DISCARD_PRICE = "price"
DISCARD_INCLUDE = "include"
DISCARD_JUNK = "junk"
DISCARD_EXCLUDE = "exclude"
DISCARD_DEDUPE = "dedupe"
DISCARD_ORPHAN = "orphan"

PURGE_BACKUP_GLOB = "deals.json.bak-purge-*"

# --- tuner in-app (Backlog #12) ---
TUNER_FILE = os.path.join(STATE_DIR, "tuner_requests.json")
TUNER_LOCK_FILE = os.path.join(STATE_DIR, ".tuner.lock")
TUNER_MAX_ITERATIONS = 4
TUNER_PRECISION_THRESHOLD = 0.9

# --- persistência de histórico por execução (7a) ---
RUNS_FILE = os.path.join(STATE_DIR, "runs.json")
RUNS_LOCK_FILE = os.path.join(STATE_DIR, ".runs.lock")
RUNS_MAX = 1000
RUNS_RETENTION_DAYS = 90
TELEGRAM_MSG_LIMIT = 3500
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

PRICE_RE = re.compile(r"R\$\s*([\d.]+(?:[.,]\d+)?)")
JUNK_RE = re.compile(
    r"\b(dock|station|suporte|at[eé]|docking|encosto|cabo|clip|gabinete|case|c[aá]mera)\b"
    r"|recupera|reparo|conserto|quebrado|defeito|p/?a/?r/?a?\s?pecas|pecas",
    re.I
)
# Fase 6: shim removido — domain.filters é fonte única, olx_scraper mantém cópia legada idêntica para compat até remoção total

DEFAULT_SCHEDULE = {
    "enabled": False,
    "mode": "interval",
    "interval_minutes": 360,
    "jitter_minutes": 0,
    "cron": "0 */6 * * *",
    "timezone": "America/Sao_Paulo",
}

DEFAULT_PRODUCT = {
    "id": "hd-6-12tb",
    "name": "HD 6-12TB",
    "queries": DEFAULT_QUERIES,
    "pages": 2,
    "search_url": SEARCH_URL,
    "playwright_only": False,
    "min_price": None,
    "max_price": 900.0,
    "junk_pattern": JUNK_RE.pattern,
    "exclude_keywords": [],
    "include_pattern": "",
    "enabled": True,
    "draft": False,
    "schedule": dict(DEFAULT_SCHEDULE),
}

DEFAULT_LLM_CONFIG = {
    "provider": "openrouter",
    "base_url": "https://openrouter.ai/api/v1",
    "model": "openai/gpt-4o-mini",
    "api_key": None,
}

DEFAULT_CONFIG = {
    "products": [dict(DEFAULT_PRODUCT)],
    "telegram_token": None,
    "telegram_chat_id": None,
    "notify_price_change_pct": DEFAULT_NOTIFY_PRICE_CHANGE_PCT,
    "tuner_llm": dict(DEFAULT_LLM_CONFIG),
}


def _normalize_schedule(raw) -> dict:
    """Normaliza campo schedule de um produto (Fase 2: interval + cron + jitter).

    Garante retrocompatibilidade: campos ausentes recebem defaults,
    valores fora de faixa são clampados, cron inválido cai para default.
    Sempre persiste jitter_minutes e cron (para UI previsível).
    """
    defaults = dict(DEFAULT_SCHEDULE)
    if not isinstance(raw, dict):
        return dict(defaults)
    out: dict = {}
    out["enabled"] = bool(raw.get("enabled", defaults["enabled"]))
    mode = str(raw.get("mode") or defaults["mode"]).strip()
    if mode not in ("interval", "cron"):
        mode = "interval"
    out["mode"] = mode

    # jitter: sempre normalizado 0-60
    try:
        jv = int(raw.get("jitter_minutes", defaults.get("jitter_minutes", 0)) or 0)
    except Exception:
        jv = 0
    jv = max(0, min(60, jv))
    out["jitter_minutes"] = jv

    if mode == "interval":
        try:
            iv = int(raw.get("interval_minutes", defaults["interval_minutes"]))
        except Exception:
            iv = defaults["interval_minutes"]
        iv = max(15, min(10080, iv))
        out["interval_minutes"] = iv
        # cron mantém para frente-compat (não usado em interval, mas útil ao trocar modo)
        cron_raw = str(raw.get("cron") or defaults["cron"]).strip()[:100]
        if cron_raw and len(cron_raw.split()) == 5:
            # valida mas não corrige silenciosamente se válido
            try:
                from apscheduler.triggers.cron import CronTrigger  # type: ignore
                CronTrigger.from_crontab(cron_raw, timezone="America/Sao_Paulo")
                out["cron"] = cron_raw
            except ImportError:
                out["cron"] = cron_raw
            except Exception:
                out["cron"] = defaults["cron"]
        else:
            out["cron"] = defaults["cron"]
    else:  # cron
        cron = str(raw.get("cron") or defaults["cron"]).strip()[:100]
        parts = cron.split()
        if len(parts) != 5:
            cron = defaults["cron"]
        else:
            validated = False
            # tenta validar com CronTrigger (sem depender de croniter)
            try:
                from apscheduler.triggers.cron import CronTrigger  # type: ignore
                CronTrigger.from_crontab(cron, timezone="America/Sao_Paulo")
                validated = True
            except ImportError:
                validated = True  # sem APScheduler, aceita 5 campos
            except Exception:
                cron = defaults["cron"]
                validated = True
            if not validated:
                # fallback croniter se CronTrigger não disponível
                try:
                    from croniter import croniter  # type: ignore
                    croniter(cron)
                except ImportError:
                    pass
                except Exception:
                    cron = defaults["cron"]
        out["cron"] = cron
        # interval preservado para compat (útil ao voltar para interval)
        try:
            iv = int(raw.get("interval_minutes", defaults["interval_minutes"]))
            iv = max(15, min(10080, iv))
            out["interval_minutes"] = iv
        except Exception:
            out["interval_minutes"] = defaults["interval_minutes"]

    # timezone fixo Fase 2 — sempre America/Sao_Paulo; ignora outros mas persiste default
    tz = str(raw.get("timezone") or defaults["timezone"]).strip()
    if tz != "America/Sao_Paulo":
        tz = "America/Sao_Paulo"
    out["timezone"] = tz
    return out


# --- persistência de last_run por produto (Fase 2) ---

def load_schedules():
    """Carrega ~/.olx/schedules.json com last_runs por produto."""
    if not os.path.exists(SCHEDULES_FILE):
        return {"last_runs": {}}
    try:
        with open(SCHEDULES_FILE) as f:
            data = json.load(f) or {}
        if not isinstance(data, dict):
            return {"last_runs": {}}
        lr = data.get("last_runs")
        if not isinstance(lr, dict):
            data["last_runs"] = {}
        # sanitiza entradas
        cleaned = {}
        for pid, info in list(data.get("last_runs", {}).items()):
            if not isinstance(info, dict):
                continue
            pid_s = str(pid).strip()
            if not pid_s:
                continue
            cleaned[pid_s] = info
        data["last_runs"] = cleaned
        return data
    except Exception:
        return {"last_runs": {}}


def save_schedules(data):
    """Persiste schedules.json de forma atômica."""
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = SCHEDULES_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SCHEDULES_FILE)
    return data


def record_schedule_run(product_id: str, status: str = "ok", error: str = None, at_iso: str = None, matched_count: int = None, results_count: int = None):
    """Registra last_run de um produto em schedules.json. Thread-safe via atomic write."""
    pid = str(product_id or "").strip()
    if not pid:
        return
    if at_iso is None:
        at_iso = datetime.now(timezone.utc).isoformat()
    data = load_schedules()
    lr = data.setdefault("last_runs", {})
    entry = dict(lr.get(pid, {}))
    entry["at"] = at_iso
    entry["status"] = status
    if error is not None:
        entry["error"] = str(error)[:500] if error else None
    elif "error" in entry and status == "ok":
        entry.pop("error", None)
    if matched_count is not None:
        entry["matched_count"] = int(matched_count)
    if results_count is not None:
        entry["results_count"] = int(results_count)
    lr[pid] = entry
    try:
        save_schedules(data)
    except Exception:
        pass
    return entry


def get_last_run(product_id: str):
    data = load_schedules()
    return data.get("last_runs", {}).get(str(product_id))


# --- persistência de notificações enviadas (dedupe Telegram) ---

def _parse_iso_utc(dt_str):
    """Parse ISO para datetime aware (UTC se naive). None se inválido."""
    if not dt_str:
        return None
    try:
        s = str(dt_str).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        # query string decodes '+' as space; restaura
        if " " in s and "+" not in s and "T" in s:
            # ex: 2026-08-27T00:27:01.123456 00:00 -> +00:00
            s = s.replace(" ", "+")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


@contextmanager
def _notified_lock():
    """Lock exclusivo entre processos (CLI vs GUI) via flock em ~/.olx/.notified.lock."""
    os.makedirs(STATE_DIR, exist_ok=True)
    fd = os.open(NOTIFIED_LOCK_FILE, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass
        os.close(fd)


def _read_notified_file():
    """Lê notified.json e devolve dict plano {key: {"at": iso, "price": float}}.

    Aceita formato com wrapper {"notified": {...}} ou flat; entradas inválidas são descartadas.
    """
    try:
        with open(NOTIFIED_FILE) as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    inner = data.get("notified") if isinstance(data.get("notified"), dict) else data
    return {str(k): v for k, v in inner.items() if isinstance(v, dict)}


def _write_notified_file(reg):
    """Escrita atômica tmp+replace (padrão de save_schedules). Caller segura o lock."""
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = NOTIFIED_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"notified": reg}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, NOTIFIED_FILE)


def _prune_notified(reg):
    """Remove entradas com mais de NOTIFIED_RETENTION_DAYS dias ou sem timestamp válido."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=NOTIFIED_RETENTION_DAYS)
    out = {}
    for k, v in reg.items():
        if not isinstance(v, dict):
            continue
        at = _parse_iso_utc(v.get("at"))
        if at is None or at < cutoff:
            continue
        out[k] = v
    return out


def _migrate_from_history():
    """Popula registro a partir de deals.json assumindo tudo já notificado.

    Migração lazy da 1ª execução: evita tempestade de spam quando deals.json
    pré-existe mas notified.json ainda não. Entradas herdam at=now (idade zero).
    """
    migrated = {}
    try:
        with open(HISTORY_FILE) as f:
            deals = json.load(f)
    except Exception:
        return migrated
    if not isinstance(deals, list):
        return migrated
    now_iso = datetime.now(timezone.utc).isoformat()
    for d in deals:
        if not isinstance(d, dict) or not d.get("id"):
            continue
        key = f"{d.get('product_id', '')}:{d.get('id')}"
        entry = {"at": now_iso}
        if d.get("price") is not None:
            try:
                entry["price"] = float(d["price"])
            except (TypeError, ValueError):
                pass
        migrated[key] = entry
    return migrated


def load_notified():
    """Carrega registro de notificados {f"{product_id}:{ad_id}": {"at": iso, "price": float}}.

    Se notified.json ausente e deals.json existente, migra (uma vez) e persiste.
    """
    if os.path.exists(NOTIFIED_FILE):
        return _read_notified_file()
    migrated = _migrate_from_history()
    if migrated:
        try:
            save_notified(migrated)
        except Exception:
            pass
    return migrated


def save_notified(entries):
    """Mescla {key: entry} no registro, aplica prune (>90 dias) e persiste atomicamente.

    Merge sobre o conteúdo atual do disco dentro do lock evita lost update
    entre processos (CLI e GUI).
    """
    if not isinstance(entries, dict):
        raise ValueError("entries deve ser dict")
    with _notified_lock():
        current = _read_notified_file()
        current.update({str(k): v for k, v in entries.items() if isinstance(v, dict)})
        current = _prune_notified(current)
        _write_notified_file(current)
        return current


def remove_notified(keys):
    """Remove chaves do registro (botão reenviar). Retorna nº removidas."""
    keys = [str(k) for k in (keys or []) if k]
    with _notified_lock():
        current = _read_notified_file()
        removed = 0
        for k in keys:
            if k in current:
                del current[k]
                removed += 1
        if removed:
            _write_notified_file(current)
        return removed


def clear_notified():
    """Apaga todo o registro de notificações. Retorna nº de entradas removidas."""
    with _notified_lock():
        n = len(_read_notified_file())
        _write_notified_file({})
        return n


# --- persistência de histórico por execução (7a) ---------------------------------

@contextmanager
def _runs_lock():
    """Lock exclusivo via flock em RUNS_LOCK_FILE (mesmo padrão de _notified_lock)."""
    os.makedirs(STATE_DIR, exist_ok=True)
    fd = os.open(RUNS_LOCK_FILE, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass
        os.close(fd)


def _read_runs_file() -> list:
    """Lê runs.json; retorna [] se ausente/corrompido. Sanitiza cada entry."""
    try:
        with open(RUNS_FILE) as f:
            data = json.load(f)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if not isinstance(entry.get("id"), str) or not entry.get("id").strip():
            continue
        if not isinstance(entry.get("started_at"), str) or not _parse_iso_utc(entry.get("started_at")):
            continue
        out.append(entry)
    return out


def _write_runs_file(runs: list):
    """Escrita atômica tmp+replace. Caller segura o lock."""
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = RUNS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(runs, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RUNS_FILE)


def _prune_runs(runs: list) -> list:
    """Filtra <90d + keep last 1000 (ordenado por started_at asc, mantém mais recentes)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RUNS_RETENTION_DAYS)
    # filter by date
    filtered = []
    for r in runs:
        dt = _parse_iso_utc(r.get("started_at"))
        if dt is None:
            continue
        if dt < cutoff:
            continue
        filtered.append(r)
    # keep newest 1000 by started_at
    if len(filtered) > RUNS_MAX:
        # sort asc to keep last 1000 newest
        filtered.sort(key=lambda x: _parse_iso_utc(x.get("started_at")) or datetime.min.replace(tzinfo=timezone.utc))
        filtered = filtered[-RUNS_MAX:]
    # ensure sorted asc (chronological) for stable file
    filtered.sort(key=lambda x: _parse_iso_utc(x.get("started_at")) or datetime.min.replace(tzinfo=timezone.utc))
    return filtered


def load_runs() -> list:
    """Lê runs.json sem prune (lazy prune só na escrita)."""
    return _read_runs_file()


def save_runs(runs: list) -> list:
    """Persiste runs.json com prune dentro do lock."""
    if not isinstance(runs, list):
        raise ValueError("runs deve ser lista")
    with _runs_lock():
        pruned = _prune_runs(list(runs))
        _write_runs_file(pruned)
        return pruned


def append_run(entry: dict) -> dict:
    """Append atômico: read -> append -> prune -> write -> return entry."""
    if not isinstance(entry, dict):
        raise ValueError("entry deve ser dict")
    if not entry.get("id"):
        raise ValueError("entry precisa de id")
    with _runs_lock():
        runs = _read_runs_file()
        runs.append(dict(entry))
        pruned = _prune_runs(runs)
        _write_runs_file(pruned)
        return dict(entry)


def get_run(run_id: str):
    """Busca run por id."""
    rid = str(run_id or "").strip()
    if not rid:
        return None
    for r in _read_runs_file():
        if r.get("id") == rid:
            return r
    return None


def delete_runs(before: str = None, product_id: str = None, status: str = None, source: str = None) -> int:
    """Remove runs matching filters. Returns count removed. At least one filter required in caller."""
    if before and isinstance(before, str) and " " in before and "+" not in before:
        before = before.replace(" ", "+")
    before_dt = _parse_iso_utc(before) if before else None
    if before and before_dt is None:
        raise ValueError("before inválido (ISO esperado)")
    if status and status not in ("ok", "error"):
        raise ValueError("status deve ser ok ou error")
    with _runs_lock():
        runs = _read_runs_file()
        keep = []
        removed = 0
        for r in runs:
            match_before = False
            match_pid = False
            match_status = False
            match_source = False
            has_filter = False
            # before: started_at < before
            if before_dt is not None:
                has_filter = True
                dt = _parse_iso_utc(r.get("started_at"))
                if dt is not None and dt < before_dt:
                    match_before = True
                else:
                    match_before = False
            if product_id:
                has_filter = True
                pids = (r.get("product_ids_scraped") or []) + (r.get("product_ids_requested") or [])
                # normalize to str set
                pset = set(str(x) for x in pids)
                if product_id in pset:
                    match_pid = True
            if status:
                has_filter = True
                if r.get("status") == status:
                    match_status = True
            if source:
                has_filter = True
                if r.get("source") == source:
                    match_source = True
            # If no filter matched as has_filter false, caller should have validated
            # Determine if this run matches ALL active filters (AND)
            # For combined filters: run is removed only if it satisfies every filter provided
            active = []
            if before_dt is not None:
                active.append(match_before)
            if product_id:
                active.append(match_pid)
            if status:
                active.append(match_status)
            if source:
                active.append(match_source)
            if not has_filter:
                keep.append(r)
                continue
            if all(active):
                removed += 1
            else:
                keep.append(r)
        if removed:
            _write_runs_file(keep)
        return removed


# --- tuner persistence (Backlog #12) ---

@contextmanager
def _tuner_lock():
    os.makedirs(STATE_DIR, exist_ok=True)
    fd = os.open(TUNER_LOCK_FILE, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass
        os.close(fd)


def _read_tuner_file() -> list:
    try:
        with open(TUNER_FILE) as f:
            data = json.load(f)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for e in data:
        if isinstance(e, dict) and e.get("id"):
            out.append(e)
    return out


def _write_tuner_file(reqs: list):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = TUNER_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(reqs, f, ensure_ascii=False, indent=2)
    os.replace(tmp, TUNER_FILE)


def load_tuner_requests() -> list:
    return _read_tuner_file()


def save_tuner_requests(reqs: list) -> list:
    if not isinstance(reqs, list):
        raise ValueError("reqs deve ser lista")
    with _tuner_lock():
        _write_tuner_file(list(reqs))
        return list(reqs)


def get_tuner_request(rid: str):
    rid = str(rid or "").strip()
    if not rid:
        return None
    for r in _read_tuner_file():
        if r.get("id") == rid:
            return r
    return None


def upsert_tuner_request(entry: dict) -> dict:
    if not isinstance(entry, dict) or not entry.get("id"):
        raise ValueError("entry precisa de id")
    with _tuner_lock():
        reqs = _read_tuner_file()
        idx = next((i for i, r in enumerate(reqs) if r.get("id") == entry["id"]), None)
        if idx is None:
            reqs.append(dict(entry))
        else:
            reqs[idx] = dict(entry)
        _write_tuner_file(reqs)
        return dict(entry)


def delete_tuner_request(rid: str) -> bool:
    rid = str(rid or "").strip()
    if not rid:
        return False
    with _tuner_lock():
        reqs = _read_tuner_file()
        keep = [r for r in reqs if r.get("id") != rid]
        if len(keep) == len(reqs):
            return False
        _write_tuner_file(keep)
        return True


def _dedupe_obrig_terms(terms: list) -> list:
    """Remove termos genéricos cujo conjunto de tokens é subconjunto de outro termo mais específico.

    Genérico: ["forza horizon 2","forza 2"] -> ["forza horizon 2"] para que heurística
    e include_pattern não validem semanticamente "motorsport 2" via alternativa genérica.
    Mantém OR correto para termos disjuntos como ["starfox","switch"].
    """
    if not terms:
        return terms
    # normaliza para lower e tokens
    norm = []
    for t in terms:
        tl = str(t).strip().lower()
        if not tl:
            continue
        toks = set(tok for tok in re.split(r"\s+", tl) if tok)
        norm.append((tl, toks))
    # ordena por |tokens| desc, depois len desc para priorizar específico
    norm.sort(key=lambda x: (len(x[1]), len(x[0])), reverse=True)
    kept = []
    kept_sets = []
    for tl, toks in norm:
        # se é subconjunto de algum já mantido, descarta (genérico)
        if any(toks <= ks for ks in kept_sets):
            continue
        # remove duplicatas exatas já mantidas (tratado acima, mas mantém)
        if tl in kept:
            continue
        kept.append(tl)
        kept_sets.append(toks)
    # preserva ordem original relativa dos mantidos? Retorna na ordem de especificidade (desc) é ok para regex OR; para briefing mantém lista filtrada
    # Para _tuner_briefing_to_include_pattern precisamos dos termos originais com case original, mas heuristic usa lower; aqui retorna lower para consistência
    # O chamador que precisa de case original deve mapear, mas para include_pattern usamos lower e escaped; é case-insensitive então lower ok
    # Reconstrói a partir de kept (lower) mas mantém forma original lower
    return kept


def _tuner_briefing_to_include_pattern(obrigatorios) -> str:
    """Converte lista/str de termos obrigatórios em regex leve com word boundaries.

    Suporta múltiplos termos separados por vírgula/; e também frases com espaço
    (ex.: "forza 2" -> exige ambos os tokens em ordem mas permite texto entre
    eles: \\bforza\\b.*\\b2\\b). Evita falso 0 matched quando include exige
    substring contígua mas título contém "forza horizon 2".
    Remove termos genéricos subconjunto de outro (ex.: "forza 2" quando já há "forza horizon 2")
    para validação semântica genérica.
    """
    if not obrigatorios:
        return ""
    if isinstance(obrigatorios, str):
        parts = [p.strip() for p in re.split(r"[,;]+", obrigatorios) if p.strip()]
    elif isinstance(obrigatorios, list):
        parts = [str(p).strip() for p in obrigatorios if str(p).strip()]
    else:
        parts = []
    if not parts:
        return ""
    # dedupe semântico: remove genéricos subconjunto
    parts = _dedupe_obrig_terms(parts)
    # para cada parte, se contém espaço, exige todos os tokens em ordem com .* entre eles
    escaped_parts = []
    for p in parts:
        tokens = [t for t in re.split(r"\s+", p.strip()) if t]
        if not tokens:
            continue
        if len(tokens) == 1:
            escaped_parts.append(r"\b" + re.escape(tokens[0]) + r"\b")
        else:
            # ex.: "forza 2" -> \bforza\b.*\b2\b, "forza horizon 2" -> \bforza\b.*\bhorizon\b.*\b2\b
            escaped_parts.append(r"\b" + r"\b.*\b".join(re.escape(t) for t in tokens) + r"\b")
    if not escaped_parts:
        return ""
    if len(escaped_parts) == 1:
        return escaped_parts[0]
    return r"(" + "|".join(escaped_parts) + r")"


def _tuner_evaluate_deal(deal: dict, briefing: dict) -> tuple:
    """Heurística de relevância frente ao briefing. Retorna (is_tp: bool, motivo: str, sugestao: str)."""
    title = str(deal.get("title") or "").lower()
    obrig = briefing.get("obrigatorios") or briefing.get("termos_obrigatorios") or ""
    excl = briefing.get("excludentes") or briefing.get("termos_a_excluir") or briefing.get("excluir") or ""
    # normaliza obrigatorios para lista (frases com espaço exigem todos os tokens, não substring contígua)
    if isinstance(obrig, str):
        obrig_list = [p.strip().lower() for p in re.split(r"[,;]+", obrig) if p.strip()]
    elif isinstance(obrig, list):
        obrig_list = [str(p).strip().lower() for p in obrig if str(p).strip()]
    else:
        obrig_list = []
    # dedupe semântico genérico: remove termo genérico subconjunto de outro mais específico
    obrig_list = _dedupe_obrig_terms(obrig_list)
    if isinstance(excl, str):
        excl_list = [p.strip().lower() for p in re.split(r"[,;]+", excl) if p.strip()]
    elif isinstance(excl, list):
        excl_list = [str(p).strip().lower() for p in excl if str(p).strip()]
    else:
        excl_list = []
    # helper: frase com espaço -> todos os tokens precisam aparecer (ex.: "forza 2" casa "forza horizon 2")
    def _term_in_title(term: str, title_low: str) -> bool:
        if not term:
            return False
        if " " in term:
            toks = [t for t in term.split() if t]
            return all(tok in title_low for tok in toks)
        return term in title_low
    # se tem excludente no título, é FP
    for term in excl_list:
        if term and _term_in_title(term, title):
            return False, f"contém excludente '{term}'", f"adicionar '{term}' em exclude_keywords"
    # se tem obrigatorio, precisa conter pelo menos um (cada obrigatorio pode ser frase)
    if obrig_list:
        hit = any(_term_in_title(term, title) for term in obrig_list if term)
        if not hit:
            return False, f"falta obrigatório {obrig_list}", f"ajustar include_pattern para {obrig_list}"
    else:
        # sem obrigatorios, usa definicao palavras chave
        definicao = str(briefing.get("definicao") or briefing.get("definicao_uma_frase") or briefing.get("descricao") or "").lower()
        words = [w for w in re.split(r"\W+", definicao) if len(w) > 3]
        if words and not any(w in title for w in words[:3]):
            # fraco, mas considera FP se definicao tem palavras que não aparecem
            # para não ser muito agressivo, só marca FP se título claramente off-topic via heuristic de sementes
            sementes = briefing.get("sementes") or briefing.get("termos_busca") or []
            if isinstance(sementes, str):
                sementes = [p.strip().lower() for p in re.split(r"[,;]+", sementes) if p.strip()]
            sementes = [str(s).lower() for s in (sementes or []) if str(s).strip()]
            if sementes and not any(s in title for s in sementes):
                return False, "não contém termo da definição/semente", "revisar queries ou include_pattern"
    return True, "relevante", ""


# Fase 6: shim removido — domain.tuner_heuristic é fonte única (mantido cópia legada)


class TunerLLMError(Exception):
    def __init__(self, msg, provider=None, model=None, latency_ms=None):
        super().__init__(msg)
        self.provider = provider
        self.model = model
        self.latency_ms = latency_ms


def tuner_llm_classify(briefing: dict, deals: list) -> dict:
    """Chama LLM via config tuner_llm (env > config.json) — providers openrouter/opencode/mock (openai-compatible).

    Retorna {"classifications": [{"id": str, "label": "tp"|"fp", "reason": str}], "proposed_refinement": {"add_exclude": [], "new_include_pattern": str|null, "price_adjust": {min,max}}}
    Levanta TunerLLMError em falha (sem env, sem SDK, erro de rede, JSON inválido).
    """
    cfg = get_tuner_llm_config()
    provider = (cfg.get("provider") or "openrouter").strip().lower()
    model = (cfg.get("model") or "openai/gpt-4o-mini").strip() if cfg.get("model") else "openai/gpt-4o-mini"
    api_key = (cfg.get("api_key") or "").strip() if cfg.get("api_key") else ""
    base_url = (cfg.get("base_url") or "").strip() if cfg.get("base_url") else ""
    # normaliza aliases legados para compat
    if provider in ("openai", "openai-compatible"):
        provider = "openrouter"
        if not base_url:
            base_url = "https://openrouter.ai/api/v1"
    # mock provider bypass: succeed without key using heuristic synthesis
    if provider == "mock":
        # synthesize as LLM success with heuristic logic but marca used=true
        heur = tuner_heuristic_classify(briefing, deals) if 'tuner_heuristic_classify' in globals() else None
        # If heur not yet defined, fallback inline
        if heur is None:
            heur = {"classifications": [], "proposed_refinement": {}}
        # ensure classifications shape
        return heur
    # valida provider suportado
    if provider not in ("openrouter", "opencode"):
        raise TunerLLMError(f"provider desconhecido: {provider} (use openrouter|opencode|mock)", provider=provider, model=model)
    if provider == "openrouter" and not api_key:
        raise TunerLLMError(f"API_KEY ausente para provider {provider}", provider=provider, model=model)
    # opencode pode dispensar api_key (ollama local) — usa dummy se vazio
    if provider == "opencode" and not api_key:
        api_key = "not-needed"
    if not base_url:
        # fallback por provider
        if provider == "openrouter":
            base_url = "https://openrouter.ai/api/v1"
        elif provider == "opencode":
            base_url = "http://127.0.0.1:4096/v1"
    # limita deals para prompt
    sample = []
    for d in (deals or [])[:50]:
        sample.append({"id": str(d.get("id") or ""), "title": str(d.get("title") or "")[:200], "price": d.get("price")})
    # monta prompt
    system_prompt = "Você é um classificador de anúncios da OLX Brasil. Dado o briefing do produto desejado, classifique cada anúncio como tp (relevante) ou fp (falso positivo). Seja rigoroso: só tp se o título contém os termos obrigatórios e não contém excludentes."
    briefing_clean = {
        "descricao": str(briefing.get("descricao") or briefing.get("description") or "")[:200],
        "definicao": str(briefing.get("definicao") or briefing.get("definicao_uma_frase") or briefing.get("descricao") or "")[:200],
        "obrigatorios": briefing.get("obrigatorios") or briefing.get("termos_obrigatorios") or "",
        "excludentes": briefing.get("excludentes") or briefing.get("termos_a_excluir") or "",
        "sementes": briefing.get("sementes") or briefing.get("termos_busca") or [],
        "faixa": {"min_price": briefing.get("preco_min") or briefing.get("min_price"), "max_price": briefing.get("preco_max") or briefing.get("max_price")},
    }
    # sanitiza sementes
    if isinstance(briefing_clean["sementes"], str):
        briefing_clean["sementes"] = [s.strip() for s in re.split(r"[,;]+", briefing_clean["sementes"]) if s.strip()]
    user_content = json.dumps({"briefing": briefing_clean, "deals": sample}, ensure_ascii=False)
    user_content += "\n\nRetorne JSON com {\"classifications\": [{\"id\": str, \"label\": \"tp\"|\"fp\", \"reason\": str}], \"proposed_refinement\": {\"add_exclude\": [str], \"new_include_pattern\": str|null, \"price_adjust\": {\"min\": number|null, \"max\": number|null}, \"reason\": str}} . add_exclude ≤5 termos, new_include_pattern é regex opcional."
    # chama provider (openai-compatible: openrouter/opencode via openai SDK com base_url)
    if provider in ("openrouter", "opencode"):
        try:
            import openai  # type: ignore
        except Exception as e:
            raise TunerLLMError(f"openai SDK não disponível: {e}", provider=provider, model=model)
        # openai SDK com base_url + headers específicos por provider
        client_kwargs = {"api_key": api_key, "base_url": base_url}
        if provider == "openrouter":
            client_kwargs["default_headers"] = {
                "HTTP-Referer": "https://github.com/luanfoli/olx-deals",
                "X-Title": "olx-deals tuner",
            }
        try:
            client = openai.OpenAI(**client_kwargs)
        except Exception as e:
            raise TunerLLMError(f"falha ao criar client {provider}: {e}", provider=provider, model=model)
        # retry com backoff para 429/5xx (openrouter shared_pool)
        last_exc = None
        resp = None
        for attempt in range(3):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0,
                    max_tokens=1500,
                )
                last_exc = None
                break
            except Exception as e:
                last_exc = e
                msg = str(e)
                # detecta retryable: 429 rate-limited ou 5xx
                is_429 = "429" in msg or "rate-limited" in msg.lower() or "rate_limit" in msg.lower()
                is_5xx = any(code in msg for code in ("500", "502", "503", "504"))
                if not (is_429 or is_5xx):
                    raise TunerLLMError(f"LLM request falhou ({provider} {base_url}): {e}", provider=provider, model=model)
                if attempt == 2:
                    raise TunerLLMError(f"LLM request falhou ({provider} {base_url}): {e}", provider=provider, model=model)
                # respeita Retry-After se presente (header > body), senão exponential backoff 2s,4s
                wait = None
                try:
                    # prioriza header da resposta (openai SDK: e.response.headers)
                    resp_obj = getattr(e, "response", None)
                    hdrs = None
                    if resp_obj is not None:
                        hdrs = getattr(resp_obj, "headers", None)
                        if hdrs is None and isinstance(resp_obj, dict):
                            hdrs = resp_obj.get("headers")
                    if hdrs:
                        # suporta dict-like com get
                        try:
                            ra = hdrs.get("retry-after") or hdrs.get("Retry-After") or hdrs.get("retry_after_seconds")
                        except Exception:
                            ra = None
                        if ra is not None:
                            try:
                                wait = int(str(ra).strip().split(",")[0])
                            except Exception:
                                wait = None
                    if wait is None:
                        import re as _re
                        m = _re.search(r"retry_after_seconds['\"]?\s*[:=]\s*(\d+)", msg)
                        if m:
                            wait = int(m.group(1))
                        else:
                            m2 = _re.search(r"Retry-After['\"]?\s*[:=]\s*['\"]?(\d+)", msg)
                            if m2:
                                wait = int(m2.group(1))
                except Exception:
                    wait = None
                if wait is None:
                    wait = 2 * (attempt + 1)  # 2s, 4s
                # clamp 1..30s
                wait = max(1, min(30, wait))
                try:
                    time.sleep(wait)
                except Exception:
                    pass
        if resp is None and last_exc is not None:
            raise TunerLLMError(f"LLM request falhou ({provider} {base_url}): {last_exc}", provider=provider, model=model)
        content = resp.choices[0].message.content if resp.choices else ""
        # tokens
        usage = getattr(resp, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
        try:
            parsed = json.loads(content) if isinstance(content, str) else content
        except Exception as e:
            raise TunerLLMError(f"LLM retornou JSON inválido: {e} | content={str(content)[:500]}", provider=provider, model=model)
        if not isinstance(parsed, dict) or "classifications" not in parsed:
            raise TunerLLMError(f"LLM resposta sem classifications: {str(parsed)[:500]}", provider=provider, model=model)
        # valida classifications
        parsed.setdefault("proposed_refinement", {})
        # inject tokens for wrapper
        parsed["_prompt_tokens"] = prompt_tokens
        parsed["_completion_tokens"] = completion_tokens
        return parsed
    else:
        raise TunerLLMError(f"provider desconhecido: {provider} (use openrouter|opencode|mock)", provider=provider, model=model)


def tuner_heuristic_classify(briefing: dict, deals: list) -> dict:
    """Heurística pura: classifica via _tuner_evaluate_deal e propõe refinamento via _tuner_propose_refinement_impl.

    Retorna {"classifications": [...], "proposed_refinement": {...}} compatível com LLM.
    """
    classifications = []
    fp_deals = []
    for d in deals or []:
        is_tp, motivo, _ = _tuner_evaluate_deal(d, briefing)
        label = "tp" if is_tp else "fp"
        classifications.append({"id": str(d.get("id") or ""), "label": label, "reason": motivo})
        if not is_tp:
            fp_deals.append(d)
    # propõe refinamento usando heurística existente (sem current_product ainda -> deriva)
    # Para compat, vamos gerar proposta baseada em fp_deals
    # Nota: usa dummy sem exclude para não filtrar exclude pré-existente; o wrapper final (_run_tuner) refina com produto real e dedupula.
    # Aqui geramos proposta básica
    # Reuso da lógica de propose com produto dummy
    dummy_product = {"exclude_keywords": [], "include_pattern": ""}
    # chama lógica interna
    prop = _tuner_propose_refinement_impl(briefing, fp_deals, dummy_product) if '_tuner_propose_refinement_impl' in globals() else {}
    # Normaliza: garante add_exclude lista, new_include_pattern pode ser None
    if not isinstance(prop, dict):
        prop = {}
    prop.setdefault("add_exclude", [])
    # limita a 5
    prop["add_exclude"] = [str(x).strip()[:40] for x in (prop.get("add_exclude") or [])][:5]
    if "new_include_pattern" not in prop:
        prop["new_include_pattern"] = None
    # valida regex
    pat = prop.get("new_include_pattern")
    if pat:
        try:
            re.compile(str(pat), re.I)
        except re.error:
            prop["new_include_pattern"] = None
    # price_adjust placeholder
    prop.setdefault("price_adjust", {})
    return {"classifications": classifications, "proposed_refinement": prop}


def _tuner_propose_refinement_impl(briefing: dict, fp_deals: list, current_product: dict) -> dict:
    """Implementação interna do propose (extraída do original) para reuso por heuristic e LLM wrapper."""
    if not fp_deals:
        return {}
    from collections import Counter
    stop = set(["de", "do", "da", "para", "com", "sem", "que", "por", "em", "um", "uma", "os", "as", "o", "a", "e", "ou"])
    words = []
    for d in fp_deals:
        title = str(d.get("title") or "").lower()
        toks = [t for t in re.split(r"\W+", title) if t and len(t) > 2 and t not in stop]
        words.extend(toks)
    obrig = briefing.get("obrigatorios") or ""
    if isinstance(obrig, str):
        obrig_raw = [p.strip().lower() for p in re.split(r"[,;]+", obrig) if p.strip()]
    elif isinstance(obrig, list):
        obrig_raw = [str(p).strip().lower() for p in obrig if str(p).strip()]
    else:
        obrig_raw = []
    # expande frases em tokens individuais para não propor exclusão de termos obrigatórios (ex.: "forza 2" -> {"forza","2"})
    obrig_words = set()
    for phrase in obrig_raw:
        for tok in re.split(r"\s+", phrase):
            if tok:
                obrig_words.add(tok)
        obrig_words.add(phrase)  # mantém frase original também
    cur_ex = set(x.lower() for x in (current_product.get("exclude_keywords") or []))
    counter = Counter(w for w in words if w not in obrig_words and w not in cur_ex and len(w) > 2)
    add = []
    for w, c in counter.most_common(5):
        if c >= 2 or len(fp_deals) <= 3:
            add.append(w)
            if len(add) >= 2:
                break
    if not add and fp_deals:
        title = str(fp_deals[0].get("title") or "").lower()
        toks = [t for t in re.split(r"\W+", title) if t and len(t) > 2 and t not in stop and t not in obrig_words and t not in cur_ex]
        if toks:
            add = [toks[0]]
    new_include = None
    falta_count = sum(1 for d in fp_deals if "falta obrigatório" in str(d.get("_motivo", "") if isinstance(d, dict) else ""))
    # Para fp_deals que são dicts originais com _motivo, usa motivo direto; para heuristic classifications não tem _motivo, então conta heuristic via evaluate
    if falta_count == 0 and fp_deals:
        # tenta inferir falta obrigatório via re-evaluate
        cnt = 0
        for d in fp_deals:
            is_tp, motivo, _ = _tuner_evaluate_deal(d, briefing)
            if "falta obrigatório" in motivo:
                cnt += 1
        falta_count = cnt
    if falta_count >= max(1, len(fp_deals)//2) and not current_product.get("include_pattern"):
        pat = _tuner_briefing_to_include_pattern(briefing.get("obrigatorios"))
        if pat:
            new_include = pat
    return {"add_exclude": add, "new_include_pattern": new_include}


def tuner_propose_refinement(*args, **kwargs) -> dict:
    """Compat: suporta (briefing, fp_deals, current_product) legado e (classifications, briefing, current_product) novo.

    Valida regex via re.compile, limita add_exclude ≤5.
    """
    # Detecta assinatura nova: (classifications, briefing, current_product)
    if len(args) == 3 and isinstance(args[0], list) and isinstance(args[1], dict) and isinstance(args[2], dict):
        classifications, briefing, current_product = args
        # classifications é lista de {id,label,reason}
        # Deriva fp_deals a partir de deals originais? Mas não temos deals originais aqui, só classifications.
        # Para compat com plano, quando chamado com classifications, vamos derivar proposta a partir das razões.
        # Se classifications contiver fp com reason, podemos inferir termos.
        # Simplifica: se classifications contém fp, tentar extrair termos de reason ou usar heuristic genérico.
        fp_ids = [c.get("id") for c in classifications if c.get("label") == "fp"]
        # Sem deals originais, não podemos extrair palavras; retorna proposta genérica baseada em fp count
        # Tenta usar heuristic com fp_ids como proxy: se há fp, sugere pattern se falta obrigatório
        prop = {}
        # Heurística: se muitos fp, sugere incluir pattern de obrigatorios
        if fp_ids and not current_product.get("include_pattern"):
            pat = _tuner_briefing_to_include_pattern(briefing.get("obrigatorios"))
            if pat:
                # só sugere se houver indicio de falta obrigatório nos reasons
                if any("falta obrigatório" in str(c.get("reason","")) for c in classifications if c.get("label")=="fp"):
                    prop["new_include_pattern"] = pat
        # add_exclude: tenta extrair palavras de reason que menciona excludente
        add = []
        for c in classifications:
            if c.get("label") == "fp":
                reason = str(c.get("reason") or "")
                # se reason menciona excludente 'X', extrai
                m = re.search(r"'([^']+)'", reason)
                if m:
                    term = m.group(1).strip()[:40]
                    if term and term.lower() not in [x.lower() for x in (current_product.get("exclude_keywords") or [])]:
                        add.append(term)
        prop["add_exclude"] = add[:5]
        # valida regex
        if prop.get("new_include_pattern"):
            try:
                re.compile(str(prop["new_include_pattern"]), re.I)
            except re.error:
                prop["new_include_pattern"] = None
        else:
            prop["new_include_pattern"] = None
        return prop
    # Assinatura legada: (briefing, fp_deals, current_product)
    if len(args) == 3 and isinstance(args[0], dict) and isinstance(args[1], list):
        briefing, fp_deals, current_product = args
        res = _tuner_propose_refinement_impl(briefing, fp_deals, current_product)
        # limita e valida
        res["add_exclude"] = [str(x).strip()[:40] for x in (res.get("add_exclude") or [])][:5]
        pat = res.get("new_include_pattern")
        if pat:
            try:
                re.compile(str(pat), re.I)
            except re.error:
                res["new_include_pattern"] = None
        else:
            res["new_include_pattern"] = None
        return res
    # Fallback genérico: tenta interpretar kwargs
    briefing = kwargs.get("briefing") or {}
    fp_deals = kwargs.get("fp_deals") or kwargs.get("classifications") or []
    current_product = kwargs.get("current_product") or {}
    if isinstance(fp_deals, list) and fp_deals and isinstance(fp_deals[0], dict) and "label" in fp_deals[0]:
        return tuner_propose_refinement(fp_deals, briefing, current_product)
    return _tuner_propose_refinement_impl(briefing, fp_deals if isinstance(fp_deals, list) else [], current_product)


def tuner_classify_deals(*args, **kwargs):
    """Wrapper híbrido LLM + fallback heurístico.

    Assinaturas suportadas:
      - tuner_classify_deals(deals: list, briefing: dict) -> {tp,fp,precision,total}  (legado, usado por testes antigos)
      - tuner_classify_deals(briefing: dict, deals: list) -> (result, llm_meta)  (novo, usado pelo worker)
    Onde result = {"classifications": [...], "proposed_refinement": {...}, "tp": [...], "fp": [...], "precision": float}
    e llm_meta = {"used": bool, "provider": str|None, "model": str|None, "latency_ms": int|None, "prompt_tokens": int|None, "completion_tokens": int|None, "error": str|None}
    """
    # Detecta assinatura legada: primeiro arg list, segundo dict
    if len(args) == 2 and isinstance(args[0], list) and isinstance(args[1], dict):
        deals, briefing = args
        # heurística direta sem LLM para compat
        tp = []
        fp = []
        for d in deals or []:
            is_tp, motivo, sugestao = _tuner_evaluate_deal(d, briefing)
            entry = dict(d)
            entry["_motivo"] = motivo
            entry["_sugestao"] = sugestao
            if is_tp:
                tp.append(entry)
            else:
                fp.append(entry)
        total = len(tp) + len(fp)
        prec = (len(tp) / total) if total else 0.0
        return {"tp": tp, "fp": fp, "precision": prec, "total": total}
    # Nova assinatura: briefing, deals
    if len(args) == 2 and isinstance(args[0], dict) and isinstance(args[1], list):
        briefing, deals = args
    elif "briefing" in kwargs and "deals" in kwargs:
        briefing = kwargs["briefing"]
        deals = kwargs["deals"]
    else:
        # fallback tenta extrair
        if len(args) >= 1 and isinstance(args[0], dict):
            briefing = args[0]
            deals = args[1] if len(args) > 1 else kwargs.get("deals", [])
        else:
            raise ValueError("tuner_classify_deals requer (briefing, deals) ou (deals, briefing)")
    # Tenta LLM (resolve via config + env)
    llm_cfg = get_tuner_llm_config()
    provider = (llm_cfg.get("provider") or "openrouter").strip().lower()
    model = (llm_cfg.get("model") or "openai/gpt-4o-mini").strip()
    start = time.time()
    llm_meta = {"used": False, "provider": provider, "model": model, "latency_ms": None, "prompt_tokens": None, "completion_tokens": None, "error": None, "base_url": llm_cfg.get("base_url")}
    try:
        result = tuner_llm_classify(briefing, deals)
        latency = int((time.time() - start) * 1000)
        # result deve ter classifications
        classifications = result.get("classifications") or []
        # captura tokens se injetados
        prompt_tokens = result.pop("_prompt_tokens", None) if isinstance(result, dict) else None
        completion_tokens = result.pop("_completion_tokens", None) if isinstance(result, dict) else None
        # proposta
        proposed = result.get("proposed_refinement") or {}
        # valida proposta
        if not isinstance(proposed, dict):
            proposed = {}
        # limita add_exclude
        if "add_exclude" in proposed:
            proposed["add_exclude"] = [str(x).strip()[:40] for x in (proposed["add_exclude"] or [])][:5]
        # valida regex
        pat = proposed.get("new_include_pattern")
        if pat:
            try:
                re.compile(str(pat), re.I)
            except re.error:
                proposed["new_include_pattern"] = None
        result["proposed_refinement"] = proposed
        # computa tp/fp para compat
        tp = [c for c in classifications if c.get("label") == "tp"]
        fp = [c for c in classifications if c.get("label") == "fp"]
        prec = (len(tp) / (len(tp)+len(fp))) if (len(tp)+len(fp)) else 0.0
        result["tp"] = tp
        result["fp"] = fp
        result["precision"] = prec
        result["total"] = len(tp)+len(fp)
        llm_meta.update({"used": True, "latency_ms": latency, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "error": None})
        return result, llm_meta
    except TunerLLMError as e:
        latency = int((time.time() - start) * 1000)
        llm_meta.update({"used": False, "provider": e.provider or provider, "model": e.model or model, "latency_ms": latency, "error": str(e)})
        heur = tuner_heuristic_classify(briefing, deals)
        # heur já tem classifications e proposed_refinement
        classifications = heur.get("classifications") or []
        tp = [c for c in classifications if c.get("label") == "tp"]
        fp = [c for c in classifications if c.get("label") == "fp"]
        prec = (len(tp) / (len(tp)+len(fp))) if (len(tp)+len(fp)) else 0.0
        heur["tp"] = tp
        heur["fp"] = fp
        heur["precision"] = prec
        heur["total"] = len(tp)+len(fp)
        return heur, llm_meta
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        llm_meta.update({"used": False, "latency_ms": latency, "error": str(e)[:500]})
        heur = tuner_heuristic_classify(briefing, deals)
        classifications = heur.get("classifications") or []
        tp = [c for c in classifications if c.get("label") == "tp"]
        fp = [c for c in classifications if c.get("label") == "fp"]
        prec = (len(tp) / (len(tp)+len(fp))) if (len(tp)+len(fp)) else 0.0
        heur["tp"] = tp
        heur["fp"] = fp
        heur["precision"] = prec
        heur["total"] = len(tp)+len(fp)
        return heur, llm_meta


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    s = re.sub(r"-+", "-", s)
    return s or "produto"


def _normalize_product(raw: dict, existing_ids=None) -> dict:
    """Normaliza e valida um produto, preenchendo defaults e gerando id se necessário."""
    existing_ids = set(existing_ids or [])
    p = {}
    name = str(raw.get("name") or raw.get("id") or "Produto").strip()
    if not name:
        name = "Produto"
    p["name"] = name[:80]

    # id
    pid = str(raw.get("id") or _slugify(name)).strip().lower()
    pid = _slugify(pid)
    # garante unicidade incremental se colidir
    base = pid
    n = 2
    while pid in existing_ids:
        pid = f"{base}-{n}"
        n += 1
    p["id"] = pid

    # queries
    queries = raw.get("queries", [])
    if isinstance(queries, str):
        queries = [queries]
    if not isinstance(queries, list):
        queries = []
    queries = [str(q).strip() for q in queries if str(q).strip()]
    # limita
    queries = queries[:50]
    queries = [q[:80] for q in queries]
    if not queries:
        queries = ["olx"]
    p["queries"] = queries

    # pages
    try:
        pages = int(raw.get("pages", 2))
    except Exception:
        pages = 2
    p["pages"] = max(1, min(10, pages))

    # search_url
    su = str(raw.get("search_url") or SEARCH_URL).strip()
    if "{query}" not in su or "{page}" not in su or not su.startswith("https://"):
        su = SEARCH_URL
    p["search_url"] = su

    p["playwright_only"] = bool(raw.get("playwright_only", False))

    # min_price / max_price
    for k in ("min_price", "max_price"):
        v = raw.get(k)
        if v is None or v == "":
            p[k] = None
        else:
            try:
                fv = float(v)
                if not (0 <= fv <= 1000000):
                    fv = None
                p[k] = fv if fv is not None and fv > 0 else None
            except Exception:
                p[k] = None
    if p["min_price"] is not None and p["max_price"] is not None:
        if p["min_price"] >= p["max_price"]:
            # corrige invertido: mantém só max
            p["min_price"] = None

    # junk_pattern
    pat = raw.get("junk_pattern", JUNK_RE.pattern)
    if pat is None:
        pat = ""
    pat = str(pat)
    if len(pat) > 2000:
        pat = pat[:2000]
    if pat:
        try:
            re.compile(pat, re.I)
        except re.error:
            pat = JUNK_RE.pattern
    p["junk_pattern"] = pat

    # exclude_keywords (filtro local pós-coleta: preço -> junk -> exclude -> dedupe)
    exclude_keywords = raw.get("exclude_keywords", [])
    if isinstance(exclude_keywords, str):
        exclude_keywords = [exclude_keywords]
    if not isinstance(exclude_keywords, list):
        exclude_keywords = []
    cleaned_ex = []
    seen_ex = set()
    for kw in exclude_keywords:
        kws = str(kw).strip()
        if not kws:
            continue
        key = kws.lower()
        if key in seen_ex:
            continue
        seen_ex.add(key)
        cleaned_ex.append(kws)
    p["exclude_keywords"] = [k[:40] for k in cleaned_ex[:50]]

    # include_pattern (agnóstico) — título deve casar para ser match; vazio = desativado
    inc_pat = raw.get("include_pattern", "")
    if inc_pat is None:
        inc_pat = ""
    inc_pat = str(inc_pat)
    if len(inc_pat) > 500:
        inc_pat = inc_pat[:500]
    if inc_pat:
        try:
            re.compile(inc_pat, re.I)
        except re.error:
            inc_pat = ""
    p["include_pattern"] = inc_pat

    p["enabled"] = bool(raw.get("enabled", True))

    # draft flag (tuner rascunho) — migra legacy [DRAFT] / draft-* se campo ausente
    # legado: draft-* / [DRAFT] permaneciam mesmo após ativação (enabled=true) → considerar promoção quando enabled=true
    if "draft" in raw:
        p["draft"] = bool(raw.get("draft"))
    else:
        raw_id = str(raw.get("id") or "").strip()
        raw_name = str(raw.get("name") or "").strip()
        is_legacy_draft_mark = raw_id.startswith("draft-") or raw_name.startswith("[DRAFT]")
        # se marcado como draft mas já habilitado, era promovido no código antigo (prefixo não foi limpo)
        p["draft"] = is_legacy_draft_mark and not p["enabled"]
    # limpa prefixo legacy [DRAFT] (antigo código deixava prefixo mesmo após promoção)
    if p["name"].startswith("[DRAFT]"):
        cleaned = p["name"][len("[DRAFT]"):].strip().lstrip("- ").strip()
        p["name"] = cleaned[:80] if cleaned else p["name"]

    # schedule por produto (Fase 1: interval)
    if "schedule" in raw and isinstance(raw["schedule"], dict):
        p["schedule"] = _normalize_schedule(raw["schedule"])
    else:
        # legacy sem schedule -> desabilitado por padrão (não altera comportamento)
        if raw.get("schedule") is None:
            p["schedule"] = dict(DEFAULT_SCHEDULE)
        else:
            p["schedule"] = _normalize_schedule(raw.get("schedule"))

    return p


def _normalize_tuner_llm(raw) -> dict:
    """Normaliza config tuner_llm persistida em config.json.

    Espera dict com provider/base_url/model/api_key. Valida provider em openrouter|opencode|mock,
    base_url https/http, model string 1..100, api_key opcional (None ou 8..500 chars).
    Retorna dict completo com defaults quando inválido.
    """
    defaults = dict(DEFAULT_LLM_CONFIG)
    if not isinstance(raw, dict):
        return dict(defaults)
    out = dict(defaults)
    prov = str(raw.get("provider") or raw.get("provider_name") or defaults["provider"]).strip().lower()
    if prov in ("openrouter", "opencode", "mock"):
        out["provider"] = prov
    # aliases: openai -> openrouter, local/ollama -> opencode
    elif prov in ("openai", "openai-compatible"):
        out["provider"] = "openrouter"
    else:
        out["provider"] = defaults["provider"]
    # base_url
    bu = raw.get("base_url") or raw.get("baseUrl") or raw.get("url")
    if out["provider"] == "openrouter":
        default_bu = "https://openrouter.ai/api/v1"
    elif out["provider"] == "opencode":
        default_bu = "http://127.0.0.1:4096/v1"
    else:  # mock
        default_bu = ""
    if bu is not None and str(bu).strip():
        bu_s = str(bu).strip()
        if bu_s.startswith("http://") or bu_s.startswith("https://"):
            # normaliza sem trailing /
            out["base_url"] = bu_s.rstrip("/")
        else:
            out["base_url"] = default_bu
    else:
        out["base_url"] = default_bu if out["provider"] != "mock" else ""
    # model
    m = raw.get("model") or raw.get("model_name") or raw.get("llm_model")
    if m is not None and str(m).strip():
        ms = str(m).strip()
        if 1 <= len(ms) <= 100:
            out["model"] = ms
    # api_key
    ak = raw.get("api_key") or raw.get("apikey") or raw.get("apiKey") or raw.get("token")
    if ak is not None and str(ak).strip() != "":
        aks = str(ak).strip()
        if len(aks) >= 4:  # permite qualquer chave >=4, mascaramento cuidará
            out["api_key"] = aks
        else:
            out["api_key"] = None
    else:
        # mantém None se não veio (permite env fallback)
        if raw.get("api_key") == "" or raw.get("api_key") is None:
            out["api_key"] = None
        # se veio mascarada (***), mantém None para não sobrescrever via _migrate
        # caller (save) trata _looks_masked separadamente
    return out


def get_tuner_llm_config(cfg=None) -> dict:
    """Resolve config efetiva do LLM: env (OLX_TUNER_LLM_*) com precedência sobre config.json.

    Fallback: _normalize_tuner_llm sobre cfg["tuner_llm"] ou DEFAULT.
    Env vars suportadas: OLX_TUNER_LLM_PROVIDER, OLX_TUNER_LLM_BASE_URL, OLX_TUNER_LLM_MODEL, OLX_TUNER_LLM_API_KEY (ou OPENAI_API_KEY).
    Para opencode sem chave, api_key pode ser None/vazio (não exige).
    """
    base = None
    if cfg is not None and isinstance(cfg, dict) and isinstance(cfg.get("tuner_llm"), dict):
        base = _normalize_tuner_llm(cfg["tuner_llm"])
    else:
        # tenta carregar do disco se não veio cfg
        try:
            c = load_config() if cfg is None else None
            if c and isinstance(c.get("tuner_llm"), dict):
                base = _normalize_tuner_llm(c["tuner_llm"])
        except Exception:
            pass
    if base is None:
        base = dict(DEFAULT_LLM_CONFIG)
    # env override (precedência)
    env_prov = (os.environ.get("OLX_TUNER_LLM_PROVIDER") or "").strip().lower()
    if env_prov:
        # normaliza aliases
        if env_prov in ("openrouter", "opencode", "mock"):
            base["provider"] = env_prov
        elif env_prov in ("openai", "openai-compatible"):
            base["provider"] = "openrouter"
        if base["provider"] == "openrouter" and not (os.environ.get("OLX_TUNER_LLM_BASE_URL") or "").strip():
            base["base_url"] = "https://openrouter.ai/api/v1"
        elif base["provider"] == "opencode" and not (os.environ.get("OLX_TUNER_LLM_BASE_URL") or "").strip():
            base["base_url"] = "http://127.0.0.1:4096/v1"
        elif base["provider"] == "mock":
            base["base_url"] = ""
    env_bu = (os.environ.get("OLX_TUNER_LLM_BASE_URL") or os.environ.get("OLX_TUNER_BASE_URL") or "").strip()
    if env_bu:
        if env_bu.startswith("http://") or env_bu.startswith("https://"):
            base["base_url"] = env_bu.rstrip("/")
    env_model = (os.environ.get("OLX_TUNER_LLM_MODEL") or os.environ.get("OLX_TUNER_LLM_MODEL_NAME") or "").strip()
    if env_model:
        base["model"] = env_model[:100]
    env_key = (os.environ.get("OLX_TUNER_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()
    if env_key:
        base["api_key"] = env_key
    # garante base_url coerente com provider quando não veio env nem config explícita
    if base["provider"] == "openrouter" and not base.get("base_url"):
        base["base_url"] = "https://openrouter.ai/api/v1"
    if base["provider"] == "opencode" and not base.get("base_url"):
        base["base_url"] = "http://127.0.0.1:4096/v1"
    if base["provider"] == "mock":
        base["base_url"] = ""
    return base


def _normalize_pct(v, default=None):
    """Normaliza notify_price_change_pct para float 0–100 (default 5%)."""
    if default is None:
        default = DEFAULT_NOTIFY_PRICE_CHANGE_PCT
    try:
        f = float(v)
        if 0 <= f <= 100:
            return f
    except (TypeError, ValueError):
        pass
    return default


def get_notify_price_change_pct(cfg=None):
    """Threshold percentual de |Δpreço| para re-notificar um anúncio já notificado."""
    if cfg is None:
        cfg = load_config()
    return _normalize_pct(cfg.get("notify_price_change_pct"))


def _migrate_legacy_config(stored: dict) -> dict:
    """Converte config flat legado (queries/min_tb/max_price_per_tb...) em products[]."""
    # já é novo formato?
    if isinstance(stored.get("products"), list):
        # normaliza cada produto
        products = []
        seen = set()
        for raw in stored["products"]:
            if not isinstance(raw, dict):
                continue
            np = _normalize_product(raw, seen)
            seen.add(np["id"])
            products.append(np)
        if not products:
            products = [dict(DEFAULT_PRODUCT)]
        out = {
            "products": products,
            "telegram_token": stored.get("telegram_token"),
            "telegram_chat_id": stored.get("telegram_chat_id"),
            "notify_price_change_pct": _normalize_pct(stored.get("notify_price_change_pct")),
            "tuner_llm": _normalize_tuner_llm(stored.get("tuner_llm") or stored.get("llm_config") or {}),
        }
        # compat: se telegram vinha como token/chat_id em telegram.json separado, já tratado no load_config
        return out

    # legado flat: tenta extrair queries/pages/search_url/junk/playwright
    legacy_queries = stored.get("queries", DEFAULT_QUERIES)
    legacy_pages = stored.get("pages", 2)
    legacy_search_url = stored.get("search_url", SEARCH_URL)
    legacy_junk = stored.get("junk_pattern", JUNK_RE.pattern)
    legacy_pw = stored.get("playwright_only", False)

    # preço: tenta mapear max_price_per_tb + max_tb -> max_price
    max_price = stored.get("max_price")
    if max_price is None and "max_price_per_tb" in stored:
        try:
            pptb = float(stored.get("max_price_per_tb", 75))
            # legacy MAX_TB era 12
            max_tb = float(stored.get("max_tb", 12))
            max_price = round(pptb * max_tb, 2)
        except Exception:
            max_price = 900.0
    if max_price is None:
        max_price = 900.0
    min_price = stored.get("min_price")
    # se veio TB, ignora tb e mantém só preço
    # cria produto único
    raw_prod = {
        "id": stored.get("id") or "hd-6-12tb",
        "name": stored.get("name") or "HD 6-12TB",
        "queries": legacy_queries,
        "pages": legacy_pages,
        "search_url": legacy_search_url,
        "playwright_only": legacy_pw,
        "min_price": min_price,
        "max_price": max_price,
        "junk_pattern": legacy_junk,
        "enabled": True,
    }
    prod = _normalize_product(raw_prod, set())
    # força id legado para compatibilidade histórica
    prod["id"] = "hd-6-12tb"
    out = {
        "products": [prod],
        "telegram_token": stored.get("telegram_token"),
        "telegram_chat_id": stored.get("telegram_chat_id"),
        "notify_price_change_pct": _normalize_pct(stored.get("notify_price_change_pct")),
        "tuner_llm": _normalize_tuner_llm(stored.get("tuner_llm") or {}),
    }
    return out


def load_config():
    """Carrega config persistida de ~/.olx/config.json com fallback e migração."""
    cfg = {"products": [dict(DEFAULT_PRODUCT)], "telegram_token": None, "telegram_chat_id": None,
           "notify_price_change_pct": DEFAULT_NOTIFY_PRICE_CHANGE_PCT, "tuner_llm": dict(DEFAULT_LLM_CONFIG)}
    stored = None
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                stored = json.load(f) or {}
        except Exception:
            stored = None
    if stored is not None:
        try:
            migrated = _migrate_legacy_config(stored)
            cfg = migrated
        except Exception:
            pass
    # compat: telegram.json legado -> injeta se não já presente
    try:
        tg_path = os.path.join(STATE_DIR, "telegram.json")
        if os.path.exists(tg_path):
            with open(tg_path) as f:
                tg = json.load(f) or {}
            if tg.get("token") and not cfg.get("telegram_token"):
                cfg["telegram_token"] = tg["token"]
            if tg.get("chat_id") and not cfg.get("telegram_chat_id"):
                cfg["telegram_chat_id"] = tg["chat_id"]
    except Exception:
        pass
    # env override
    if os.environ.get("OLX_TG_TOKEN"):
        cfg["telegram_token"] = os.environ["OLX_TG_TOKEN"]
    if os.environ.get("OLX_TG_CHAT_ID"):
        try:
            cfg["telegram_chat_id"] = int(os.environ["OLX_TG_CHAT_ID"])
        except Exception:
            cfg["telegram_chat_id"] = os.environ["OLX_TG_CHAT_ID"]
    # garante tuner_llm normalizado (mesmo quando stored tinha apenas products)
    try:
        if not isinstance(cfg.get("tuner_llm"), dict):
            cfg["tuner_llm"] = dict(DEFAULT_LLM_CONFIG)
        else:
            cfg["tuner_llm"] = _normalize_tuner_llm(cfg["tuner_llm"])
        # se stored não tinha tuner_llm, tenta ler do arquivo bruto para não perder custom antes de migração
        if stored and isinstance(stored, dict) and "tuner_llm" in stored and stored["tuner_llm"] is not None:
            # já migrado acima, mas garante que campos mascarados não sobrescrevam
            pass
    except Exception:
        cfg["tuner_llm"] = dict(DEFAULT_LLM_CONFIG)
    # garante pelo menos 1 produto
    if not cfg.get("products"):
        cfg["products"] = [dict(DEFAULT_PRODUCT)]
    # normaliza produtos finais (garante campos)
    # não re-slugifica ids já existentes para não quebrar deals, apenas garante defaults
    normalized = []
    seen = set()
    for raw in cfg["products"]:
        # para produtos já com id, evita colisão artificial
        if raw.get("id") in seen:
            # gera novo id se duplicado
            raw = dict(raw)
            raw["id"] = f"{raw['id']}-{len(seen)}"
        # validação leve sem mudar id se já existe (exceto duplicado)
        np = _normalize_product(raw, set()) if raw.get("id") not in seen else _normalize_product(raw, seen)
        # se id foi gerado diferente e original tinha id, preserva original se não colide
        if raw.get("id") and raw["id"] not in seen:
            np["id"] = _slugify(str(raw["id"]))
        seen.add(np["id"])
        normalized.append(np)
    cfg["products"] = normalized
    return cfg


def save_config(patch):
    """Persiste config em ~/.olx/config.json. Patch pode conter products e/ou telegram_*."""
    os.makedirs(STATE_DIR, exist_ok=True)
    base = load_config()
    patch = patch or {}

    # se patch contém products, substitui lista (com normalização)
    if "products" in patch:
        raw_products = patch["products"]
        if not isinstance(raw_products, list):
            raise ValueError("products deve ser lista")
        if len(raw_products) > 100:
            raise ValueError("máximo 100 produtos")
        products = []
        seen = set()
        for raw in raw_products:
            if not isinstance(raw, dict):
                continue
            np = _normalize_product(raw, seen)
            seen.add(np["id"])
            products.append(np)
        if not products:
            raise ValueError("products não pode ser vazio")
        base["products"] = products

    # telegram
    for k in ("telegram_token", "telegram_chat_id"):
        if k in patch:
            v = patch[k]
            if v == "":
                v = None
            base[k] = v

    # threshold de re-notificação por mudança de preço (%)
    if "notify_price_change_pct" in patch:
        base["notify_price_change_pct"] = _normalize_pct(patch["notify_price_change_pct"])

    # tuner_llm
    if "tuner_llm" in patch:
        raw_llm = patch["tuner_llm"]
        if raw_llm is None:
            base["tuner_llm"] = dict(DEFAULT_LLM_CONFIG)
        elif not isinstance(raw_llm, dict):
            raise ValueError("tuner_llm deve ser objeto")
        else:
            # mascaramento: se api_key veio mascarada (***), mantém stored
            # caller app.py já trata _looks_masked, mas aqui também preserva
            if raw_llm.get("api_key") is not None and isinstance(raw_llm.get("api_key"), str) and raw_llm.get("api_key", "").startswith("***"):
                # mantém valor existente
                raw_llm = dict(raw_llm)
                raw_llm["api_key"] = base.get("tuner_llm", {}).get("api_key")
            base["tuner_llm"] = _normalize_tuner_llm(raw_llm)
        # garante base_url coerente
        if base["tuner_llm"]["provider"] == "mock":
            base["tuner_llm"]["base_url"] = ""

    # compat: se patch trouxe campos legados flat (queries, max_price etc) sem products,
    # aplica no primeiro produto (para chamadas antigas da API)
    legacy_flat_keys = {"queries", "pages", "search_url", "playwright_only", "min_price", "max_price", "junk_pattern", "max_price_per_tb", "min_tb", "max_tb"}
    if legacy_flat_keys & set(patch.keys()) and "products" not in patch:
        # mapeia para primeiro produto
        if not base["products"]:
            base["products"] = [dict(DEFAULT_PRODUCT)]
        first = dict(base["products"][0])
        for k in legacy_flat_keys:
            if k in patch:
                if k == "max_price_per_tb":
                    try:
                        pptb = float(patch[k])
                        max_tb = float(patch.get("max_tb", base["products"][0].get("max_price", 900)/75 if base["products"][0].get("max_price") else 12))
                        first["max_price"] = round(pptb * 12, 2)
                    except Exception:
                        pass
                elif k in ("min_tb", "max_tb"):
                    continue
                else:
                    first[k] = patch[k]
        # re-normaliza primeiro produto mantendo id
        pid = first.get("id")
        np = _normalize_product(first, set(p["id"] for i, p in enumerate(base["products"]) if i != 0))
        np["id"] = pid  # preserva id original
        base["products"][0] = np

    out = {
        "products": base["products"],
        "notify_price_change_pct": _normalize_pct(base.get("notify_price_change_pct")),
        "tuner_llm": _normalize_tuner_llm(base.get("tuner_llm") or {}),
    }
    if base.get("telegram_token") is not None:
        out["telegram_token"] = base["telegram_token"]
    if base.get("telegram_chat_id") is not None:
        out["telegram_chat_id"] = base["telegram_chat_id"]

    with open(CONFIG_FILE, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # compat: também atualiza telegram.json se token/chat_id mudaram
    if "telegram_token" in out or "telegram_chat_id" in out:
        try:
            tg_path = os.path.join(STATE_DIR, "telegram.json")
            tg = {}
            if os.path.exists(tg_path):
                with open(tg_path) as ft:
                    tg = json.load(ft) or {}
            if "telegram_token" in out:
                tg["token"] = out["telegram_token"]
            if "telegram_chat_id" in out:
                tg["chat_id"] = out["telegram_chat_id"]
            with open(tg_path, "w") as ft:
                json.dump(tg, ft, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return out


def get_junk_re(pattern=None):
    if pattern is None:
        cfg = load_config()
        # pega do primeiro produto como fallback legado
        prods = cfg.get("products") or []
        pattern = prods[0].get("junk_pattern", JUNK_RE.pattern) if prods else JUNK_RE.pattern
    try:
        return re.compile(pattern, re.I) if pattern else re.compile(r"(?!x)x")
    except re.error:
        return JUNK_RE


def _compile_exclude_res(keywords):
    """Compila regex híbrida por termo: (?<![a-z]) + literal escapado, case-insensitive.

    Lookbehind ASCII bloqueia letra imediatamente anterior ("RGB" não casa "GB"),
    mas dígito/espaço/início de título casam ("HD 500GB" casa "GB" — sem boundary
    entre "0" e "G"). re.escape garante termo literal.
    """
    res = []
    for kw in keywords or []:
        try:
            res.append(re.compile(r"(?<![a-z])" + re.escape(str(kw)), re.I))
        except re.error:
            continue
    return res


def _is_orphan(product_id: str, active_ids: set) -> bool:
    return str(product_id or "").strip() not in active_ids


def _evaluate_current(deal: dict, product_cfg: dict | None, active_ids: set) -> tuple:
    """Reavalia deal isolado com filtros vigentes. Não toca disco.

    Retorna (matched_current: bool, discard_reason_current: str|None).
    Ordem: orphan -> include -> junk -> exclude -> price -> match.
    price None => (False, "price").
    """
    pid = str(deal.get("product_id") or "").strip()
    if _is_orphan(pid, active_ids):
        return False, DISCARD_ORPHAN
    if product_cfg is None:
        return False, DISCARD_ORPHAN
    title = str(deal.get("title") or "")
    price = deal.get("price")
    if price is None:
        return False, DISCARD_PRICE
    try:
        pf = float(price)
    except Exception:
        return False, DISCARD_PRICE
    inc = str(product_cfg.get("include_pattern") or "").strip()
    if inc:
        try:
            if not re.compile(inc, re.I).search(title):
                return False, DISCARD_INCLUDE
        except re.error:
            pass
    jp = str(product_cfg.get("junk_pattern") or "").strip()
    if jp:
        try:
            if re.compile(jp, re.I).search(title):
                return False, DISCARD_JUNK
        except re.error:
            pass
    eks = product_cfg.get("exclude_keywords") or []
    ex_res = _compile_exclude_res(eks)
    hit = next((kw for kw, rx in zip(eks, ex_res) if rx.search(title)), None)
    if hit is not None:
        return False, DISCARD_EXCLUDE
    min_p = product_cfg.get("min_price")
    max_p = product_cfg.get("max_price")
    if min_p is not None:
        try:
            if pf < float(min_p):
                return False, DISCARD_PRICE
        except Exception:
            pass
    if max_p is not None:
        try:
            if pf > float(max_p):
                return False, DISCARD_PRICE
        except Exception:
            pass
    return True, None


def _evaluate_current_detailed(deal: dict, product_cfg: dict | None, active_ids: set) -> tuple:
    """Variante que detalha exclude:<term> para by_reason."""
    m, r = _evaluate_current(deal, product_cfg, active_ids)
    if r == DISCARD_EXCLUDE and product_cfg is not None:
        eks = product_cfg.get("exclude_keywords") or []
        ex_res = _compile_exclude_res(eks)
        hit = next((kw for kw, rx in zip(eks, ex_res) if rx.search(str(deal.get("title") or ""))), None)
        if hit:
            r = f"exclude:{str(hit).lower()}"
    return m, r


# Fase 6: shim removido — domain.filters é fonte única (_evaluate_current etc)


def _purge_backup_path() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return os.path.join(STATE_DIR, f"deals.json.bak-purge-{ts}")


def _purge_catalog(deals: list, predicate) -> tuple:
    """Separa deals em (to_purge, remaining) via predicate(deal)->bool."""
    to_purge = []
    remaining = []
    for d in deals:
        if predicate(d):
            to_purge.append(d)
        else:
            remaining.append(d)
    return to_purge, remaining


def purge_deals(predicate, dry_run: bool = False) -> dict:
    """Remove de deals.json todos onde predicate(deal) is True.

    Cria backup antes de escrever quando não dry_run. Escreve via tmp+replace.
    Nunca toca notified.json.
    Retorna {"purged": n, "remaining": m, "backup_path": str|None}
    """
    import shutil
    deals = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                deals = json.load(f) or []
            if not isinstance(deals, list):
                deals = []
        except Exception:
            deals = []
        for d in deals:
            if not d.get("product_id"):
                d["product_id"] = "hd-6-12tb"
                d["product_name"] = d.get("product_name") or "HD 6-12TB"
    to_purge, remaining = _purge_catalog(deals, predicate)
    if dry_run:
        return {"purged": len(to_purge), "remaining": len(remaining), "backup_path": None,
                "to_purge": to_purge, "remaining_deals": remaining}
    backup_path = _purge_backup_path()
    os.makedirs(STATE_DIR, exist_ok=True)
    if os.path.exists(HISTORY_FILE):
        shutil.copy2(HISTORY_FILE, backup_path)
    tmp = HISTORY_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(remaining, f, ensure_ascii=False, indent=2)
    os.replace(tmp, HISTORY_FILE)
    return {"purged": len(to_purge), "remaining": len(remaining), "backup_path": backup_path,
            "to_purge": to_purge, "remaining_deals": remaining}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def parse_price(text):
    m = PRICE_RE.search(text)
    if not m:
        return None
    raw = m.group(1).strip()
    # Formato brasileiro: "1.200,50" -> 1200.50, "750" -> 750
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    else:
        # sem vírgula: pode ser "1.200" (milhar) ou "750.50" (decimal US)
        # heurística: se tem ponto e último grupo tem 3 dígitos => milhar
        if raw.count(".") == 1 and len(raw.split(".")[-1]) == 3:
            raw = raw.replace(".", "")
        elif raw.count(".") > 1:
            raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


def ad_id(href):
    m = re.search(r"-(\d+)(?:\.html|/)?$", href)
    return m.group(1) if m else href


def extract_cards_from_html(html):
    """Parsa HTML (curl_cffi ou DOM) e devolve lista de dicts."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for a in soup.select('a[data-testid="adcard-link"]'):
        href = a.get("href", "").strip()
        title = (a.get_text(" ", strip=True) or "").strip()
        price = None
        node = a.parent
        for _ in range(6):
            if not node:
                break
            txt = node.get_text(" ", strip=True)
            p = parse_price(txt)
            if p is not None:
                price = p
                break
            node = node.parent
        if not title:
            continue
        cards.append({
            "id": ad_id(href),
            "title": title,
            "price": price,
            "url": href if href.startswith("http") else "https://www.olx.com.br" + href,
        })
    return cards


def curl_fetch(url, cookies):
    from curl_cffi import requests as cf
    s = cf.Session(impersonate="chrome124")
    if cookies:
        for c in cookies:
            s.cookies.set(c["name"], c["value"], domain=c.get("domain", ".olx.com.br"))
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    r = s.get(url, timeout=30)
    return r.status_code, r.text


def playwright_fetch(url, pw=None, browser=None, cookiejar_path=COOKIE_JAR):
    """Fetch via Playwright DOM. Se `pw`/`browser` forem None, cria e fecha os próprios."""
    from playwright.sync_api import sync_playwright
    own_pw, own_browser = False, False
    if pw is None:
        pw = sync_playwright().start()
        own_pw = True
    if browser is None:
        browser = pw.chromium.launch(headless=True)
        own_browser = True
    ctx_kwargs = dict(
        user_agent=UA,
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
        viewport={"width": 1440, "height": 900},
    )
    if os.path.exists(cookiejar_path):
        with open(cookiejar_path) as f:
            ctx_kwargs["storage_state"] = json.load(f)
    ctx = browser.new_context(**ctx_kwargs)
    pg = ctx.new_page()
    pg.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')
    try:
        resp = pg.goto(url, timeout=45000, wait_until="domcontentloaded")
        if resp and resp.status == 200:
            try:
                pg.wait_for_selector('[data-testid="adcard-link"]', timeout=20000)
            except Exception:
                log("aviso: selector de cards não apareceu em 20s")
            pg.wait_for_timeout(1500)
            html = pg.content()
            return html
        return None
    except Exception as e:
        log(f"playwright error: {e}")
        return None
    finally:
        ctx.close()
        if own_browser:
            browser.close()
        if own_pw:
            pw.stop()


def playwright_login(cookies_path=COOKIE_JAR, search_url=None):
    from playwright.sync_api import sync_playwright
    if search_url is None:
        cfg = load_config()
        prods = cfg.get("products") or []
        search_url = prods[0].get("search_url", SEARCH_URL) if prods else SEARCH_URL
    log("Login: abrindo navegador para resolver Cloudflare e capturar cookies...")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=UA, locale="pt-BR", timezone_id="America/Sao_Paulo",
            viewport={"width": 1440, "height": 900},
        )
        pg = ctx.new_page()
        pg.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')
        resp = pg.goto(search_url.format(query="hd", page=1), timeout=45000, wait_until="domcontentloaded")
        try:
            pg.wait_for_selector('[data-testid="adcard-link"]', timeout=20000)
        except Exception:
            log("aviso: selector de cards não apareceu em 20s")
        pg.wait_for_timeout(1500)
        cards = pg.locator('[data-testid="adcard-link"]').count()
        log(f"status={resp.status if resp else None} cards={cards}")
        storage = ctx.storage_state()
        os.makedirs(os.path.dirname(cookies_path), exist_ok=True)
        with open(cookies_path, "w") as f:
            json.dump(storage, f)
        log(f"cookies salvos em {cookies_path}")
        ctx.close()
        browser.close()
    return cards > 0


def load_cookies_dict():
    try:
        with open(COOKIE_JAR) as f:
            ss = json.load(f)
        return ss.get("cookies", [])
    except Exception:
        return []


def _scrape_single_product(product, cookies, pw_handle, browser, cookies_refreshed_flag, log_fn, progress_cb, global_seen, stats=None):
    """Scrape de um único produto genérico. Retorna lista de matches.

    Se stats for dict mutável, preenche contadores:
      total_cards, matched, discarded_price, discarded_junk, discarded_exclude, discarded_dedupe
    total_cards = cards brutos extraídos antes de qualquer filtro.
    """
    queries = product.get("queries") or []
    pages = product.get("pages", 2)
    search_url = product.get("search_url", SEARCH_URL)
    use_pw_only = bool(product.get("playwright_only", False))
    min_price = product.get("min_price")
    max_price = product.get("max_price")
    junk_pattern = product.get("junk_pattern", "")
    junk_re = None
    if junk_pattern:
        try:
            junk_re = re.compile(junk_pattern, re.I)
        except re.error:
            junk_re = JUNK_RE
    exclude_keywords = product.get("exclude_keywords") or []
    exclude_res = _compile_exclude_res(exclude_keywords)
    excluded_count = 0
    include_pattern = product.get("include_pattern") or ""
    include_re = None
    if include_pattern:
        try:
            include_re = re.compile(include_pattern, re.I)
        except re.error:
            include_re = None

    product_id = product.get("id")
    product_name = product.get("name")
    results = []
    seen_local = set()
    # init stats counters if provided
    if stats is not None:
        for k in ("total_cards", "matched", "discarded_price", "discarded_junk", "discarded_exclude", "discarded_dedupe"):
            stats.setdefault(k, 0)

    for q in queries:
        for page in range(1, pages + 1):
            if progress_cb:
                try:
                    progress_cb({"product_id": product_id, "product_name": product_name, "query": q, "page": page, "total_products": None, "total_queries": len(queries), "total_pages": pages})
                except Exception:
                    pass
            url = search_url.format(query=q.replace(" ", "+"), page=page)
            cards = []
            used = "curl"
            if not use_pw_only:
                try:
                    status, html = curl_fetch(url, cookies)
                    cards = extract_cards_from_html(html) if status == 200 else []
                except Exception as e:
                    log_fn(f"curl error [{product_name} / {q} p{page}]: {e}")
                    cards = []
                if not cards:
                    used = "playwright"
                    log_fn(f"curl falhou/0 cards [{product_name} / {q} p{page}]; fallback Playwright")
                    if not cookies_refreshed_flag[0]:
                        ok = playwright_login(search_url=search_url)
                        cookies_refreshed_flag[0] = True
                        if ok:
                            cookies[:] = load_cookies_dict()
                    html = playwright_fetch(url, pw=pw_handle, browser=browser)
                    if html:
                        cards = extract_cards_from_html(html)
            else:
                used = "playwright"
                html = playwright_fetch(url, pw=pw_handle, browser=browser)
                cards = extract_cards_from_html(html) if html else []

            if not cards:
                log_fn(f"  [vazio] {product_name} | {q} p{page}")
            else:
                log_fn(f"  {used}: {product_name} | {q} p{page} -> {len(cards)} cards")

            for c in cards:
                c["query"] = q
                c["page"] = page
                c["product_id"] = product_id
                c["product_name"] = product_name
                c["fetched_at"] = datetime.now(timezone.utc).isoformat()
                c["matched"] = False
                c["discard_reason"] = None
                if stats is not None:
                    stats["total_cards"] += 1
                # 0) sem preço — descarta completamente (não persiste) MAS conta como discarded_price
                if c["price"] is None:
                    if stats is not None:
                        stats["discarded_price"] += 1
                    continue
                # 1) include_pattern (intenção) — antes de preço; não persiste no histórico se falhar (histórico = só intenção-relevante, menos price)
                if include_re and not include_re.search(c["title"]):
                    if stats is not None:
                        stats["discarded_junk"] += 1
                    # não persiste intenção-irrelevante
                    continue
                # 2) junk (intenção) — não persiste
                if junk_re and junk_re.search(c["title"]):
                    if stats is not None:
                        stats["discarded_junk"] += 1
                    continue
                # 3) exclude (intenção) — não persiste
                hit = next((kw for kw, rex in zip(exclude_keywords, exclude_res) if rex.search(c["title"])), None)
                if hit is not None:
                    excluded_count += 1
                    if stats is not None:
                        stats["discarded_exclude"] += 1
                    log_fn(f"    [exclude] {c['title']} -> termo '{hit}'")
                    continue
                # 4) faixa de preço (min/max do produto) — só após intenção passar; virará matched=false price mas já é intenção-relevante
                if min_price is not None and c["price"] < float(min_price):
                    if stats is not None:
                        stats["discarded_price"] += 1
                    c["discard_reason"] = DISCARD_PRICE
                    results.append(c)
                    continue
                if max_price is not None and c["price"] > float(max_price):
                    if stats is not None:
                        stats["discarded_price"] += 1
                    c["discard_reason"] = DISCARD_PRICE
                    results.append(c)
                    continue
                # 5) dedupe
                key = f"{product_id}:{c['id']}"
                if key in global_seen or key in seen_local:
                    if stats is not None:
                        stats["discarded_dedupe"] += 1
                    continue
                seen_local.add(key)
                global_seen.add(key)
                c["matched"] = True
                c["discard_reason"] = None
                if stats is not None:
                    stats["matched"] += 1
                results.append(c)
                log_fn(f"    >>> MATCH [{product_name}] {c['title']} | R${c['price']:.2f} | {c['url']}")
            time.sleep(random.uniform(1.5, 3.5))
    if excluded_count:
        log_fn(f"[{product_name}] {excluded_count} cards descartados por exclude_keywords")
    return results


def scrape(products_or_queries, pages=None, use_playwright_only=None, min_price=None, max_price=None,
           junk_pattern=None, search_url=None, log_fn=None, progress_cb=None, stats_out=None, **kwargs):
    """
    Busca genérica. Assinatura flexível:

    - Nova: scrape(products: list[dict], log_fn, progress_cb)
      onde products é lista de produtos (cada um com id, name, queries, pages, search_url, min_price, max_price, junk_pattern, enabled)
    - Legada: scrape(queries: list[str], pages: int, use_playwright_only, min_tb, max_tb, max_price_per_tb, junk_pattern, search_url, ...)

    Para compat, detecta o tipo do primeiro arg.
    """
    if log_fn is None:
        log_fn = log

    # kwargs legados: min_tb, max_tb, max_price_per_tb
    legacy_ptb = kwargs.get("max_price_per_tb")
    if legacy_ptb is not None and max_price is None:
        try:
            # estima max_price como pptb * 12 (assume 12TB max)
            max_price = float(legacy_ptb) * 12
        except Exception:
            pass

    # detecta chamada legada: primeiro arg é lista de strings queries
    is_legacy_queries = False
    if isinstance(products_or_queries, list) and products_or_queries:
        if isinstance(products_or_queries[0], str):
            is_legacy_queries = True
        elif isinstance(products_or_queries[0], dict) and "queries" in products_or_queries[0]:
            is_legacy_queries = False
        elif isinstance(products_or_queries, list) and all(isinstance(x, str) for x in products_or_queries):
            is_legacy_queries = True

    # se legado, converte para produto sintético
    if is_legacy_queries:
        queries = products_or_queries
        # pages pode vir como None -> pega default
        if pages is None:
            pages = 2
        # search_url fallback
        if search_url is None:
            cfg = load_config()
            prods = cfg.get("products") or []
            search_url = prods[0].get("search_url", SEARCH_URL) if prods else SEARCH_URL
        synthetic = {
            "id": "legacy",
            "name": "legacy",
            "queries": queries,
            "pages": pages,
            "search_url": search_url,
            "playwright_only": bool(use_playwright_only) if use_playwright_only is not None else False,
            "min_price": min_price,
            "max_price": max_price,
            "junk_pattern": junk_pattern or JUNK_RE.pattern,
            "enabled": True,
        }
        products = [synthetic]
    else:
        # nova assinatura: products_or_queries é lista de produtos ou None
        if products_or_queries is None:
            cfg = load_config()
            products = [p for p in cfg.get("products", []) if p.get("enabled", True)]
        else:
            products = products_or_queries
        # filtra habilitados se não for chamada explícita com todos
        # se products veio do load_config já filtramos habilitados no caller
        if not isinstance(products, list):
            products = [products]

    # normaliza products (garante defaults)
    # kwargs force_enabled=True permite scrape de drafts (enabled:false) quando app.py pede explicitamente
    force_enabled = bool(kwargs.get("force_enabled") or kwargs.get("force"))
    normalized = []
    seen_ids = set()
    for raw in products:
        if not isinstance(raw, dict):
            continue
        if not raw.get("enabled", True) and not force_enabled:
            continue
        # se veio sem id/name, normaliza
        np = _normalize_product(raw, seen_ids)
        # preserva id original se já existia
        if raw.get("id"):
            np["id"] = _slugify(str(raw["id"]))
        seen_ids.add(np["id"])
        normalized.append(np)

    if not normalized:
        log_fn("Nenhum produto habilitado para busca")
        if stats_out is not None:
            stats_out.update({"total_cards": 0, "matched": 0, "discarded_price": 0, "discarded_junk": 0, "discarded_exclude": 0, "discarded_dedupe": 0, "per_product": {}})
        return []

    # overrides globais se passados (ex: app.py passa min_price global? agora não, mas suporta)
    # se max_price/min_price/junk_pattern/search_url foram passados na chamada nova, aplicam para todos?
    # Não: nova API passa products já com seus valores; overrides são tratados no app.py antes.

    cookies = load_cookies_dict()
    cookies_refreshed = [False]

    # decide se precisa de playwright global
    any_pw_only = any(p.get("playwright_only") for p in normalized)
    # se algum produto exige playwright_only, inicia browser compartilhado
    pw_handle, browser = None, None
    # também inicia se fallback for necessário; para simplificar inicia só se necessário
    # mas para manter lógica antiga, avalia: se não for pw_only global, inicia lazily no fallback
    # aqui iniciamos se any_pw_only ou se cookies vazios? mantém lógica: inicia se use_playwright_only legado
    # Para genérico, se qualquer produto for pw_only, inicia
    if any_pw_only or (use_playwright_only):
        from playwright.sync_api import sync_playwright
        pw_handle = sync_playwright().start()
        browser = pw_handle.chromium.launch(headless=True)
    else:
        # inicia lazily só se fallback precisar; mas _scrape_single_product espera pw_handle/brower possivelmente None
        # fallback vai criar temporário se None? atualmente fallback usa pw_handle/brower passados; se None, playwright_fetch cria próprio
        pw_handle, browser = None, None

    # para fallback playwright compartilhado quando não foi iniciado, precisamos iniciar ao primeiro fallback
    # simplifica: se não iniciou e precisar fallback, _scrape_single_product vai criar browser temporário via playwright_fetch(own)
    # então ok deixar None

    try:
        all_results = []
        global_seen = set()
        total_products = len(normalized)
        aggregated = {"total_cards": 0, "matched": 0, "discarded_price": 0, "discarded_junk": 0, "discarded_exclude": 0, "discarded_dedupe": 0, "per_product": {}}
        for idx, prod in enumerate(normalized):
            # atualiza progress total_products
            if progress_cb:
                try:
                    progress_cb({"product_id": prod["id"], "product_name": prod["name"], "query": prod["queries"][0] if prod["queries"] else "", "page": 1, "total_products": total_products, "current_product_index": idx+1})
                except Exception:
                    pass
            per = {"total_cards": 0, "matched": 0, "discarded_price": 0, "discarded_junk": 0, "discarded_exclude": 0, "discarded_dedupe": 0}
            res = _scrape_single_product(prod, cookies, pw_handle, browser, cookies_refreshed, log_fn, progress_cb, global_seen, stats=per)
            aggregated["total_cards"] += per["total_cards"]
            aggregated["matched"] += per["matched"]
            aggregated["discarded_price"] += per["discarded_price"]
            aggregated["discarded_junk"] += per["discarded_junk"]
            aggregated["discarded_exclude"] += per["discarded_exclude"]
            aggregated["discarded_dedupe"] += per["discarded_dedupe"]
            aggregated["per_product"][prod["id"]] = per
            all_results.extend(res)
            # se produto era synthetic legacy e tinha max_price etc, já tratado
        if stats_out is not None:
            stats_out.update(aggregated)
        return all_results
    finally:
        if pw_handle:
            try:
                browser.close()
            except Exception:
                pass
            try:
                pw_handle.stop()
            except Exception:
                pass


def save_history(results):
    os.makedirs(STATE_DIR, exist_ok=True)
    old = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                old = json.load(f)
        except Exception:
            old = []
    # migração: deals antigos sem product_id -> atribui ao produto legado
    for r in old:
        if not r.get("product_id"):
            r["product_id"] = "hd-6-12tb"
            r["product_name"] = r.get("product_name") or "HD 6-12TB"
    by_id = {}
    for r in old + results:
        # chave composta product_id+id para não colidir entre produtos
        key = f"{r.get('product_id','')}:{r.get('id')}"
        by_id[key] = r
    all_ = list(by_id.values())[-HISTORY_MAX:]
    new = []
    seen_keys = set(f"{r.get('product_id','')}:{r.get('id')}" for r in old)
    for r in results:
        key = f"{r.get('product_id','')}:{r.get('id')}"
        if key not in seen_keys:
            new.append(r)
    with open(HISTORY_FILE, "w") as f:
        json.dump(all_, f, ensure_ascii=False, indent=2)
    return new


def load_telegram_config():
    cfg = {}
    path = os.path.join(STATE_DIR, "telegram.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                cfg = json.load(f) or {}
        except Exception:
            cfg = {}
    token = os.environ.get("OLX_TG_TOKEN") or cfg.get("token")
    chat_id = os.environ.get("OLX_TG_CHAT_ID") or cfg.get("chat_id")
    # também tenta do config.json novo
    try:
        c = load_config()
        if c.get("telegram_token"):
            token = c["telegram_token"]
        if c.get("telegram_chat_id"):
            chat_id = c["telegram_chat_id"]
    except Exception:
        pass
    return token, chat_id


def _price_delta_pct(prev_price, new_price):
    """|Δ| percentual entre preços; None se incomparáveis."""
    try:
        p0 = float(prev_price)
        p1 = float(new_price)
    except (TypeError, ValueError):
        return None
    if p0 <= 0:
        return 0.0 if p1 == p0 else float("inf")
    return abs(p1 - p0) / p0 * 100.0


def _split_blocks_into_messages(blocks, limit=TELEGRAM_MSG_LIMIT):
    """Divide blocos de texto em mensagens <= limit chars (Telegram ~4096).

    Substitui o truncamento antigo, que descartava deals silenciosamente.
    """
    messages = []
    cur = ""
    for b in blocks:
        b = str(b)
        cand = b if not cur else cur + "\n\n" + b
        if len(cand) <= limit:
            cur = cand
            continue
        if cur:
            messages.append(cur)
            cur = ""
        while len(b) > limit:
            messages.append(b[:limit])
            b = b[limit:]
        cur = b
    if cur:
        messages.append(cur)
    return messages


def _telegram_send(token, chat_id, text, timeout=30):
    """Envia 1 mensagem ao Telegram. True se HTTP 200."""
    from curl_cffi import requests as cf
    r = cf.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=timeout,
    )
    if r.status_code == 200:
        return True
    log(f"telegram erro: HTTP {r.status_code} {r.text[:200]}")
    return False


def notify(new_deals):
    """Notifica deals no Telegram com dedupe via ~/.olx/notified.json.

    Classificação por chave "{product_id}:{ad_id}" contra o registro:
    - inédita -> envia como novo
    - existente com |Δpreço| <= notify_price_change_pct -> pula
    - existente com |Δpreço| > threshold -> re-notifica como "Preço atualizado"

    Mensagens longas são divididas (sem truncamento). O registro só é
    persistido após HTTP 200 de todas as mensagens — falha não persiste,
    dando retry natural no próximo scrape. Comportamento defensivo mantido
    (não propaga exceção), mas contadores refletem a falha via chave "erro".

    Retorna {"enviados": int, "pulados": int, "re_notificados": int[, "erro": str]}.
    """
    counters = {"enviados": 0, "pulados": 0, "re_notificados": 0}
    if not new_deals:
        return counters
    token, chat_id = load_telegram_config()
    if not token or not chat_id:
        counters["erro"] = "telegram não configurado (token/chat_id ausentes)"
        log(counters["erro"] + "; pulando envio.")
        return counters
    try:
        reg = load_notified()
        threshold = get_notify_price_change_pct()
        now_iso = datetime.now(timezone.utc).isoformat()
        by_product = {}
        pending = {}
        n_novos = n_preco = 0
        for d in new_deals:
            key = f"{d.get('product_id', '')}:{d.get('id')}"
            price = d.get("price")
            prev = reg.get(key)
            kind = "novo"
            if prev is not None:
                delta = _price_delta_pct(prev.get("price"), price)
                if delta is None or delta <= threshold:
                    counters["pulados"] += 1
                    continue
                kind = "preco"
            entry = {"at": now_iso}
            if price is not None:
                try:
                    entry["price"] = float(price)
                except (TypeError, ValueError):
                    pass
            pending[key] = entry
            pn = d.get("product_name") or d.get("product_id") or "Produto"
            by_product.setdefault(pn, []).append((d, kind))
            if kind == "preco":
                n_preco += 1
            else:
                n_novos += 1
        if not pending:
            return counters
        blocks = []
        for pn, items in by_product.items():
            blocks.append(f"📦 {pn} — {len(items)} anúncio(s):")
            for d, kind in items:
                price_s = f"R$ {d['price']:.2f}" if d.get("price") is not None else "sem preço"
                head = "🔄 Preço atualizado" if kind == "preco" else "🆕 Novo"
                blocks.append(f"{head}\n{d.get('title', '')}\n{price_s}\n{d.get('url', '')}")
        messages = _split_blocks_into_messages(blocks)
        all_ok = True
        for msg in messages:
            if not _telegram_send(token, chat_id, msg):
                all_ok = False
                break
        if not all_ok:
            counters["erro"] = "falha no envio; registro não persistido (retry no próximo scrape)"
            return counters
        save_notified(pending)
        counters["enviados"] = n_novos
        counters["re_notificados"] = n_preco
        log(f"telegram enviado: {n_novos} novo(s), {n_preco} preço atualizado ({len(messages)} mensagem(ns))")
        return counters
    except Exception as e:
        counters["erro"] = str(e)
        log(f"telegram error: {e}")
        return counters


def main():
    ap = argparse.ArgumentParser(description="OLX deals — busca genérica")
    ap.add_argument("--login", action="store_true", help="capturar cookies via Playwright")
    ap.add_argument("--scrape", action="store_true", help="executar busca de todos os produtos")
    ap.add_argument("--product", type=str, default=None, help="filtrar por id de produto (scrape só este)")
    ap.add_argument("--queries", nargs="*", default=None, help="termos de busca (legado: usa produto sintético)")
    ap.add_argument("--pages", type=int, default=None, help="páginas por query (legado)")
    ap.add_argument("--playwright-only", action="store_true", help="não usar curl_cffi")
    ap.add_argument("--min-price", type=float, default=None, help="filtro preço mínimo (legado)")
    ap.add_argument("--max-price", type=float, default=None, help="filtro preço máximo")
    # compat legado tb
    ap.add_argument("--max-price-per-tb", type=float, default=None, help="(legado) teto R$/TB -> convertido para max_price")
    ap.add_argument("--min-tb", type=float, default=None, help="(legado) ignorado")
    ap.add_argument("--max-tb", type=float, default=None, help="(legado) ignorado")
    args = ap.parse_args()

    cfg = load_config()

    if args.login or not os.path.exists(COOKIE_JAR):
        # usa search_url do primeiro produto
        su = cfg["products"][0].get("search_url", SEARCH_URL) if cfg["products"] else SEARCH_URL
        ok = playwright_login(search_url=su)
        if not ok:
            log("AVISO: captura de cookies não mostrou cards; continuando mesmo assim.")

    if args.scrape:
        products = cfg.get("products", [])
        # filtra por id se pedido
        if args.product:
            products = [p for p in products if p["id"] == args.product]
            if not products:
                log(f"Produto {args.product} não encontrado")
                return
        # legado: se --queries passado, ignora products e usa sintético
        if args.queries is not None:
            queries = args.queries
            pages = args.pages if args.pages is not None else 2
            max_price = args.max_price
            if args.max_price_per_tb is not None and max_price is None:
                max_price = float(args.max_price_per_tb) * 12
            results = scrape(
                queries, pages,
                use_playwright_only=args.playwright_only,
                min_price=args.min_price,
                max_price=max_price,
                search_url=cfg["products"][0].get("search_url", SEARCH_URL) if cfg["products"] else SEARCH_URL,
            )
        else:
            # aplica overrides de preço/playwright se passados via CLI para todos os produtos
            if args.min_price is not None or args.max_price is not None or args.playwright_only or args.pages is not None:
                for p in products:
                    if args.min_price is not None:
                        p["min_price"] = args.min_price
                    if args.max_price is not None:
                        p["max_price"] = args.max_price
                    if args.pages is not None:
                        p["pages"] = args.pages
                    if args.playwright_only:
                        p["playwright_only"] = True
            results = scrape(products)
        matches = [r for r in results if r.get("matched")]
        log(f"=== {len(matches)} anúncios atendem filtro (genérico) ===")
        new = save_history(results)
        log(f"=== {len(new)} novos no history; anteriores em {HISTORY_FILE} ===")
        new_matched = [r for r in new if r.get("matched")]
        cnt = notify(new_matched)
        msg = (f"=== telegram: {cnt.get('enviados', 0)} enviados, "
               f"{cnt.get('pulados', 0)} já notificados (ignorados), "
               f"{cnt.get('re_notificados', 0)} re-notificados (mudança de preço)")
        if cnt.get("erro"):
            msg += f" | erro: {cnt['erro']}"
        log(msg + " ===")


# --- Fase 3: shims de persistência (Protocol+File+Fake) ---
_orig_load_config = load_config
_orig_save_config = save_config
_orig_save_history = save_history
_orig_purge_deals = purge_deals
_orig_load_notified = load_notified
_orig_save_notified = save_notified
_orig_remove_notified = remove_notified
_orig_clear_notified = clear_notified
_orig_load_runs = load_runs
_orig_save_runs = save_runs
_orig_append_run = append_run
_orig_get_run = get_run
_orig_delete_runs = delete_runs
_orig_load_schedules = load_schedules
_orig_save_schedules = save_schedules
_orig_record_schedule_run = record_schedule_run
_orig_get_last_run = get_last_run
try:
    from olx_deals.infrastructure.persistence.config_repo import FileConfigRepository as _FileConfigRepo
    from olx_deals.infrastructure.persistence.deals_repo import FileDealsRepository as _FileDealsRepo
    from olx_deals.infrastructure.persistence.notified_repo import FileNotifiedRepository as _FileNotifiedRepo
    from olx_deals.infrastructure.persistence.runs_repo import FileRunsRepository as _FileRunsRepo
    from olx_deals.infrastructure.persistence.schedules_repo import FileSchedulesRepository as _FileSchedulesRepo
    _cfg_repo = _FileConfigRepo()
    _deals_repo = _FileDealsRepo()
    _notified_repo = _FileNotifiedRepo()
    _runs_repo = _FileRunsRepo()
    _schedules_repo = _FileSchedulesRepo()
    def load_config(): return _cfg_repo.load()
    def save_config(p): return _cfg_repo.save(p)
    def save_history(r): return _deals_repo.save(r)
    def purge_deals(pred, dry_run=False): return _deals_repo.purge(pred, dry_run)
    def load_notified(): return _notified_repo.load()
    def save_notified(e): return _notified_repo.save(e)
    def remove_notified(k): return _notified_repo.remove(k)
    def clear_notified(): return _notified_repo.clear()
    def load_runs(): return _runs_repo.load()
    def save_runs(r): return _runs_repo.save(r)
    def append_run(e): return _runs_repo.append(e)
    def get_run(i): return _runs_repo.get(i)
    def delete_runs(before=None, product_id=None, status=None, source=None): return _runs_repo.delete(before, product_id, status, source)
    def load_schedules(): return _schedules_repo.load()
    def save_schedules(d): return _schedules_repo.save(d)
    def record_schedule_run(pid, status="ok", error=None, at_iso=None, matched_count=None, results_count=None): return _schedules_repo.record(pid, status, error, at_iso, matched_count, results_count)
    def get_last_run(pid): return _schedules_repo.get_last(pid)
except Exception as _e:
    pass

# --- Fase 4: shims de scraping (Fetcher/Parser + ScrapeService) ---
_orig_parse_price = parse_price
_orig_ad_id = ad_id
_orig_extract_cards = extract_cards_from_html
_orig_curl_fetch = curl_fetch
_orig_playwright_fetch = playwright_fetch
_orig_playwright_login = playwright_login
_orig_load_cookies_dict = load_cookies_dict
_orig_scrape_single_product = _scrape_single_product
_orig_scrape = scrape
try:
    from olx_deals.infrastructure.scraping.parser import (
        PRICE_RE as _NEW_PRICE_RE,
        ad_id as _new_ad_id,
        extract_cards_from_html as _new_extract,
        parse_price as _new_parse_price,
    )
    from olx_deals.infrastructure.scraping.curl_fetcher import curl_fetch as _new_curl_fetch  # type: ignore[no-redef]
    from olx_deals.infrastructure.scraping.curl_fetcher import load_cookies_dict as _new_load_cookies  # type: ignore[no-redef]
    from olx_deals.infrastructure.scraping.playwright_fetcher import (  # type: ignore[no-redef]
        playwright_fetch as _new_pw_fetch,
        playwright_login as _new_pw_login,
    )
    from olx_deals.infrastructure.scraping.fetcher import FakeFetcher as _NewFakeFetcher, Fetcher as _NewFetcher  # type: ignore[no-redef]
    from olx_deals.infrastructure.scraping.composite_fetcher import CompositeFetcher as _NewComposite  # type: ignore[no-redef]
    from olx_deals.application.scrape_service import _scrape_single_product as _new_single, scrape as _new_scrape  # type: ignore[no-redef]

    PRICE_RE = _NEW_PRICE_RE
    parse_price = _new_parse_price
    ad_id = _new_ad_id
    extract_cards_from_html = _new_extract
    curl_fetch = _new_curl_fetch
    playwright_fetch = _new_pw_fetch
    playwright_login = _new_pw_login
    load_cookies_dict = _new_load_cookies
    # expõe tipos para testes/compat
    Fetcher = _NewFetcher  # type: ignore[assignment]
    FakeFetcher = _NewFakeFetcher  # type: ignore[assignment]
    CompositeFetcher = _NewComposite  # type: ignore[assignment]
    _scrape_single_product = _new_single
    scrape = _new_scrape
except Exception as _e_scrape:  # noqa: F841
    pass

if __name__ == "__main__":
    main()
