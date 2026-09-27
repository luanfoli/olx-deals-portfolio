"""
Testes da spec OpenAPI + Swagger UI (PLANO-OPENAPI-MCP Fase 2).

- openapi.yaml parseia (PyYAML) e passa em checagens meta básicas
  (openapi 3.x, info, paths, operationId únicos, responses, parâmetros).
- PARIDADE com app.url_map: toda regra /api/* registrada no Flask existe na
  spec com seus métodos — e vice-versa. Impede a doc apodrecer quando alguém
  adiciona/remove rota sem atualizar a spec.
- Payload de exemplo de produto valida contra #/components/schemas/Product
  usando um validador mínimo do subconjunto JSON Schema usado pela spec.

Sem dependência de jsonschema: validador próprio cobre type/nullable,
required, properties, additionalProperties, enum, min/max (inclusive
exclusive), minItems/maxItems, minLength/maxLength, pattern, format(uri),
items e oneOf (limitado a schemas triviais usados aqui).
"""

import re

import pytest
import yaml

from app import BASE_DIR

import app as app_module

SPEC_PATH = f"{BASE_DIR}/openapi.yaml"


@pytest.fixture(scope="module")
def spec():
    with open(SPEC_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Validação mínima de JSON Schema (subconjunto usado nesta spec)
# ---------------------------------------------------------------------------

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
}


def _check_type(value, schema, path, errors):
    types = schema["type"] if isinstance(schema.get("type"), list) else [schema.get("type")]
    py_types = tuple(t for t in (_TYPES[x] for x in types if x in _TYPES))
    if not py_types:
        return True
    # bool não é number/integer em JSON Schema
    if isinstance(value, bool) and "boolean" not in types:
        errors.append(f"{path}: esperado {types}, recebido boolean")
        return False
    ok = isinstance(value, py_types)
    if ok and "integer" in types and isinstance(value, bool):
        ok = False
    if ok and "number" in types and "integer" in types:
        ok = isinstance(value, int) or float(value).is_integer() or "number" in types
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    if not ok:
        errors.append(f"{path}: esperado {types}, recebido {type(value).__name__}")
    return ok


def _validate(value, schema, path, errors, root):
    if schema is True or schema == {}:
        return
    if schema is False:
        errors.append(f"{path}: schema proíbe qualquer valor")
        return
    if "$ref" in schema:
        ref = schema["$ref"]
        assert ref.startswith("#/")
        target = root
        for part in ref[2:].split("/"):
            target = target[part]
        _validate(value, target, f"{path} ({ref})", errors, root)
        return

    if "oneOf" in schema:
        matched = 0
        for sub in schema["oneOf"]:
            before = len(errors)
            _validate(value, sub, path, errors, root)
            if len(errors) == before:
                matched += 1
            else:
                del errors[before:]
        if matched != 1:
            errors.append(f"{path}: casa {matched} ramificações de oneOf (esperado 1)")
        for extra in ("type", "enum", "required", "properties"):
            if extra in schema:
                _validate(value, {k: v for k, v in schema.items() if k != "oneOf"},
                          path, errors, root)
        return

    if "enum" in schema and value not in schema["enum"]:
        # OpenAPI: null só válido se nullable
        if value is None and schema.get("nullable"):
            pass
        else:
            errors.append(f"{path}: '{value}' fora do enum {schema['enum']}")

    t = schema.get("type")
    if t is None:
        return
    if value is None:
        if schema.get("nullable") or "null" in (t if isinstance(t, list) else [t]):
            return
        errors.append(f"{path}: null não permitido")
        return
    if not _check_type(value, schema, path, errors):
        return

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: minLength={schema['minLength']}, len={len(value)}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: maxLength={schema['maxLength']}, len={len(value)}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: não casa pattern {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        lo, hi = schema.get("minimum"), schema.get("maximum")
        elo, ehi = schema.get("exclusiveMinimum"), schema.get("exclusiveMaximum")
        if lo is not None and value < lo:
            errors.append(f"{path}: {value} < minimum {lo}")
        if hi is not None and value > hi:
            errors.append(f"{path}: {value} > maximum {hi}")
        if elo is not None and value <= elo:
            errors.append(f"{path}: {value} <= exclusiveMinimum {elo}")
        if ehi is not None and value >= ehi:
            errors.append(f"{path}: {value} >= exclusiveMaximum {ehi}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: minItems={schema['minItems']}, len={len(value)}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: maxItems={schema['maxItems']}, len={len(value)}")
        items = schema.get("items")
        if items:
            for i, item in enumerate(value):
                _validate(item, items, f"{path}[{i}]", errors, root)

    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}: campo obrigatório ausente '{req}'")
        props = schema.get("properties", {})
        for key, val in value.items():
            if key in props:
                _validate(val, props[key], f"{path}.{key}", errors, root)
            elif schema.get("additionalProperties") is True or "additionalProperties" not in schema:
                continue
            elif isinstance(schema.get("additionalProperties"), dict):
                _validate(val, schema["additionalProperties"], f"{path}.{key}", errors, root)
            else:
                errors.append(f"{path}: campo extra '{key}' não permitido")


def validate_against(value, schema_name, root):
    errors = []
    _validate(value, {"$ref": f"#/components/schemas/{schema_name}"}, "$", errors, root)
    return errors


# ---------------------------------------------------------------------------
# Meta-validação estrutural da spec
# ---------------------------------------------------------------------------

class TestSpecParse:
    def test_parseia_e_tem_envelope_valido(self, spec):
        assert isinstance(spec, dict)
        assert spec["openapi"].startswith("3.0")
        assert spec["info"]["title"]
        assert spec["info"]["version"]

    def test_paths_sao_dict_com_operacoes(self, spec):
        assert isinstance(spec.get("paths"), dict) and spec["paths"]
        for path, item in spec["paths"].items():
            assert path.startswith("/api/") or path == "/docs"
            ops = {k for k in item if k in ("get", "post", "put", "delete", "patch")}
            assert ops, f"{path} sem operações"

    def test_operation_ids_unicos(self, spec):
        ids = []
        for path, item in spec["paths"].items():
            for method in ("get", "post", "put", "delete", "patch"):
                op = item.get(method)
                if isinstance(op, dict):
                    assert op.get("operationId"), f"{method.upper()} {path} sem operationId"
                    assert op.get("responses"), f"{method.upper()} {path} sem responses"
                    assert op.get("tags"), f"{method.upper()} {path} sem tag"
                    ids.append(op["operationId"])
        assert len(ids) == len(set(ids)), "operationIds duplicados"

    def test_parametros_path_definidos(self, spec):
        for path, item in spec["paths"].items():
            declared = set()
            params = list(item.get("parameters", []))
            for method in ("get", "post", "put", "delete"):
                op = item.get(method)
                if isinstance(op, dict):
                    assert op.get("responses"), f"{method.upper()} {path}"
                    params.extend(op.get("parameters", []))
            for p in params:
                if isinstance(p, dict) and "$ref" in p:
                    ref = p["$ref"]
                    target = spec
                    for part in ref[2:].split("/"):
                        target = target[part]
                    p = target
                if isinstance(p, dict) and p.get("in") == "path":
                    declared.add(p["name"])
            expected = set(re.findall(r"\{(\w+)\}", path))
            missing = expected - declared - set(item.keys())
            assert not missing, f"{path}: params de path não declarados: {missing}"

    def test_security_scheme_api_token(self, spec):
        schemes = spec["components"]["securitySchemes"]
        assert "ApiKeyHeader" in schemes
        assert schemes["ApiKeyHeader"]["name"] == "X-API-Token"


# ---------------------------------------------------------------------------
# Paridade url_map <-> spec
# ---------------------------------------------------------------------------

def _flask_api_routes():
    """{(oapath, method): rule} das rotas /api/* declaradas no Flask."""
    routes = {}
    for rule in app_module.app.url_map.iter_rules():
        if not rule.rule.startswith("/api/"):
            continue
        oapath = re.sub(r"<(?:[^:<>]+:)?([^<>]+)>", r"{\1}", rule.rule)
        for m in rule.methods - {"HEAD", "OPTIONS"}:
            routes[(oapath, m)] = rule.rule
    return routes


def _spec_routes(spec):
    routes = {}
    for oapath, item in spec["paths"].items():
        for m in ("get", "post", "put", "delete", "patch"):
            if m in item:
                routes[(oapath, m.upper())] = oapath
    return routes


class TestParidadeUrlMapSpec:
    def test_toda_rota_flask_esta_na_spec(self, spec):
        flask_routes = _flask_api_routes()
        spec_routes = _spec_routes(spec)
        faltando = set(flask_routes) - set(spec_routes)
        assert not faltando, (
            f"Rotas /api/* registradas no Flask ausentes na spec "
            f"(doc vai apodrecer): {sorted(faltando)}"
        )

    def test_toda_rota_da_spec_existe_no_flask(self, spec):
        flask_routes = _flask_api_routes()
        spec_routes = _spec_routes(spec)
        sobrando = set(spec_routes) - set(flask_routes)
        assert not sobrando, f"Rotas na spec sem implementação no Flask: {sorted(sobrando)}"

    def test_metodos_batem_exatamente(self, spec):
        assert set(_flask_api_routes()) == set(_spec_routes(spec))


# ---------------------------------------------------------------------------
# Schemas contra payloads reais da API
# ---------------------------------------------------------------------------

PRODUTO_EXEMPLO = {
    "id": "hd-8tb",
    "name": "HD 8TB",
    "queries": ["hd 8tb"],
    "pages": 2,
    "search_url": "https://www.olx.com.br/brasil?q={query}&opst=2&page={page}",
    "playwright_only": False,
    "min_price": None,
    "max_price": 900.0,
    "junk_pattern": r"\b(dock|cabo)\b",
    "exclude_keywords": ["notebook"],
    "enabled": True,
    "schedule": {
        "enabled": False,
        "mode": "interval",
        "interval_minutes": 360,
        "cron": "0 */6 * * *",
        "jitter_minutes": 0,
        "timezone": "America/Sao_Paulo",
    },
}

DEAL_EXEMPLO = {
    "id": "2583917482",
    "title": "HD Externo 8TB",
    "price": 599.9,
    "url": "https://www.olx.com.br/anuncio/2583917482",
    "query": "hd 8tb",
    "page": 1,
    "product_id": "hd-8tb",
    "product_name": "HD 8TB",
    "fetched_at": "2026-08-21T12:00:00+00:00",
    "matched": True,
    "notified": False,
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import olx_scraper

    state = tmp_path / ".olx"
    state.mkdir()
    monkeypatch.setattr(olx_scraper, "STATE_DIR", str(state))
    monkeypatch.setattr(olx_scraper, "CONFIG_FILE", str(state / "config.json"))
    monkeypatch.setattr(olx_scraper, "SCHEDULES_FILE", str(state / "schedules.json"))
    monkeypatch.setattr(olx_scraper, "COOKIE_JAR", str(state / "cookies.json"))
    monkeypatch.setattr(olx_scraper, "HISTORY_FILE", str(state / "deals.json"))
    monkeypatch.setattr(olx_scraper, "NOTIFIED_FILE", str(state / "notified.json"))
    monkeypatch.setattr(olx_scraper, "NOTIFIED_LOCK_FILE", str(state / ".notified.lock"))
    monkeypatch.setattr(app_module, "API_TOKEN", None)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestSchemasContraApiReal:
    def test_payload_de_produto_valida_contra_product(self, spec):
        errors = validate_against(PRODUTO_EXEMPLO, "Product", spec)
        assert not errors, errors

    def test_resposta_do_create_bate_com_product(self, client, spec):
        r = client.post("/api/products", json={
            "name": "Schema Teste", "id": "schema-test",
            "queries": ["teste"], "pages": 3, "max_price": 500,
        })
        assert r.status_code == 201, r.get_json()
        product = r.get_json()["product"]
        # resposta normalizada tem todos os campos required da spec
        errors = validate_against(product, "Product", spec)
        assert not errors, errors

    def test_deal_salvo_valida_contra_deal(self, client, spec):
        client.post("/api/products", json={
            "name": "Deal Teste", "id": "deal-test", "queries": ["x"], "pages": 1,
        })
        import json as _json
        import olx_scraper

        deal = dict(DEAL_EXEMPLO, product_id="deal-test")
        with open(olx_scraper.HISTORY_FILE, "w") as f:
            _json.dump([deal], f)
        j = client.get("/api/deals").get_json()
        assert j["count"] == 1
        errors = validate_against(j["deals"][0], "Deal", spec)
        assert not errors, errors

    def test_error_envelope_em_400(self, client, spec):
        r = client.post("/api/products", json={"queries": []})
        assert r.status_code == 400
        errors = validate_against(r.get_json(), "ErrorEnvelope", spec)
        assert not errors, errors

    def test_health_valida(self, client, spec):
        j = client.get("/api/health").get_json()
        assert j["ok"] is True
        assert "time" in j


class TestDocsESpecServidos:
    def test_openapi_yaml_servido(self, client, spec):
        r = client.get("/api/openapi.yaml")
        assert r.status_code == 200
        servido = yaml.safe_load(r.data.decode("utf-8"))
        assert servido == spec

    def test_docs_html_carrega_swagger_vendored(self, client):
        r = client.get("/docs")
        assert r.status_code == 200
        html = r.data.decode("utf-8")
        assert "/static/swagger-ui/swagger-ui-bundle.js" in html
        assert "/api/openapi.yaml" in html

    def test_assets_vendored_existem(self, client):
        for asset in ("swagger-ui.css", "swagger-ui-bundle.js"):
            r = client.get(f"/static/swagger-ui/{asset}")
            assert r.status_code == 200, asset
            assert len(r.data) > 100_000, asset
