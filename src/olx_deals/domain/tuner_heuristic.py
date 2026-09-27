"""domain.tuner_heuristic — puro, sem Flask/os/json/playwright. Extraído de olx_scraper.py:707,744,784"""
from __future__ import annotations

import re


def _dedupe_obrig_terms(terms: list) -> list:
    """Remove termos genéricos cujo conjunto de tokens é subconjunto de outro termo mais específico.

    Genérico: ["forza horizon 2","forza 2"] -> ["forza horizon 2"] para que heurística
    e include_pattern não validem semanticamente "motorsport 2" via alternativa genérica.
    Mantém OR correto para termos disjuntos como ["starfox","switch"].
    """
    if not terms:
        return terms
    norm = []
    for t in terms:
        tl = str(t).strip().lower()
        if not tl:
            continue
        toks = set(tok for tok in re.split(r"\s+", tl) if tok)
        norm.append((tl, toks))
    norm.sort(key=lambda x: (len(x[1]), len(x[0])), reverse=True)
    kept = []
    kept_sets = []
    for tl, toks in norm:
        if any(toks <= ks for ks in kept_sets):
            continue
        if tl in kept:
            continue
        kept.append(tl)
        kept_sets.append(toks)
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
    parts = _dedupe_obrig_terms(parts)
    escaped_parts = []
    for p in parts:
        tokens = [t for t in re.split(r"\s+", p.strip()) if t]
        if not tokens:
            continue
        if len(tokens) == 1:
            escaped_parts.append(r"\b" + re.escape(tokens[0]) + r"\b")
        else:
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
    if isinstance(obrig, str):
        obrig_list = [p.strip().lower() for p in re.split(r"[,;]+", obrig) if p.strip()]
    elif isinstance(obrig, list):
        obrig_list = [str(p).strip().lower() for p in obrig if str(p).strip()]
    else:
        obrig_list = []
    obrig_list = _dedupe_obrig_terms(obrig_list)
    if isinstance(excl, str):
        excl_list = [p.strip().lower() for p in re.split(r"[,;]+", excl) if p.strip()]
    elif isinstance(excl, list):
        excl_list = [str(p).strip().lower() for p in excl if str(p).strip()]
    else:
        excl_list = []

    def _term_in_title(term: str, title_low: str) -> bool:
        if not term:
            return False
        if " " in term:
            toks = [t for t in term.split() if t]
            return all(tok in title_low for tok in toks)
        return term in title_low

    for term in excl_list:
        if term and _term_in_title(term, title):
            return False, f"contém excludente '{term}'", f"adicionar '{term}' em exclude_keywords"
    if obrig_list:
        hit = any(_term_in_title(term, title) for term in obrig_list if term)
        if not hit:
            return False, f"falta obrigatório {obrig_list}", f"ajustar include_pattern para {obrig_list}"
    else:
        definicao = str(briefing.get("definicao") or briefing.get("definicao_uma_frase") or briefing.get("descricao") or "").lower()
        words = [w for w in re.split(r"\W+", definicao) if len(w) > 3]
        if words and not any(w in title for w in words[:3]):
            sementes = briefing.get("sementes") or briefing.get("termos_busca") or []
            if isinstance(sementes, str):
                sementes = [p.strip().lower() for p in re.split(r"[,;]+", sementes) if p.strip()]
            sementes = [str(s).lower() for s in (sementes or []) if str(s).strip()]
            if sementes and not any(s in title for s in sementes):
                return False, "não contém termo da definição/semente", "revisar queries ou include_pattern"
    return True, "relevante", ""
