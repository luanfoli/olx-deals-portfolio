"""
Caracterização: _migrate_legacy_config() olx_scraper.py:1568 flat -> products[]
Não toca produção.
"""
import pytest
import olx_scraper as osc


class TestMigrateLegacy:
    def test_new_format_passthrough_normalizes(self):
        stored = {
            "products": [
                {"name": "HD Teste", "id": "hd-teste", "queries": ["hd 8tb"], "pages": 2, "min_price": 100, "max_price": 900}
            ],
            "telegram_token": "tok",
            "telegram_chat_id": "chat",
        }
        out = osc._migrate_legacy_config(stored)
        assert len(out["products"]) == 1
        assert out["products"][0]["id"] == "hd-teste"
        assert out["products"][0]["queries"] == ["hd 8tb"]
        assert out["telegram_token"] == "tok"

    def test_new_format_empty_products_fallback_default(self):
        out = osc._migrate_legacy_config({"products": []})
        assert len(out["products"]) == 1
        assert out["products"][0]["id"] == "hd-6-12tb"

    def test_new_format_filters_invalid_entries(self):
        stored = {"products": ["not dict", {"name": "P1", "queries": ["x"]}]}
        out = osc._migrate_legacy_config(stored)
        assert len(out["products"]) == 1
        assert out["products"][0]["name"] == "P1"

    def test_legacy_flat_with_max_price_per_tb(self):
        legacy = {
            "queries": ["hd 6tb"],
            "pages": 3,
            "max_price_per_tb": 75,
            "max_tb": 12,
        }
        out = osc._migrate_legacy_config(legacy)
        p = out["products"][0]
        assert p["id"] == "hd-6-12tb"
        assert p["queries"] == ["hd 6tb"]
        assert p["pages"] == 3
        assert p["max_price"] == pytest.approx(900.0)  # 75*12

    def test_legacy_flat_defaults_when_missing(self):
        out = osc._migrate_legacy_config({})
        p = out["products"][0]
        assert p["id"] == "hd-6-12tb"
        assert p["queries"] == osc.DEFAULT_QUERIES
        assert p["max_price"] == pytest.approx(900.0)

    def test_legacy_flat_preserves_min_price(self):
        out = osc._migrate_legacy_config({"min_price": 200, "max_price": 800})
        p = out["products"][0]
        assert p["min_price"] == pytest.approx(200.0)
        assert p["max_price"] == pytest.approx(800.0)

    def test_legacy_max_price_per_tb_invalid_defaults(self):
        out = osc._migrate_legacy_config({"max_price_per_tb": "bad"})
        assert out["products"][0]["max_price"] == pytest.approx(900.0)

    def test_legacy_with_search_url_and_junk(self):
        legacy = {"search_url": osc.SEARCH_URL, "junk_pattern": osc.JUNK_RE.pattern, "playwright_only": True}
        out = osc._migrate_legacy_config(legacy)
        p = out["products"][0]
        assert p["search_url"] == osc.SEARCH_URL
        assert p["junk_pattern"] == osc.JUNK_RE.pattern
        assert p["playwright_only"] is True

    def test_notify_pct_and_tuner_normalized(self):
        stored = {
            "products": [{"name": "P1", "queries": ["x"]}],
            "notify_price_change_pct": 10,
            "tuner_llm": {"provider": "mock", "model": "m"},
        }
        out = osc._migrate_legacy_config(stored)
        assert out["notify_price_change_pct"] == pytest.approx(10.0)
        assert out["tuner_llm"]["provider"] == "mock"

    def test_duplicate_ids_handled(self):
        stored = {"products": [{"name": "P1", "id": "dup", "queries": ["x"]}, {"name": "P2", "id": "dup", "queries": ["y"]}]}
        out = osc._migrate_legacy_config(stored)
        ids = [p["id"] for p in out["products"]]
        assert ids[0] == "dup"
        assert ids[1] == "dup-2"

    def test_slugify_and_existing_ids(self):
        stored = {"products": [{"name": "Meu Produto", "queries": ["x"]}, {"name": "Meu Produto", "queries": ["y"]}]}
        out = osc._migrate_legacy_config(stored)
        # primeiro vira meu-produto, segundo meu-produto-2
        assert out["products"][0]["id"] == "meu-produto"
        assert out["products"][1]["id"] == "meu-produto-2"

    def test_load_config_migration_via_file(self, tmp_path, monkeypatch):
        import olx_scraper, json, os
        state = tmp_path / ".olx"
        state.mkdir()
        cfg_file = state / "config.json"
        monkeypatch.setattr(olx_scraper, "STATE_DIR", str(state))
        monkeypatch.setattr(olx_scraper, "CONFIG_FILE", str(cfg_file))
        # escreve legado flat
        legacy = {"queries": ["hd 6tb"], "min_price": 100}
        cfg_file.write_text(json.dumps(legacy))
        cfg = olx_scraper.load_config()
        assert cfg["products"][0]["queries"] == ["hd 6tb"]
        assert cfg["products"][0]["min_price"] == pytest.approx(100.0)
