"""interfaces.api.validation — hardening: validação de produtos e tuner (fonte única).

Extraído de app.py:576-785 (_validate_product_payload) e 1664-1863 (_validate_tuner_payload etc.)
para ser fonte única DRY. App re-exporta para compat com testes legados (test_schedule).
Mantém mensagens pt-BR idênticas ao app original para não quebrar tests que fazem substring check.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

# --- product payload ---

def _validate_product_payload(data, is_update=False):
    errors = []
    if "name" in data:
        n = str(data["name"]).strip()
        if not n:
            errors.append("name não pode ser vazio")
        elif len(n) > 80:
            errors.append("name max 80 chars")
    elif not is_update:
        errors.append("name obrigatório")

    if "queries" in data:
        q = data["queries"]
        if isinstance(q, str):
            q = [q]
            data["queries"] = q
        if not isinstance(q, list) or not q or not all(isinstance(x, str) and x.strip() for x in q):
            errors.append("queries deve ser lista não vazia de strings")
        else:
            cleaned = [s.strip() for s in q if s.strip()]
            if len(cleaned) > 50:
                errors.append("queries: máximo 50 termos")
            if any(len(s) > 80 for s in cleaned):
                errors.append("queries: cada termo max 80 chars")
            data["queries"] = cleaned

    if "pages" in data:
        try:
            p = int(data["pages"])
            if not (1 <= p <= 10):
                errors.append("pages deve ser 1-10")
            data["pages"] = p
        except Exception:
            errors.append("pages deve ser inteiro 1-10")

    if "search_url" in data:
        u = str(data["search_url"]).strip()
        if "{query}" not in u or "{page}" not in u:
            errors.append("search_url deve conter {query} e {page}")
        elif not u.startswith("https://"):
            errors.append("search_url deve começar com https://")
        else:
            data["search_url"] = u

    if "junk_pattern" in data:
        pat = str(data["junk_pattern"])
        if len(pat) > 2000:
            errors.append("junk_pattern muito longo")
        else:
            if pat:
                try:
                    re.compile(pat, re.I)
                except re.error as e:
                    errors.append(f"junk_pattern regex inválido: {e}")

    if "exclude_keywords" in data:
        ek = data["exclude_keywords"]
        if ek is None:
            data["exclude_keywords"] = []
        else:
            if isinstance(ek, str):
                ek = [ek]
                data["exclude_keywords"] = ek
            if not isinstance(ek, list) or not all(isinstance(x, str) and x.strip() for x in ek):
                errors.append("exclude_keywords deve ser lista de strings não vazias")
            else:
                cleaned = [s.strip() for s in ek if s.strip()]
                if len(cleaned) > 50:
                    errors.append("exclude_keywords: máximo 50 termos")
                if any(len(s) > 40 for s in cleaned):
                    errors.append("exclude_keywords: cada termo max 40 chars")
                data["exclude_keywords"] = cleaned

    for k in ("min_price", "max_price"):
        if k in data and data[k] not in (None, ""):
            try:
                v = float(data[k])
                if not (0 < v <= 1000000):
                    errors.append(f"{k} fora de faixa 0-1000000")
                else:
                    data[k] = v
            except Exception:
                errors.append(f"{k} deve ser número")
        elif k in data and data[k] == "":
            data[k] = None

    if "min_price" in data and "max_price" in data:
        if data["min_price"] is not None and data["max_price"] is not None:
            if data["min_price"] >= data["max_price"]:
                errors.append("min_price deve ser < max_price")
    if "enabled" in data:
        data["enabled"] = bool(data["enabled"])
    if "draft" in data:
        data["draft"] = bool(data["draft"])
    if "playwright_only" in data:
        data["playwright_only"] = bool(data["playwright_only"])

    if "id" in data:
        pid = str(data["id"]).strip()
        if not pid:
            errors.append("id não pode ser vazio")
        elif len(pid) > 60:
            errors.append("id max 60 chars")
        elif not re.match(r"^[a-z0-9\-]+$", pid.lower()):
            errors.append("id deve conter só a-z0-9 e hífen")

    if "schedule" in data:
        s = data["schedule"]
        if not isinstance(s, dict):
            errors.append("schedule deve ser objeto")
        else:
            if "enabled" in s:
                if not isinstance(s["enabled"], bool):
                    if s["enabled"] in (0, 1):
                        s["enabled"] = bool(s["enabled"])
                    else:
                        errors.append("schedule.enabled deve ser boolean")
            if "mode" in s:
                if s["mode"] not in ("interval", "cron"):
                    errors.append("schedule.mode deve ser 'interval' ou 'cron'")
            mode = s.get("mode", "interval")
            if "jitter_minutes" in s:
                try:
                    jv = int(s["jitter_minutes"])
                    if not (0 <= jv <= 60):
                        errors.append("schedule.jitter_minutes deve ser 0–60")
                    else:
                        s["jitter_minutes"] = jv
                except Exception:
                    errors.append("schedule.jitter_minutes deve ser inteiro 0–60")
            if "timezone" in s:
                tz = str(s["timezone"]).strip()
                if tz and tz != "America/Sao_Paulo":
                    errors.append("schedule.timezone deve ser America/Sao_Paulo (fixo)")
            if mode == "interval":
                if "interval_minutes" in s:
                    try:
                        iv = int(s["interval_minutes"])
                        if not (15 <= iv <= 10080):
                            errors.append("schedule.interval_minutes deve ser 15–10080 (15min a 7 dias)")
                        else:
                            s["interval_minutes"] = iv
                    except Exception:
                        errors.append("schedule.interval_minutes deve ser inteiro 15–10080")
                if "cron" in s and s["cron"] is not None and str(s["cron"]).strip():
                    cron = str(s["cron"]).strip()
                    if len(cron.split()) != 5:
                        errors.append("schedule.cron deve ter 5 campos (ex: '0 */6 * * *')")
                    else:
                        try:
                            from apscheduler.triggers.cron import CronTrigger
                            CronTrigger.from_crontab(cron, timezone="America/Sao_Paulo")
                        except ImportError:
                            try:
                                from croniter import croniter  # type: ignore
                                croniter(cron)
                            except ImportError:
                                pass
                            except Exception as e:
                                errors.append(f"schedule.cron inválido: {e}")
                        except Exception as e:
                            errors.append(f"schedule.cron inválido: {e}")
            else:
                if "cron" in s:
                    cron = str(s["cron"]).strip()
                    if len(cron.split()) != 5:
                        errors.append("schedule.cron deve ter 5 campos")
                    else:
                        try:
                            from apscheduler.triggers.cron import CronTrigger
                            CronTrigger.from_crontab(cron, timezone="America/Sao_Paulo")
                        except ImportError:
                            try:
                                from croniter import croniter  # type: ignore
                                croniter(cron)
                            except ImportError:
                                pass
                            except Exception as e:
                                errors.append(f"schedule.cron inválido: {e}")
                        except Exception as e:
                            errors.append(f"schedule.cron inválido: {e}")
                elif s.get("enabled"):
                    errors.append("schedule.cron obrigatório quando mode=cron")
                if "interval_minutes" in s:
                    try:
                        iv = int(s["interval_minutes"])
                        if not (15 <= iv <= 10080):
                            errors.append("schedule.interval_minutes deve ser 15–10080")
                        else:
                            s["interval_minutes"] = iv
                    except Exception:
                        errors.append("schedule.interval_minutes deve ser inteiro")

    return errors


def _validate_tuner_payload(data):
    errors = []
    if not isinstance(data, dict):
        return ["payload deve ser objeto"]
    desc = str(data.get("descricao") or data.get("description") or "").strip()
    if not desc:
        errors.append("descricao obrigatória")
    elif len(desc) < 1 or len(desc) > 200:
        errors.append("descricao deve ter 1..200 chars")
    definicao = data.get("definicao") or data.get("definicao_uma_frase") or data.get("definition")
    if definicao is not None and str(definicao).strip() != "":
        d = str(definicao).strip()
        if len(d) < 1 or len(d) > 200:
            errors.append("definicao deve ter 1..200 chars")
    for key in ("obrigatorios", "termos_obrigatorios", "must_terms"):
        if key in data and data[key] not in (None, ""):
            v = data[key]
            terms = []
            if isinstance(v, str):
                terms = [p.strip() for p in re.split(r"[,;]+", v) if p.strip()]
            elif isinstance(v, list):
                if not all(isinstance(x, str) for x in v):
                    errors.append(f"{key} deve ser lista de strings")
                else:
                    terms = [str(x).strip() for x in v if str(x).strip()]
            else:
                errors.append(f"{key} deve ser string ou lista")
            if any(len(t) < 1 or len(t) > 40 for t in terms):
                errors.append(f"{key}: cada termo deve ter 1..40 chars")
            if len(terms) > 10:
                errors.append(f"{key}: máximo 10 termos")
    for key in ("excludentes", "termos_a_excluir", "exclude_terms", "termos_excluir"):
        if key in data and data[key] not in (None, ""):
            v = data[key]
            terms = []
            if isinstance(v, str):
                terms = [p.strip() for p in re.split(r"[,;]+", v) if p.strip()]
            elif isinstance(v, list):
                if not all(isinstance(x, str) for x in v):
                    errors.append(f"{key} deve ser lista de strings")
                else:
                    terms = [str(x).strip() for x in v if str(x).strip()]
            else:
                errors.append(f"{key} deve ser string ou lista")
            if any(len(t) < 1 or len(t) > 40 for t in terms):
                errors.append(f"{key}: cada termo deve ter 1..40 chars")
            if len(terms) > 10:
                errors.append(f"{key}: máximo 10 termos")
    faixa = data.get("faixa") if isinstance(data.get("faixa"), dict) else {}
    min_val = None
    max_val = None
    for k in ("preco_min", "min_price", "price_min"):
        if k in data and data[k] not in (None, ""):
            min_val = data[k]
            break
    if min_val is None and "min_price" in faixa and faixa["min_price"] not in (None, ""):
        min_val = faixa["min_price"]
    for k in ("preco_max", "max_price", "price_max"):
        if k in data and data[k] not in (None, ""):
            max_val = data[k]
            break
    if max_val is None and "max_price" in faixa and faixa["max_price"] not in (None, ""):
        max_val = faixa["max_price"]
    for label, v in [("min_price", min_val), ("max_price", max_val)]:
        if v is not None and v != "":
            try:
                fv = float(v)
                if not (0 < fv <= 1000000):
                    errors.append(f"{label} fora de faixa 0-1000000")
            except Exception:
                errors.append(f"{label} deve ser número")
    if min_val is not None and min_val != "" and max_val is not None and max_val != "":
        try:
            if float(min_val) >= float(max_val):
                errors.append("min_price deve ser < max_price")
        except Exception:
            pass
    for k in ("preco_min", "preco_max", "min_price", "max_price", "price_min", "price_max"):
        if k in data and data[k] not in (None, ""):
            try:
                fv = float(data[k])
                if not (0 < fv <= 1000000):
                    if f"{k} fora de faixa" not in ";".join(errors):
                        errors.append(f"{k} fora de faixa 0-1000000")
            except Exception:
                if f"{k} deve ser número" not in errors:
                    errors.append(f"{k} deve ser número")
    sementes = data.get("sementes") or data.get("termos_busca") or data.get("queries") or data.get("search_terms")
    if sementes is None or (isinstance(sementes, str) and not sementes.strip()) or (isinstance(sementes, list) and not [x for x in sementes if str(x).strip()]):
        errors.append("sementes (2-3 termos que digitaria na OLX) obrigatório")
    else:
        if isinstance(sementes, str):
            termos = [p.strip() for p in re.split(r"[,;\n]+", sementes) if p.strip()]
        elif isinstance(sementes, list):
            if not all(isinstance(x, str) and x.strip() for x in sementes):
                errors.append("sementes deve ser lista não vazia de strings")
                termos = []
            else:
                termos = [str(x).strip() for x in sementes if str(x).strip()]
        else:
            errors.append("sementes deve ser lista ou string")
            termos = []
        if termos:
            if len(termos) < 2:
                errors.append("sementes: informe ao menos 2 termos (2..3)")
            if len(termos) > 3:
                errors.append("sementes: máximo 3 termos")
            if any(len(t) < 2 or len(t) > 80 for t in termos):
                errors.append("sementes: cada termo deve ter 2..80 chars")
    return errors


def _tuner_normalize_briefing(data):
    desc = str(data.get("descricao") or data.get("description") or "").strip()
    definicao = str(data.get("definicao") or data.get("definicao_uma_frase") or data.get("definition") or "").strip() or desc
    obrig_raw = data.get("obrigatorios") or data.get("termos_obrigatorios") or data.get("must_terms") or ""
    exclud_raw = data.get("excludentes") or data.get("termos_a_excluir") or data.get("exclude_terms") or data.get("termos_excluir") or ""
    def _to_list(v):
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        if isinstance(v, str) and v.strip():
            return [p.strip() for p in re.split(r"[,;]+", v) if p.strip()]
        return []
    obrig_list = _to_list(obrig_raw)
    excl_list = _to_list(exclud_raw)
    obrig = ", ".join(obrig_list)
    excl = ", ".join(excl_list)
    preco_min = None
    preco_max = None
    faixa = data.get("faixa") if isinstance(data.get("faixa"), dict) else {}
    for k in ("preco_min", "min_price", "price_min"):
        if k in data and data[k] not in (None, ""):
            try:
                preco_min = float(data[k])
                break
            except Exception:
                pass
    if preco_min is None and isinstance(faixa, dict):
        for k in ("min_price", "min", "preco_min"):
            if k in faixa and faixa[k] not in (None, ""):
                try:
                    preco_min = float(faixa[k])
                    break
                except Exception:
                    pass
    for k in ("preco_max", "max_price", "price_max"):
        if k in data and data[k] not in (None, ""):
            try:
                preco_max = float(data[k])
                break
            except Exception:
                pass
    if preco_max is None and isinstance(faixa, dict):
        for k in ("max_price", "max", "preco_max"):
            if k in faixa and faixa[k] not in (None, ""):
                try:
                    preco_max = float(faixa[k])
                    break
                except Exception:
                    pass
    sementes = data.get("sementes") or data.get("termos_busca") or data.get("queries") or data.get("search_terms") or []
    if isinstance(sementes, str):
        sementes = [p.strip() for p in re.split(r"[,;\n]+", sementes) if p.strip()]
    elif isinstance(sementes, list):
        sementes = [str(x).strip() for x in sementes if str(x).strip()]
    else:
        sementes = []
    faixa_out = {}
    if preco_min is not None:
        faixa_out["min_price"] = preco_min
    if preco_max is not None:
        faixa_out["max_price"] = preco_max
    return {
        "descricao": desc,
        "definicao": definicao,
        "obrigatorios": obrig,
        "obrigatorios_list": obrig_list,
        "excludentes": excl,
        "excludentes_list": excl_list,
        "preco_min": preco_min,
        "preco_max": preco_max,
        "faixa": faixa_out if faixa_out else None,
        "sementes": sementes,
    }


def _tuner_create_draft_product(briefing, existing_ids):
    import olx_scraper
    desc = briefing["descricao"]
    base_slug = olx_scraper._slugify(desc)[:30] or "tuner"
    pid = f"{base_slug}-{uuid.uuid4().hex[:6]}"
    base = pid
    n = 2
    while pid in existing_ids:
        pid = f"{base}-{n}"
        n += 1
    name = (briefing["definicao"][:80] or desc[:80]).strip()
    sementes = briefing.get("sementes") or []
    obrig_list = briefing.get("obrigatorios_list") or []
    if not obrig_list and briefing.get("obrigatorios"):
        obrig_list = [p.strip() for p in re.split(r"[,;]+", briefing["obrigatorios"]) if p.strip()]
    combined = list(dict.fromkeys([str(s).strip() for s in sementes if str(s).strip()] + [str(o).strip() for o in obrig_list if str(o).strip()]))
    queries = combined[:5] if combined else [desc[:80]]
    queries = [q[:80] for q in queries if q]
    if not queries:
        queries = [desc[:80]]
    include_pat = olx_scraper._tuner_briefing_to_include_pattern(briefing.get("obrigatorios") or obrig_list)
    excl_list = []
    exclud = briefing.get("excludentes") or ""
    if isinstance(exclud, list):
        excl_list = [str(x).strip()[:40] for x in exclud if str(x).strip()][:10]
    elif exclud:
        excl_list = [p.strip()[:40] for p in re.split(r"[,;]+", str(exclud)) if p.strip()][:10]
    min_price = briefing.get("preco_min")
    max_price = briefing.get("preco_max")
    if min_price is None and briefing.get("faixa") and isinstance(briefing["faixa"], dict):
        min_price = briefing["faixa"].get("min_price")
    if max_price is None and briefing.get("faixa") and isinstance(briefing["faixa"], dict):
        max_price = briefing["faixa"].get("max_price")
    raw = {
        "id": pid,
        "name": name,
        "queries": queries,
        "pages": 2,
        "search_url": olx_scraper.SEARCH_URL,
        "playwright_only": False,
        "min_price": min_price,
        "max_price": max_price,
        "junk_pattern": olx_scraper.JUNK_RE.pattern,
        "include_pattern": include_pat,
        "exclude_keywords": excl_list,
        "enabled": False,
        "draft": True,
        "schedule": {"enabled": False, "mode": "interval", "interval_minutes": 360, "jitter_minutes": 0, "cron": "0 */6 * * *", "timezone": "America/Sao_Paulo"},
    }
    prod = olx_scraper._normalize_product(raw, existing_ids)
    prod["id"] = pid
    prod["draft"] = True
    prod["enabled"] = False
    prod["schedule"]["enabled"] = False
    return prod
