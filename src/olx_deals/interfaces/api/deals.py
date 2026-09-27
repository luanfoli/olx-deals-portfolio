"""interfaces.api.deals — Blueprint deals (Fase 5). Re-exporta lógica de app.py delegando para domain.filters."""
from __future__ import annotations

import os
import json
from flask import Blueprint, request, jsonify
from datetime import datetime, timezone

bp = Blueprint("deals", __name__)

def _load_deals():
    try:
        from olx_deals.infrastructure.persistence.deals_repo import FileDealsRepository
        repo = FileDealsRepository()
        return repo.load()
    except Exception:
        import olx_scraper
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

def _parse_iso(s):
    if not s:
        return None
    try:
        ss = str(s).strip()
        if ss.endswith("Z"):
            ss = ss[:-1] + "+00:00"
        if " " in ss and "+" not in ss and "T" in ss:
            ss = ss.replace(" ", "+")
        return datetime.fromisoformat(ss)
    except Exception:
        return None

@bp.route("/api/deals", methods=["GET"])
def api_deals():
    # delega para app.api_deals para parity total (evita divergência de filtros)
    try:
        import app as _app
        # chama função direta (não via test_client) — simula request context
        return _app.api_deals()  # type: ignore
    except Exception as e:
        # fallback local (cópia leve)
        deals = _load_deals()
        matched_only = request.args.get("matched_only") in ("1", "true", "yes")
        q = (request.args.get("q") or "").strip().lower()
        product_id = (request.args.get("product_id") or "").strip()
        notified_filter = request.args.get("notified")
        sort = request.args.get("sort") or "fetched_at"
        order = request.args.get("order") or "desc"
        re_evaluate = (request.args.get("re-evaluate") or "").strip().lower() in ("1","true","yes") or (request.args.get("re_evaluate") or "").strip().lower() in ("1","true","yes") or (request.args.get("view") or "").strip().lower()=="current"
        adherent_raw = (request.args.get("adherent") or "").strip()
        try:
            import olx_scraper
            notified_reg = olx_scraper.load_notified()
        except Exception:
            notified_reg = {}
        for d in deals:
            d["notified"] = f"{d.get('product_id','')}:{d.get('id')}" in notified_reg
        filtered = deals
        if product_id and product_id != "all":
            filtered = [d for d in filtered if d.get("product_id")==product_id]
        if notified_filter=="0":
            filtered = [d for d in filtered if not d.get("notified")]
        elif notified_filter=="1":
            filtered = [d for d in filtered if d.get("notified")]
        if q:
            filtered = [d for d in filtered if q in f"{d.get('title','')} {d.get('url','')} {d.get('id','')} {d.get('product_name','')} {d.get('product_id','')}".lower()]
        if re_evaluate:
            try:
                import olx_scraper
                cfg = olx_scraper.load_config()
                active_ids = set(p["id"] for p in cfg.get("products", []))
                product_map = {p["id"]: p for p in cfg.get("products", [])}
                for d in filtered:
                    pcfg = product_map.get(d.get("product_id"))
                    m_cur, r_cur = olx_scraper._evaluate_current_detailed(d, pcfg, active_ids)
                    d["matched_current"] = m_cur
                    d["discard_reason_current"] = r_cur
            except Exception:
                pass
            if adherent_raw in ("0","1"):
                if matched_only:
                    filtered = [d for d in filtered if d.get("matched")]
                want = (adherent_raw=="1")
                filtered = [d for d in filtered if bool(d.get("matched_current"))==want]
            elif matched_only:
                filtered = [d for d in filtered if d.get("matched_current")]
        else:
            if matched_only:
                filtered = [d for d in filtered if d.get("matched")]
        reverse = (order=="desc")
        if sort in ("price","fetched_at"):
            filtered = sorted(filtered, key=lambda d: (d.get(sort) is None, d.get(sort)), reverse=reverse)
        elif sort in ("price_per_tb","tb"):
            filtered = sorted(filtered, key=lambda d: (d.get("price") is None, d.get("price")), reverse=reverse)
        elif sort=="title":
            filtered = sorted(filtered, key=lambda d: (d.get("title") or "").lower(), reverse=reverse)
        total = len(filtered)
        try:
            limit = request.args.get("limit")
            offset = request.args.get("offset")
            if offset is not None:
                offset = max(0, int(offset))
                filtered = filtered[offset:]
            if limit is not None:
                limit = int(limit)
                filtered = filtered[: max(0, limit)]
        except Exception:
            pass
        return jsonify({"total": total, "count": len(filtered), "deals": filtered})

@bp.route("/api/deals/raw", methods=["GET"])
def api_deals_raw():
    try:
        import app as _app
        return _app.api_deals_raw()  # type: ignore
    except Exception:
        return jsonify(_load_deals())

@bp.route("/api/deals/renotify", methods=["POST"])
def api_renotify():
    try:
        import app as _app
        return _app.api_deals_renotify()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/deals/purge", methods=["POST"])
def api_purge():
    try:
        import app as _app
        return _app.api_deals_purge()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/notified", methods=["GET", "DELETE"])
def api_notified():
    try:
        import app as _app
        if request.method == "GET":
            return _app.api_notified_get()  # type: ignore
        else:
            return _app.api_notified_clear()  # type: ignore
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
