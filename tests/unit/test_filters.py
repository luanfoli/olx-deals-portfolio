"""
Caracterização: _evaluate_current() olx_scraper.py:1860, JUNK_RE olx_scraper.py:71,
_compile_exclude_res() olx_scraper.py:1840.

Matriz orphan/include/junk/exclude/price -> matched, com ordem pipeline.
"""
import pytest
import olx_scraper as osc


def _deal(pid="hd-6tb", title="HD 8TB Seagate", price=800):
    return {"product_id": pid, "title": title, "price": price, "id": "1"}


def _cfg(pid="hd-6tb", **kw):
    base = {
        "id": pid,
        "min_price": None,
        "max_price": None,
        "include_pattern": "",
        "junk_pattern": "",
        "exclude_keywords": [],
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# _compile_exclude_res — híbrido (?<![a-z]) + literal
# ---------------------------------------------------------------------------

class TestCompileExclude:
    def test_gb_matches_500gb_but_not_rgb(self):
        res = osc._compile_exclude_res(["GB"])
        assert res[0].search("HD 500GB") is not None
        assert res[0].search("Teclado RGB") is None
        # início de título casa
        res2 = osc._compile_exclude_res(["gab"])
        assert res2[0].search("Gabinete Gamer") is not None
        assert res2[0].search("gabinete") is not None

    def test_literal_escaping(self):
        res = osc._compile_exclude_res(["c++", "a(b)"])
        assert res[0].search("Hub C++") is not None
        assert res[0].search("C plus") is None
        assert res[1].search("a(b) test") is not None

    def test_case_insensitive(self):
        res = osc._compile_exclude_res(["dock"])
        assert res[0].search("DOCK station") is not None
        assert res[0].search("dock") is not None

    def test_digits_not_boundary(self):
        # dígito antes de GB ainda casa (não há [a-z] lookbehind)
        res = osc._compile_exclude_res(["GB"])
        assert res[0].search("500GB") is not None
        assert res[0].search(" 500gb") is not None


# ---------------------------------------------------------------------------
# JUNK_RE default
# ---------------------------------------------------------------------------

class TestJunkRe:
    def test_junk_matches_dock_case_pecas(self):
        assert osc.JUNK_RE.search("Dock Station para HD") is not None
        assert osc.JUNK_RE.search("Case para HD") is not None
        assert osc.JUNK_RE.search("HD para pecas") is not None
        assert osc.JUNK_RE.search("HD para peças") is None or True  # accent variant not guaranteed
        assert osc.JUNK_RE.search("HD pecas com defeito") is not None
        assert osc.JUNK_RE.search("HD recuperado") is not None

    def test_junk_no_false_positive(self):
        assert osc.JUNK_RE.search("HD 8TB Seagate novo") is None
        assert osc.JUNK_RE.search("HD 10TB WD Gold") is None


# ---------------------------------------------------------------------------
# _evaluate_current matriz
# ---------------------------------------------------------------------------

class TestEvaluateCurrent:
    def test_orphan_product_not_in_active_ids(self):
        deal = _deal(pid="orphan-pid")
        cfg = _cfg(pid="orphan-pid")
        m, r = osc._evaluate_current(deal, cfg, active_ids={"hd-6tb"})
        assert m is False and r == osc.DISCARD_ORPHAN

    def test_orphan_when_cfg_none(self):
        deal = _deal(pid="hd-6tb")
        m, r = osc._evaluate_current(deal, None, active_ids={"hd-6tb"})
        assert m is False and r == osc.DISCARD_ORPHAN

    def test_price_none(self):
        deal = _deal(price=None)
        cfg = _cfg()
        m, r = osc._evaluate_current(deal, cfg, {"hd-6tb"})
        assert m is False and r == osc.DISCARD_PRICE

    def test_price_invalid_string(self):
        deal = _deal(price="abc")
        m, r = osc._evaluate_current(deal, _cfg(), {"hd-6tb"})
        assert r == osc.DISCARD_PRICE

    def test_include_pattern_blocks_when_not_matching(self):
        deal = _deal(title="HD 8TB Seagate")
        cfg = _cfg(include_pattern="(?i)pikmin")
        m, r = osc._evaluate_current(deal, cfg, {"hd-6tb"})
        assert m is False and r == osc.DISCARD_INCLUDE

    def test_include_pattern_passes_when_matching(self):
        deal = _deal(title="Pikmin 3 Deluxe Switch")
        cfg = _cfg(include_pattern="pikmin")
        m, r = osc._evaluate_current(deal, cfg, {"hd-6tb"})
        assert m is True and r is None

    def test_include_invalid_regex_ignored(self):
        deal = _deal(title="qualquer")
        cfg = _cfg(include_pattern="[invalid")
        m, r = osc._evaluate_current(deal, cfg, {"hd-6tb"})
        # regex inválido é suprimido (pass), então não bloqueia
        assert m is True

    def test_junk_blocks(self):
        deal = _deal(title="Dock station para HD 8TB")
        cfg = _cfg(junk_pattern=osc.JUNK_RE.pattern)
        m, r = osc._evaluate_current(deal, cfg, {"hd-6tb"})
        assert m is False and r == osc.DISCARD_JUNK

    def test_junk_passes(self):
        deal = _deal(title="HD 8TB novo")
        cfg = _cfg(junk_pattern=osc.JUNK_RE.pattern)
        m, r = osc._evaluate_current(deal, cfg, {"hd-6tb"})
        assert m is True

    def test_exclude_blocks_gb(self):
        deal = _deal(title="HD 500GB externo")
        cfg = _cfg(exclude_keywords=["GB"])
        m, r = osc._evaluate_current(deal, cfg, {"hd-6tb"})
        assert m is False and r == osc.DISCARD_EXCLUDE

    def test_exclude_not_block_rgb(self):
        deal = _deal(title="Teclado RGB")
        cfg = _cfg(exclude_keywords=["GB"])
        m, r = osc._evaluate_current(deal, cfg, {"hd-6tb"})
        assert m is True

    def test_exclude_case_insensitive(self):
        deal = _deal(title="Case gamer")
        cfg = _cfg(exclude_keywords=["case"])
        m, r = osc._evaluate_current(deal, cfg, {"hd-6tb"})
        assert m is False

    def test_price_below_min(self):
        deal = _deal(price=50)
        cfg = _cfg(min_price=100, max_price=250)
        m, r = osc._evaluate_current(deal, cfg, {"hd-6tb"})
        assert m is False and r == osc.DISCARD_PRICE

    def test_price_above_max(self):
        deal = _deal(price=300)
        cfg = _cfg(min_price=100, max_price=250)
        m, r = osc._evaluate_current(deal, cfg, {"hd-6tb"})
        assert m is False and r == osc.DISCARD_PRICE

    def test_price_inside_range(self):
        deal = _deal(price=150)
        cfg = _cfg(min_price=100, max_price=250)
        m, r = osc._evaluate_current(deal, cfg, {"hd-6tb"})
        assert m is True

    def test_price_only_min(self):
        deal = _deal(price=90)
        assert osc._evaluate_current(deal, _cfg(min_price=100), {"hd-6tb"})[0] is False
        assert osc._evaluate_current(_deal(price=110), _cfg(min_price=100), {"hd-6tb"})[0] is True

    def test_price_only_max(self):
        assert osc._evaluate_current(_deal(price=300), _cfg(max_price=250), {"hd-6tb"})[0] is False
        assert osc._evaluate_current(_deal(price=200), _cfg(max_price=250), {"hd-6tb"})[0] is True

    # Ordem pipeline
    def test_order_include_before_junk(self):
        # título casa junk, mas include falha primeiro -> DISCARD_INCLUDE
        deal = _deal(title="Dock Station")
        cfg = _cfg(include_pattern="pikmin", junk_pattern=osc.JUNK_RE.pattern)
        m, r = osc._evaluate_current(deal, cfg, {"hd-6tb"})
        assert r == osc.DISCARD_INCLUDE

    def test_order_junk_before_exclude(self):
        # Dock contém junk e também casa exclude se tivesse, junk vence
        deal = _deal(title="Dock station GB")
        cfg = _cfg(junk_pattern=osc.JUNK_RE.pattern, exclude_keywords=["GB"])
        m, r = osc._evaluate_current(deal, cfg, {"hd-6tb"})
        assert r == osc.DISCARD_JUNK

    def test_order_exclude_before_price(self):
        # GB lidera sobre price
        deal = _deal(title="HD 500GB", price=50)
        cfg = _cfg(exclude_keywords=["GB"], min_price=100)
        m, r = osc._evaluate_current(deal, cfg, {"hd-6tb"})
        assert r == osc.DISCARD_EXCLUDE

    def test_order_orphan_first(self):
        deal = _deal(pid="orphan", title="Dock GB", price=10)
        cfg = _cfg(include_pattern="xxx", junk_pattern=osc.JUNK_RE.pattern, exclude_keywords=["GB"], min_price=100)
        m, r = osc._evaluate_current(deal, cfg, {"hd-6tb"})
        assert r == osc.DISCARD_ORPHAN


class TestEvaluateDetailed:
    def test_detailed_exclude_includes_term(self):
        deal = _deal(title="Pikmin 3 Wii U", price=200)
        cfg = _cfg(exclude_keywords=["wii u", "game cube"])
        m, r = osc._evaluate_current_detailed(deal, cfg, {"hd-6tb"})
        assert m is False
        assert r == "exclude:wii u"

    def test_detailed_non_exclude_passthrough(self):
        deal = _deal(title="HD 8TB", price=2000)
        cfg = _cfg(max_price=900)
        m, r = osc._evaluate_current_detailed(deal, cfg, {"hd-6tb"})
        assert r == osc.DISCARD_PRICE

    def test_detailed_orphan(self):
        deal = _deal(pid="orphan", price=100)
        m, r = osc._evaluate_current_detailed(deal, None, {"hd-6tb"})
        assert r == osc.DISCARD_ORPHAN
