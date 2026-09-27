"""
Testes para exclude_keywords — item 1 do backlog (PLANO-1-EXCLUDE-KEYWORDS.md).

Cobre:
- Normalização em _normalize_product: default [], str única -> lista, trim,
  dedupe case-insensitive (preserva 1ª), limites 50 termos x 40 chars, tipos inválidos
- Filtro híbrido (?<![a-z]) + literal escapado, case-insensitive:
  "GB" pega "500GB" mas não "RGB"; início de título casa
- Pipeline: include -> junk -> exclude -> preço -> dedupe -> matched; excluído não entra em
  results, não vira matched e não polui global_seen (reaparece se termo removido)
- Retrocompatibilidade: produto sem o campo scrapeia igual
- API: POST/PUT /api/products valida (>50 termos / >40 chars -> 400) e persiste;
  update parcial sem o campo preserva o existente
- Termos com chars especiais (c++, a(b)) funcionam como literal

Rede simulada via monkeypatch de olx_scraper.curl_fetch (padrão de test_e2e.py);
estado isolado em tmpdir (padrão de test_notify_dedupe.py).
"""

import pytest


def _card_html(ad_id, title, price):
    """Card no formato que extract_cards_from_html parseia (âncora + preço no pai)."""
    return (f'<div><a data-testid="adcard-link" '
            f'href="https://www.olx.com.br/item/an-{ad_id}.html">{title}</a> '
            f'R$ {price}</div>')


@pytest.fixture()
def isolated_olx(tmp_path, monkeypatch):
    """Stack isolado em tmpdir com a OLX simulada por catálogo mutável."""
    import olx_scraper
    import app as app_module

    state = tmp_path / ".olx"
    state.mkdir()
    monkeypatch.setattr(olx_scraper, "STATE_DIR", str(state))
    monkeypatch.setattr(olx_scraper, "CONFIG_FILE", str(state / "config.json"))
    monkeypatch.setattr(olx_scraper, "SCHEDULES_FILE", str(state / "schedules.json"))
    monkeypatch.setattr(olx_scraper, "COOKIE_JAR", str(state / "cookies.json"))
    monkeypatch.setattr(olx_scraper, "HISTORY_FILE", str(state / "deals.json"))
    monkeypatch.setattr(olx_scraper, "NOTIFIED_FILE", str(state / "notified.json"))
    monkeypatch.setattr(olx_scraper, "NOTIFIED_LOCK_FILE", str(state / ".notified.lock"))

    catalog = {}  # ad_id -> (title, price)

    def fake_curl_fetch(url, cookies):
        cards = "".join(_card_html(aid, t, p) for aid, (t, p) in sorted(catalog.items()))
        return 200, f"<html><body>{cards}</body></html>"

    monkeypatch.setattr(olx_scraper, "curl_fetch", fake_curl_fetch)
    # elimina o sleep anti-ban entre fetches
    monkeypatch.setattr(olx_scraper.random, "uniform", lambda a, b: 0.001)

    yield {
        "state": state,
        "app": app_module,
        "scraper": olx_scraper,
        "catalog": catalog,
    }


@pytest.fixture()
def client(isolated_olx):
    app = isolated_olx["app"].app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _product(scraper, **overrides):
    p = {
        "id": "hd-test",
        "name": "HD Test",
        "queries": ["hd 8tb"],
        "pages": 1,
        "min_price": None,
        "max_price": None,
        "junk_pattern": "",
        "enabled": True,
    }
    p.update(overrides)
    return p


def _scrape(iso, products):
    logs = []
    res = iso["scraper"].scrape(products, log_fn=logs.append, progress_cb=None)
    return res, "\n".join(logs)


def _seed(catalog, *items):
    catalog.clear()
    for aid, title, price in items:
        catalog[aid] = (title, price)


# ---------------------------------------------------------------------------
# 1) Normalização
# ---------------------------------------------------------------------------

class TestNormalizacao:
    def test_default_lista_vazia(self, isolated_olx):
        s = isolated_olx["scraper"]
        p = s._normalize_product({"name": "X"})
        assert p["exclude_keywords"] == []

    def test_str_unica_vira_lista(self, isolated_olx):
        s = isolated_olx["scraper"]
        p = s._normalize_product({"name": "X", "exclude_keywords": "GB"})
        assert p["exclude_keywords"] == ["GB"]

    def test_dedupe_case_insensitive_preserva_primeira(self, isolated_olx):
        s = isolated_olx["scraper"]
        p = s._normalize_product({"name": "X", "exclude_keywords": ["GB", "gb", "Gb", "dock"]})
        assert p["exclude_keywords"] == ["GB", "dock"]

    def test_trim_e_descarta_vazios(self, isolated_olx):
        s = isolated_olx["scraper"]
        p = s._normalize_product({"name": "X", "exclude_keywords": ["  GB  ", "", "   ", "dock"]})
        assert p["exclude_keywords"] == ["GB", "dock"]

    def test_maximo_50_termos(self, isolated_olx):
        s = isolated_olx["scraper"]
        kws = [f"termo{i:02d}" for i in range(60)]
        p = s._normalize_product({"name": "X", "exclude_keywords": kws})
        assert len(p["exclude_keywords"]) == 50

    def test_cada_termo_max_40_chars(self, isolated_olx):
        s = isolated_olx["scraper"]
        p = s._normalize_product({"name": "X", "exclude_keywords": ["x" * 100]})
        assert p["exclude_keywords"] == ["x" * 40]

    def test_tipos_invalidos_viram_lista_vazia(self, isolated_olx):
        s = isolated_olx["scraper"]
        for bad in (123, 4.5, {"a": 1}, None, True):
            assert s._normalize_product({"name": "X", "exclude_keywords": bad})["exclude_keywords"] == []

    def test_elementos_nao_string_sao_coagidos(self, isolated_olx):
        s = isolated_olx["scraper"]
        p = s._normalize_product({"name": "X", "exclude_keywords": [123, "GB"]})
        assert p["exclude_keywords"] == ["123", "GB"]

    def test_load_config_produto_sem_campo_recebe_vazio(self, isolated_olx):
        s = isolated_olx["scraper"]
        s.save_config({"products": [{"id": "p1", "name": "P1", "queries": ["x"]}]})
        cfg = s.load_config()
        assert cfg["products"][0]["exclude_keywords"] == []


# ---------------------------------------------------------------------------
# 2) Filtro híbrido + 3) case-insensitive (via scrape com OLX simulada)
# ---------------------------------------------------------------------------

class TestFiltroHibrido:
    def test_gb_exclui_hd_externo_500gb(self, isolated_olx):
        _seed(isolated_olx["catalog"], ("1", "HD Externo 500GB", 120), ("2", "HD 8TB Seagate", 700))
        prod = _product(isolated_olx["scraper"], exclude_keywords=["GB"])
        res, logs = _scrape(isolated_olx, [prod])
        matched = [r for r in res if r.get("matched")]
        assert [r["title"] for r in matched] == ["HD 8TB Seagate"]
        # intenção-irrelevante não persiste em res/history (apenas intenção-relevante, menos price)
        assert all(r["title"] != "HD Externo 500GB" for r in res)
        assert "[exclude]" in logs
        assert "termo 'GB'" in logs
        assert "descartados por exclude_keywords" in logs

    def test_rgb_nao_e_excluido_por_gb(self, isolated_olx):
        _seed(isolated_olx["catalog"], ("3", "Teclado RGB Gamer", 150))
        prod = _product(isolated_olx["scraper"], exclude_keywords=["GB"])
        res, _ = _scrape(isolated_olx, [prod])
        assert [r["title"] for r in res] == ["Teclado RGB Gamer"]
        assert r"[exclude]" not in _

    def test_inicio_de_titulo_casa(self, isolated_olx):
        _seed(isolated_olx["catalog"], ("4", "Gabinete Gamer Full Tower", 300))
        prod = _product(isolated_olx["scraper"], exclude_keywords=["gab"])
        res, _ = _scrape(isolated_olx, [prod])
        matched = [r for r in res if r.get("matched")]
        assert matched == []
        assert res == []  # intenção-irrelevante não persiste


class TestCaseInsensitive:
    def test_termo_minusculo_casa_titulo_maiusculo(self, isolated_olx):
        _seed(isolated_olx["catalog"], ("5", "SSD 240GB Novo", 90))
        prod = _product(isolated_olx["scraper"], exclude_keywords=["gb"])
        res, _ = _scrape(isolated_olx, [prod])
        matched = [r for r in res if r.get("matched")]
        assert matched == []
        assert res == []

    def test_termo_maiusculo_casa_titulo_minusculo(self, isolated_olx):
        _seed(isolated_olx["catalog"], ("6", "suporte dock tablet", 40))
        prod = _product(isolated_olx["scraper"], exclude_keywords=["DOCK"])
        res, _ = _scrape(isolated_olx, [prod])
        matched = [r for r in res if r.get("matched")]
        assert matched == []
        assert res == []

    def test_varios_termos_primeiro_hit_vence_no_log(self, isolated_olx):
        _seed(isolated_olx["catalog"], ("7", "Case com Suporte HD", 50))
        prod = _product(isolated_olx["scraper"], exclude_keywords=["zzz", "case"])
        _, logs = _scrape(isolated_olx, [prod])
        assert "termo 'case'" in logs


# ---------------------------------------------------------------------------
# 4) Pipeline/order: excluído não entra em results/matched/global_seen
# ---------------------------------------------------------------------------

class TestPipeline:
    def test_excluido_nao_polui_global_seen_e_volta_depois(self, isolated_olx):
        s = isolated_olx["scraper"]
        _seed(isolated_olx["catalog"], ("8", "HD Externo 500GB", 120), ("9", "HD 8TB WD", 700))
        gseen = set()
        logs = []

        # 1ª passada: termo GB ativo -> só o WD passa como matched, 500GB é descartado e não persiste (histórico só intenção-relevante)
        prod_ex = _product(s, exclude_keywords=["GB"])
        res1 = s._scrape_single_product(prod_ex, [], None, None, [False], logs.append, None, gseen)
        matched1 = [r for r in res1 if r.get("matched")]
        assert [r["title"] for r in matched1] == ["HD 8TB WD"]
        assert all(r.get("matched") is True for r in matched1)
        assert res1 == matched1  # excluído não persiste
        assert gseen == {"hd-test:9"}

        # termo removido -> anúncio reaparece (não foi marcado como visto)
        prod_ok = _product(s)
        res2 = s._scrape_single_product(prod_ok, [], None, None, [False], logs.append, None, gseen)
        matched2 = [r for r in res2 if r.get("matched")]
        assert [r["title"] for r in matched2] == ["HD Externo 500GB"]
        assert gseen == {"hd-test:9", "hd-test:8"}

    def test_excluido_nao_entra_em_results_nem_history(self, isolated_olx):
        s = isolated_olx["scraper"]
        _seed(isolated_olx["catalog"], ("10", "HD Externo 500GB", 120), ("11", "HD 8TB WD", 700))
        prod = _product(s, exclude_keywords=["GB"])
        res, _ = _scrape(isolated_olx, [prod])
        new = s.save_history(res)
        import json
        with open(s.HISTORY_FILE) as f:
            hist = json.load(f)
        # após ajuste histórico = só intenção-relevante (menos price); excluído não persiste
        matched = [r for r in res if r.get("matched")]
        assert {r["id"] for r in matched} == {"11"}
        assert {r["id"] for r in res} == {"11"}
        # save_history persiste só intenção-relevante (price pode vir como discard_reason)
        assert {d["id"] for d in hist} == {"11"}
        assert {d["id"] for d in new} == {"11"}

    def test_exclude_vem_depois_de_preco_e_junk(self, isolated_olx):
        s = isolated_olx["scraper"]
        # após reordem intenção->price e histórico só intenção-relevante, card com termo não persiste (exclude antes de price)
        _seed(isolated_olx["catalog"], ("12", "Dock Station Caro", 5000))
        prod = _product(s, exclude_keywords=["dock"], max_price=900.0)
        res, logs = _scrape(isolated_olx, [prod])
        matched = [r for r in res if r.get("matched")]
        assert matched == []
        assert res == []  # intenção-irrelevante não persiste
        assert "[exclude]" in logs

    def test_sem_exclude_nada_descartado(self, isolated_olx):
        _seed(isolated_olx["catalog"], ("13", "HD 500GB", 60))
        res, logs = _scrape(isolated_olx, [_product(isolated_olx["scraper"])])
        assert len(res) == 1
        assert "[exclude]" not in logs


# ---------------------------------------------------------------------------
# 5) Retrocompatibilidade
# ---------------------------------------------------------------------------

class TestRetrocompatibilidade:
    def test_produto_sem_campo_scrapeia_igual(self, isolated_olx):
        _seed(isolated_olx["catalog"], ("14", "HD 8TB WD", 700), ("15", "HD Externo 500GB", 120))
        res, logs = _scrape(isolated_olx, [_product(isolated_olx["scraper"])])
        assert {r["title"] for r in res} == {"HD 8TB WD", "HD Externo 500GB"}
        assert "[exclude]" not in logs

    def test_produto_com_campo_none_tratado_como_vazio(self, isolated_olx):
        _seed(isolated_olx["catalog"], ("16", "HD Externo 500GB", 120))
        res, _ = _scrape(isolated_olx, [_product(isolated_olx["scraper"], exclude_keywords=None)])
        assert len(res) == 1


# ---------------------------------------------------------------------------
# 6) API: validação e persistência
# ---------------------------------------------------------------------------

class TestApi:
    def test_create_persiste_exclude_keywords(self, client, isolated_olx):
        r = client.post("/api/products", json={
            "name": "P", "id": "p-excl", "queries": ["hd"],
            "exclude_keywords": ["GB", "dock"],
        })
        assert r.status_code == 201, r.get_json()
        j = r.get_json()
        assert j["product"]["exclude_keywords"] == ["GB", "dock"]
        r2 = client.get("/api/products/p-excl")
        assert r2.get_json()["product"]["exclude_keywords"] == ["GB", "dock"]
        # GET /api/config também devolve o campo
        cfg = client.get("/api/config").get_json()
        prod = next(p for p in cfg["products"] if p["id"] == "p-excl")
        assert prod["exclude_keywords"] == ["GB", "dock"]

    def test_create_aceita_str_unica(self, client, isolated_olx):
        r = client.post("/api/products", json={
            "name": "P", "id": "p-str", "queries": ["hd"], "exclude_keywords": "GB",
        })
        assert r.status_code == 201
        assert r.get_json()["product"]["exclude_keywords"] == ["GB"]

    def test_create_lista_vazia_valida(self, client, isolated_olx):
        r = client.post("/api/products", json={
            "name": "P", "id": "p-vazio", "queries": ["hd"], "exclude_keywords": [],
        })
        assert r.status_code == 201
        assert r.get_json()["product"]["exclude_keywords"] == []

    def test_mais_de_50_termos_retorna_400(self, client, isolated_olx):
        r = client.post("/api/products", json={
            "name": "P", "queries": ["hd"],
            "exclude_keywords": [f"t{i}" for i in range(51)],
        })
        assert r.status_code == 400
        assert any("máximo 50" in e for e in r.get_json()["errors"])

    def test_termo_maior_que_40_chars_retorna_400(self, client, isolated_olx):
        r = client.post("/api/products", json={
            "name": "P", "queries": ["hd"], "exclude_keywords": ["x" * 41],
        })
        assert r.status_code == 400
        assert any("max 40 chars" in e for e in r.get_json()["errors"])

    def test_tipo_invalido_retorna_400(self, client, isolated_olx):
        r = client.post("/api/products", json={
            "name": "P", "queries": ["hd"], "exclude_keywords": 123,
        })
        assert r.status_code == 400
        r = client.post("/api/products", json={
            "name": "P", "queries": ["hd"], "exclude_keywords": [""],
        })
        assert r.status_code == 400

    def test_put_update_parcial_preserva_exclude(self, client, isolated_olx):
        client.post("/api/products", json={
            "name": "P", "id": "p-parcial", "queries": ["hd"],
            "exclude_keywords": ["GB"],
        })
        # update parcial sem o campo -> não apaga o existente
        r = client.put("/api/products/p-parcial", json={"pages": 3})
        assert r.status_code == 200
        assert r.get_json()["product"]["exclude_keywords"] == ["GB"]
        assert r.get_json()["product"]["pages"] == 3

    def test_put_com_lista_zera_exclude(self, client, isolated_olx):
        client.post("/api/products", json={
            "name": "P", "id": "p-zero", "queries": ["hd"], "exclude_keywords": ["GB"],
        })
        r = client.put("/api/products/p-zero", json={"exclude_keywords": []})
        assert r.status_code == 200
        assert r.get_json()["product"]["exclude_keywords"] == []

    def test_put_atualiza_valores(self, client, isolated_olx):
        client.post("/api/products", json={
            "name": "P", "id": "p-upd", "queries": ["hd"], "exclude_keywords": ["GB"],
        })
        r = client.put("/api/products/p-upd", json={"exclude_keywords": ["dock", "case"]})
        assert r.status_code == 200
        assert r.get_json()["product"]["exclude_keywords"] == ["dock", "case"]

    def test_put_sanitiza_trim(self, client, isolated_olx):
        client.post("/api/products", json={
            "name": "P", "id": "p-trim", "queries": ["hd"], "exclude_keywords": [],
        })
        r = client.put("/api/products/p-trim", json={"exclude_keywords": ["  GB  ", "gb"]})
        assert r.status_code == 200
        assert r.get_json()["product"]["exclude_keywords"] == ["GB"]


# ---------------------------------------------------------------------------
# 7) Termos com caracteres especiais são literais
# ---------------------------------------------------------------------------

class TestCaracteresEspeciais:
    def test_compile_escapa_literal(self, isolated_olx):
        s = isolated_olx["scraper"]
        res = s._compile_exclude_res(["c++", "a(b)", "(?!x)x"])
        assert len(res) == 3
        assert res[0].search("Hub USB C++ 5 portas") is not None
        assert res[1].search("mesa para a(b) teste") is not None
        assert res[2].search("literal (?!x)x no título") is not None
        # não casa onde o padrão regex real casaria (é literal!)
        assert res[2].search("qualquer texto") is None
        assert res[0].search("USB C plus") is None

    def test_scrape_com_termos_especiais(self, isolated_olx):
        _seed(isolated_olx["catalog"],
              ("17", "Hub USB C++ 5 portas", 40),
              ("18", "Suporte a(b) genérico", 30),
              ("19", "Hub USB simples", 25))
        prod = _product(isolated_olx["scraper"], exclude_keywords=["c++", "a(b)"])
        res, _ = _scrape(isolated_olx, [prod])
        matched = [r for r in res if r.get("matched")]
        assert {r["title"] for r in matched} == {"Hub USB simples"}
        assert res == matched  # intenção-irrelevante não persiste, res == matched
