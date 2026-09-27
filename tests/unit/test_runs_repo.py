"""
Caracterização: _prune_runs/append_run/delete_runs olx_scraper.py:442 (90d/1000, AND, JSON corrompido)
Isolado via tmp_path monkeypatch STATE_DIR/RUNS_FILE.
"""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture()
def isolated_runs(tmp_path, monkeypatch):
    import olx_scraper
    state = tmp_path / ".olx"
    state.mkdir()
    runs_file = state / "runs.json"
    lock_file = state / ".runs.lock"
    monkeypatch.setattr(olx_scraper, "STATE_DIR", str(state))
    monkeypatch.setattr(olx_scraper, "RUNS_FILE", str(runs_file))
    monkeypatch.setattr(olx_scraper, "RUNS_LOCK_FILE", str(lock_file))
    # ensure clean
    yield {"scraper": olx_scraper, "state": state, "runs_file": runs_file}


def _run(id_, started_at, product_ids_scraped=None, status="ok", source="manual"):
    return {
        "id": id_,
        "started_at": started_at,
        "finished_at": started_at,
        "status": status,
        "source": source,
        "product_ids_scraped": product_ids_scraped or [],
        "product_ids_requested": [],
        "duration_ms": 1000,
    }


def _iso_now():
    return datetime.now(timezone.utc).isoformat()


def _iso_days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


class TestPruneRuns:
    def test_prune_removes_older_than_90d(self, isolated_runs):
        scraper = isolated_runs["scraper"]
        old = _run("old", _iso_days_ago(95))
        recent = _run("recent", _iso_now())
        pruned = scraper._prune_runs([old, recent])
        assert [r["id"] for r in pruned] == ["recent"]

    def test_prune_keeps_last_1000_newest(self, isolated_runs):
        scraper = isolated_runs["scraper"]
        base = datetime.now(timezone.utc) - timedelta(days=1)
        runs = []
        for i in range(1005):
            iso = (base + timedelta(seconds=i)).isoformat()
            runs.append(_run(f"r{i:04d}", iso))
        pruned = scraper._prune_runs(runs)
        assert len(pruned) == 1000
        # mantém os mais recentes (últimos 1000)
        assert pruned[0]["id"] == "r0005"
        assert pruned[-1]["id"] == "r1004"

    def test_prune_sorted_asc(self, isolated_runs):
        scraper = isolated_runs["scraper"]
        t1 = _iso_now()
        t0 = _iso_days_ago(1)
        pruned = scraper._prune_runs([_run("a", t1), _run("b", t0)])
        assert [r["id"] for r in pruned] == ["b", "a"]

    def test_prune_ignores_invalid_started_at(self, isolated_runs):
        scraper = isolated_runs["scraper"]
        good = _run("good", _iso_now())
        bad = {"id": "bad", "started_at": "invalid"}
        pruned = scraper._prune_runs([good, bad])
        assert [r["id"] for r in pruned] == ["good"]


class TestAppendAndLoad:
    def test_append_and_load(self, isolated_runs):
        scraper = isolated_runs["scraper"]
        entry = _run("run1", _iso_now(), product_ids_scraped=["p1"])
        scraper.append_run(entry)
        runs = scraper.load_runs()
        assert len(runs) == 1 and runs[0]["id"] == "run1"

    def test_append_applies_prune_90d(self, isolated_runs):
        scraper = isolated_runs["scraper"]
        old = _run("old", _iso_days_ago(100))
        # escreve diretamente arquivo com old
        isolated_runs["runs_file"].write_text(json.dumps([old]))
        # append novo deve podar old
        scraper.append_run(_run("new", _iso_now()))
        runs = scraper.load_runs()
        assert [r["id"] for r in runs] == ["new"]

    def test_read_corrupted_returns_empty(self, isolated_runs):
        scraper = isolated_runs["scraper"]
        isolated_runs["runs_file"].write_text("not json {")
        assert scraper.load_runs() == []
        # _read_runs_file também retorna [] para lista não-lista
        isolated_runs["runs_file"].write_text(json.dumps({"not": "list"}))
        assert scraper.load_runs() == []

    def test_read_ignores_entries_missing_id_or_started_at(self, isolated_runs):
        scraper = isolated_runs["scraper"]
        bad1 = {"started_at": _iso_now()}
        bad2 = {"id": "x", "started_at": "bad"}
        good = _run("good", _iso_now())
        isolated_runs["runs_file"].write_text(json.dumps([bad1, bad2, good]))
        assert [r["id"] for r in scraper.load_runs()] == ["good"]


class TestDeleteRuns:
    def test_delete_by_before(self, isolated_runs):
        scraper = isolated_runs["scraper"]
        r1 = _run("r1", _iso_days_ago(5))
        r2 = _run("r2", _iso_now())
        isolated_runs["runs_file"].write_text(json.dumps([r1, r2]))
        before = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        removed = scraper.delete_runs(before=before)
        assert removed == 1
        assert [r["id"] for r in scraper.load_runs()] == ["r2"]

    def test_delete_by_product_id(self, isolated_runs):
        scraper = isolated_runs["scraper"]
        r1 = _run("r1", _iso_now(), product_ids_scraped=["p1"])
        r2 = _run("r2", _iso_now(), product_ids_scraped=["p2"])
        isolated_runs["runs_file"].write_text(json.dumps([r1, r2]))
        removed = scraper.delete_runs(product_id="p1")
        assert removed == 1
        assert [r["id"] for r in scraper.load_runs()] == ["r2"]

    def test_delete_by_status(self, isolated_runs):
        scraper = isolated_runs["scraper"]
        r_ok = _run("ok", _iso_now(), status="ok")
        r_err = _run("err", _iso_now(), status="error")
        isolated_runs["runs_file"].write_text(json.dumps([r_ok, r_err]))
        removed = scraper.delete_runs(status="error")
        assert removed == 1
        assert scraper.load_runs()[0]["id"] == "ok"

    def test_delete_and_requires_all_filters(self, isolated_runs):
        scraper = isolated_runs["scraper"]
        # r1 tem p1 e ok, r2 tem p1 e error
        r1 = _run("r1", _iso_now(), product_ids_scraped=["p1"], status="ok")
        r2 = _run("r2", _iso_now(), product_ids_scraped=["p1"], status="error")
        isolated_runs["runs_file"].write_text(json.dumps([r1, r2]))
        # AND: só remove se product_id=p1 E status=error -> só r2
        removed = scraper.delete_runs(product_id="p1", status="error")
        assert removed == 1
        assert [r["id"] for r in scraper.load_runs()] == ["r1"]

    def test_delete_by_source(self, isolated_runs):
        scraper = isolated_runs["scraper"]
        r1 = _run("r1", _iso_now(), source="manual")
        r2 = _run("r2", _iso_now(), source="schedule")
        isolated_runs["runs_file"].write_text(json.dumps([r1, r2]))
        removed = scraper.delete_runs(source="manual")
        assert removed == 1
        assert scraper.load_runs()[0]["id"] == "r2"

    def test_delete_invalid_before_raises(self, isolated_runs):
        scraper = isolated_runs["scraper"]
        with pytest.raises(ValueError):
            scraper.delete_runs(before="not-iso")

    def test_delete_invalid_status_raises(self, isolated_runs):
        scraper = isolated_runs["scraper"]
        with pytest.raises(ValueError):
            scraper.delete_runs(status="unknown")

    def test_delete_before_plus_form(self, isolated_runs):
        scraper = isolated_runs["scraper"]
        r_old = _run("old", _iso_days_ago(3))
        r_new = _run("new", _iso_now())
        isolated_runs["runs_file"].write_text(json.dumps([r_old, r_new]))
        before = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        # simula decodificação query string onde '+' virou ' '
        before_spaced = before.replace("+", " ")
        removed = scraper.delete_runs(before=before_spaced)
        assert removed == 1

    def test_save_runs_prunes(self, isolated_runs):
        scraper = isolated_runs["scraper"]
        runs = [_run(f"r{i}", _iso_days_ago(100)) for i in range(3)]
        runs.append(_run("good", _iso_now()))
        scraper.save_runs(runs)
        assert [r["id"] for r in scraper.load_runs()] == ["good"]
