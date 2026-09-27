"""
Caracterização: GET /api/deals?re-evaluate=1&adherent&matched_only&price_min app.py:1256
+ view aderente, filtros combináveis, compat histórica.
"""
import json
import pytest


@pytest.fixture()
def isolated_view(tmp_path, monkeypatch):
    import olx_scraper, app as app_module
    state = tmp_path / ".olx"
    state.mkdir()
    monkeypatch.setattr(olx_scraper, "STATE_DIR", str(state))
    monkeypatch.setattr(olx_scraper, "CONFIG_FILE", str(state / "config.json"))
    monkeypatch.setattr(olx_scraper, "HISTORY_FILE", str(state / "deals.json"))
    monkeypatch.setattr(olx_scraper, "NOTIFIED_FILE", str(state / "notified.json"))
    monkeypatch.setattr(olx_scraper, "NOTIFIED_LOCK_FILE", str(state / ".notified.lock"))
    monkeypatch.setattr(olx_scraper, "RUNS_FILE", str(state / "runs.json"))
    monkeypatch.setattr(olx_scraper, "RUNS_LOCK_FILE", str(state / ".runs.lock"))
    monkeypatch.setattr(olx_scraper, "SCHEDULES_FILE", str(state / "schedules.json"))
    monkeypatch.setattr(olx_scraper, "COOKIE_JAR", str(state / "cookies.json"))

    # produto config: pikimin com exclude e include, e hd
    cfg = {
        "products": [
            {
                "id": "pikimin",
                "name": "Pikmin",
                "queries": ["pikmin"],
                "pages": 1,
                "min_price": None,
                "max_price": None,
                "junk_pattern": "",
                "exclude_keywords": ["wii u", "game cube"],
                "include_pattern": "pikmin",
                "enabled": True,
            },
            {
                "id": "hd-6tb",
                "name": "HD 6TB",
                "queries": ["hd 6tb"],
                "pages": 1,
                "min_price": 100,
                "max_price": 250,
                "junk_pattern": "",
                "exclude_keywords": [],
                "include_pattern": "",
                "enabled": True,
            },
        ],
        "telegram_token": None,
        "telegram_chat_id": None,
        "notify_price_change_pct": 5,
    }
    # salva via save_config para normalizar
    olx_scraper.save_config(cfg)

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield {"client": client, "scraper": olx_scraper, "state": state, "app": app_module}


def _seed_deals(state, deals):
    path = state / "deals.json"
    with open(path, "w") as f:
        json.dump(deals, f)


def _mk(pid, id_, title, price, matched=True):
    return {
        "id": id_,
        "title": title,
        "price": price,
        "url": f"https://www.olx.com.br/item/{id_}.html",
        "product_id": pid,
        "product_name": pid,
        "fetched_at": "2026-08-27T00:00:00+00:00",
        "matched": matched,
        "discard_reason": None if matched else "price",
    }


class TestDealsViewBasic:
    def test_matched_only_historic(self, isolated_view):
        deals = [
            _mk("hd-6tb", "1", "HD 8TB", 150, True),
            _mk("hd-6tb", "2", "HD 500GB", 50, False),
        ]
        _seed_deals(isolated_view["state"], deals)
        j = isolated_view["client"].get("/api/deals?matched_only=1").get_json()
        assert j["total"] == 1 and j["deals"][0]["id"] == "1"

    def test_price_filter_ad_hoc(self, isolated_view):
        deals = [
            _mk("hd-6tb", "1", "HD 50", 50, True),
            _mk("hd-6tb", "2", "HD 150", 150, True),
            _mk("hd-6tb", "3", "HD 250", 250, True),
        ]
        _seed_deals(isolated_view["state"], deals)
        # sem re-evaluate, filtra por price range
        j = isolated_view["client"].get("/api/deals?price_min=100&price_max=200").get_json()
        assert [d["id"] for d in j["deals"]] == ["2"]

    def test_price_filter_invalid_returns_400(self, isolated_view):
        _seed_deals(isolated_view["state"], [_mk("hd-6tb", "1", "HD", 150)])
        r = isolated_view["client"].get("/api/deals?price_min=foo")
        assert r.status_code == 400
        r = isolated_view["client"].get("/api/deals?price_min=200&price_max=100")
        assert r.status_code == 400

    def test_price_filter_excludes_none(self, isolated_view):
        deals = [
            _mk("hd-6tb", "1", "HD sem preco", None, True),
            _mk("hd-6tb", "2", "HD 150", 150, True),
        ]
        _seed_deals(isolated_view["state"], deals)
        j = isolated_view["client"].get("/api/deals?price_min=100").get_json()
        assert [d["id"] for d in j["deals"]] == ["2"]

    def test_adherent_without_reevaluate_400(self, isolated_view):
        _seed_deals(isolated_view["state"], [_mk("hd-6tb", "1", "HD", 150)])
        r = isolated_view["client"].get("/api/deals?adherent=0")
        assert r.status_code == 400

    def test_re_evaluate_adds_fields(self, isolated_view):
        deals = [_mk("pikimin", "1", "Pikmin 3 Deluxe", 200, True)]
        _seed_deals(isolated_view["state"], deals)
        j = isolated_view["client"].get("/api/deals?re-evaluate=1").get_json()
        assert "matched_current" in j["deals"][0]
        assert "discard_reason_current" in j["deals"][0]


class TestReEvaluateFilters:
    def test_re_evaluate_exclude_wii_u(self, isolated_view):
        # pikimin com exclude wii u, deal legado matched=true mas title tem wii u
        deals = [
            _mk("pikimin", "1452796691", "Pikmin 3 Wii U", 200, True),
            _mk("pikimin", "1507077843", "Pikmin 2 game cube jp", 200, True),
            _mk("pikimin", "1", "Pikmin 3 Deluxe Switch", 200, True),
            _mk("pikimin", "2", "Starfox Switch", 200, True),  # include falha -> não aderente
        ]
        _seed_deals(isolated_view["state"], deals)
        # sem re-evaluate, historic matched_only retorna 4
        j = isolated_view["client"].get("/api/deals?matched_only=1").get_json()
        assert j["total"] == 4
        # com re-evaluate, matched_only filtra current
        j2 = isolated_view["client"].get("/api/deals?re-evaluate=1&matched_only=1").get_json()
        # só Pikmin 3 Deluxe deve permanecer (os outros são wii u/game cube/include)
        ids = [d["id"] for d in j2["deals"]]
        assert ids == ["1"]
        # adherent=0 lista não aderentes
        j3 = isolated_view["client"].get("/api/deals?re-evaluate=1&matched_only=1&adherent=0").get_json()
        # com adherent, matched_only filtra histórico e adherent filtra current -> todos os 4 históricos são matched, mas current não
        # deve retornar 3 (wii u, game cube, include falha) — Starfox também falha include
        assert j3["total"] == 3
        assert set(d["id"] for d in j3["deals"]) == {"1452796691", "1507077843", "2"}

    def test_adherent_without_matched_only(self, isolated_view):
        deals = [
            _mk("pikimin", "1", "Pikmin 3 Wii U", 200, True),
            _mk("pikimin", "2", "Pikmin 3 Deluxe", 200, True),
        ]
        _seed_deals(isolated_view["state"], deals)
        # adherent=0 sem matched_only deve listar só não aderentes na view current
        j = isolated_view["client"].get("/api/deals?re-evaluate=1&adherent=0").get_json()
        assert [d["id"] for d in j["deals"]] == ["1"]
        j2 = isolated_view["client"].get("/api/deals?re-evaluate=1&adherent=1").get_json()
        assert [d["id"] for d in j2["deals"]] == ["2"]

    def test_orphan_via_reevaluate(self, isolated_view):
        deals = [
            _mk("hd-6-12tb", "343", "HD antigo orfao", 800, True),  # produto não existe mais
            _mk("hd-6tb", "2", "HD 150", 150, True),
        ]
        _seed_deals(isolated_view["state"], deals)
        j = isolated_view["client"].get("/api/deals?re-evaluate=1&adherent=0").get_json()
        # orfao deve aparecer como não aderente
        assert any(d["id"] == "343" and d["discard_reason_current"] == "orphan" for d in j["deals"])

    def test_price_reevaluate_vs_historic(self, isolated_view):
        # hd-6tb tem max 250, deal a 300 é matched histórico mas não aderente current
        deals = [
            _mk("hd-6tb", "1", "HD 300", 300, True),  # historicamente matched
            _mk("hd-6tb", "2", "HD 150", 150, True),
        ]
        _seed_deals(isolated_view["state"], deals)
        j_hist = isolated_view["client"].get("/api/deals?matched_only=1").get_json()
        assert j_hist["total"] == 2
        j_cur = isolated_view["client"].get("/api/deals?re-evaluate=1&matched_only=1").get_json()
        assert [d["id"] for d in j_cur["deals"]] == ["2"]

    def test_VIEW_alias_current(self, isolated_view):
        deals = [_mk("pikimin", "1", "Pikmin 3 Wii U", 200, True)]
        _seed_deals(isolated_view["state"], deals)
        j = isolated_view["client"].get("/api/deals?view=current&adherent=0").get_json()
        assert j["total"] == 1

    def test_combined_price_and_re_evaluate(self, isolated_view):
        deals = [
            _mk("hd-6tb", "1", "HD 50", 50, True),   # fora faixa histórica? mas price 50
            _mk("hd-6tb", "2", "HD 150", 150, True),
            _mk("hd-6tb", "3", "HD 300", 300, True),
        ]
        _seed_deals(isolated_view["state"], deals)
        # re-evaluate + price filter 100-200
        j = isolated_view["client"].get("/api/deals?re-evaluate=1&price_min=100&price_max=200&adherent=1").get_json()
        assert [d["id"] for d in j["deals"]] == ["2"]
