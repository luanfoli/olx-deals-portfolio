"""
Testes para agendador por produto (Fase 2).

Cobre:
- _normalize_schedule (olx_scraper.py:78)
- _validate_product_payload schedule (app.py:517)
- save/load config + schedules.json
- API /api/products PUT com schedule (habilitar/desabilitar, interval, cron, jitter)
- API /api/schedules/status reflete jobs, next_run, last_run
- Regras: produto desabilitado não agenda, toggle sem schedule preserva, persistência após restart (_get_next_run_for_interval)

Isolado via tmpdir monkeypatch de olx_scraper.STATE_DIR/CONFIG_FILE/SCHEDULES_FILE/COOKIE_JAR/HISTORY_FILE.
Scheduler APScheduler real em memória, limpo entre testes.
"""

import json
import os
import tempfile
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest


@pytest.fixture()
def isolated_olx(tmp_path, monkeypatch):
    """Isola ~/.olx em tmp_path e reseta scheduler."""
    import olx_scraper
    import app as app_module

    # cria estrutura tmp
    state = tmp_path / ".olx"
    state.mkdir()
    cfg_file = state / "config.json"
    sched_file = state / "schedules.json"
    cookies_file = state / "cookies.json"
    hist_file = state / "deals.json"

    # monkeypatch constantes de módulo
    monkeypatch.setattr(olx_scraper, "STATE_DIR", str(state))
    monkeypatch.setattr(olx_scraper, "CONFIG_FILE", str(cfg_file))
    monkeypatch.setattr(olx_scraper, "SCHEDULES_FILE", str(sched_file))
    monkeypatch.setattr(olx_scraper, "COOKIE_JAR", str(cookies_file))
    monkeypatch.setattr(olx_scraper, "HISTORY_FILE", str(hist_file))
    monkeypatch.setattr(olx_scraper, "RUNS_FILE", str(state / "runs.json"))
    monkeypatch.setattr(olx_scraper, "RUNS_LOCK_FILE", str(state / ".runs.lock"))

    # app importa olx_scraper, então já enxerga novos paths via load_config()
    # limpa scheduler global entre testes
    sched = app_module._get_scheduler()
    if sched is not None:
        try:
            sched.remove_all_jobs()
        except Exception:
            pass
        # garante rodando
        if not sched.running:
            try:
                sched.start()
            except Exception:
                pass
        else:
            # remove jobs mas mantém running
            pass
    # limpa fila
    with app_module.job_lock:
        app_module.schedule_queue.clear()

    yield {
        "state": state,
        "cfg_file": cfg_file,
        "sched_file": sched_file,
        "cookies_file": cookies_file,
        "hist_file": hist_file,
        "app": app_module,
        "olx_scraper": olx_scraper,
        "tmp_path": tmp_path,
    }

    # teardown: remove jobs
    sched = app_module._get_scheduler()
    if sched is not None:
        try:
            sched.remove_all_jobs()
        except Exception:
            pass
    with app_module.job_lock:
        app_module.schedule_queue.clear()


@pytest.fixture()
def client(isolated_olx):
    app = isolated_olx["app"].app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _create_product(client, payload):
    r = client.post("/api/products", json=payload)
    return r


# ---------------------------------------------------------------------------
# Unit: _normalize_schedule
# ---------------------------------------------------------------------------

class TestNormalizeSchedule:
    def test_defaults_when_none(self, isolated_olx):
        scraper = isolated_olx["olx_scraper"]
        out = scraper._normalize_schedule(None)
        assert out["enabled"] is False
        assert out["mode"] == "interval"
        assert out["interval_minutes"] == 360
        assert out["jitter_minutes"] == 0
        assert out["cron"] == "0 */6 * * *"
        assert out["timezone"] == "America/Sao_Paulo"

    def test_interval_clamp(self, isolated_olx):
        scraper = isolated_olx["olx_scraper"]
        # abaixo mínimo 15 -> clamp para 15
        out = scraper._normalize_schedule({"enabled": True, "mode": "interval", "interval_minutes": 5})
        assert out["interval_minutes"] == 15
        # acima máximo -> clamp 10080
        out = scraper._normalize_schedule({"enabled": True, "mode": "interval", "interval_minutes": 99999})
        assert out["interval_minutes"] == 10080
        # normal
        out = scraper._normalize_schedule({"enabled": True, "mode": "interval", "interval_minutes": 30})
        assert out["interval_minutes"] == 30

    def test_jitter_clamp(self, isolated_olx):
        scraper = isolated_olx["olx_scraper"]
        out = scraper._normalize_schedule({"jitter_minutes": -5})
        assert out["jitter_minutes"] == 0
        out = scraper._normalize_schedule({"jitter_minutes": 99})
        assert out["jitter_minutes"] == 60
        out = scraper._normalize_schedule({"jitter_minutes": 5})
        assert out["jitter_minutes"] == 5

    def test_cron_invalid_fallback(self, isolated_olx):
        scraper = isolated_olx["olx_scraper"]
        # cron com 4 campos -> fallback
        out = scraper._normalize_schedule({"mode": "cron", "cron": "0 */6 * *"})
        assert out["cron"] == "0 */6 * * *"
        # válido preserva
        out = scraper._normalize_schedule({"mode": "cron", "cron": "0 9,19 * * *"})
        assert out["cron"] == "0 9,19 * * *"

    def test_interval_preserves_cron_for_compat(self, isolated_olx):
        scraper = isolated_olx["olx_scraper"]
        out = scraper._normalize_schedule({"mode": "interval", "interval_minutes": 60, "cron": "0 9,19 * * *"})
        # interval mantém cron para troca futura
        assert out["cron"] == "0 9,19 * * *"
        assert out["interval_minutes"] == 60


# ---------------------------------------------------------------------------
# Unit: _validate_product_payload
# ---------------------------------------------------------------------------

class TestValidateSchedule:
    def test_valid_interval(self, isolated_olx):
        app_module = isolated_olx["app"]
        errors = app_module._validate_product_payload({"schedule": {"enabled": True, "mode": "interval", "interval_minutes": 30, "jitter_minutes": 5}}, is_update=True)
        assert errors == []

    def test_interval_too_small(self, isolated_olx):
        app_module = isolated_olx["app"]
        errors = app_module._validate_product_payload({"schedule": {"enabled": True, "mode": "interval", "interval_minutes": 10}}, is_update=True)
        assert any("15" in e and "10080" in e for e in errors)

    def test_jitter_out_of_range(self, isolated_olx):
        app_module = isolated_olx["app"]
        errors = app_module._validate_product_payload({"schedule": {"enabled": True, "mode": "interval", "interval_minutes": 30, "jitter_minutes": 99}}, is_update=True)
        assert any("jitter" in e for e in errors)

    def test_cron_needs_5_fields(self, isolated_olx):
        app_module = isolated_olx["app"]
        errors = app_module._validate_product_payload({"schedule": {"enabled": True, "mode": "cron", "cron": "invalido"}}, is_update=True)
        assert any("5 campos" in e for e in errors)

    def test_cron_invalid_expression(self, isolated_olx):
        app_module = isolated_olx["app"]
        # cron com 5 campos mas hora inválida 25
        errors = app_module._validate_product_payload({"schedule": {"enabled": True, "mode": "cron", "cron": "0 25 * * *"}}, is_update=True)
        # deve rejeitar via CronTrigger
        assert any("cron" in e.lower() for e in errors)

    def test_cron_required_when_enabled(self, isolated_olx):
        app_module = isolated_olx["app"]
        # mode cron sem cron key e enabled true -> erro
        errors = app_module._validate_product_payload({"schedule": {"enabled": True, "mode": "cron"}}, is_update=True)
        assert any("cron" in e.lower() for e in errors)

    def test_timezone_fixed(self, isolated_olx):
        app_module = isolated_olx["app"]
        errors = app_module._validate_product_payload({"schedule": {"enabled": True, "mode": "interval", "interval_minutes": 30, "timezone": "UTC"}}, is_update=True)
        assert any("America/Sao_Paulo" in e for e in errors)


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_save_load_preserves_schedule(self, isolated_olx, client):
        scraper = isolated_olx["olx_scraper"]
        # cria produto via save_config direto
        scraper.save_config({"products": [{
            "name": "Teste HD",
            "id": "teste-hd",
            "queries": ["hd 8tb"],
            "pages": 2,
            "max_price": 900,
            "schedule": {"enabled": True, "mode": "interval", "interval_minutes": 60, "jitter_minutes": 5, "cron": "0 */6 * * *", "timezone": "America/Sao_Paulo"}
        }]})
        cfg = scraper.load_config()
        assert cfg["products"][0]["schedule"]["enabled"] is True
        assert cfg["products"][0]["schedule"]["interval_minutes"] == 60
        assert cfg["products"][0]["schedule"]["jitter_minutes"] == 5
        # arquivo contém
        on_disk = json.loads(isolated_olx["cfg_file"].read_text())
        assert on_disk["products"][0]["schedule"]["enabled"] is True

    def test_schedules_json_last_run(self, isolated_olx):
        scraper = isolated_olx["olx_scraper"]
        scraper.record_schedule_run("teste-hd", status="ok", matched_count=3, results_count=5)
        data = scraper.load_schedules()
        assert "teste-hd" in data["last_runs"]
        assert data["last_runs"]["teste-hd"]["status"] == "ok"
        assert data["last_runs"]["teste-hd"]["matched_count"] == 3
        # arquivo existe
        assert isolated_olx["sched_file"].exists()

    def test_last_run_survives_reload(self, isolated_olx):
        scraper = isolated_olx["olx_scraper"]
        scraper.record_schedule_run("p1", status="ok", at_iso="2026-08-21T12:00:00+00:00")
        # simula restart: reload
        data = scraper.load_schedules()
        assert data["last_runs"]["p1"]["at"] == "2026-08-21T12:00:00+00:00"


# ---------------------------------------------------------------------------
# Integração API: habilitar/desabilitar cria jobs
# ---------------------------------------------------------------------------

class TestAPIEnableDisable:
    def test_enable_interval_creates_job_with_next_run(self, isolated_olx, client):
        # cria produto desabilitado
        r = _create_product(client, {"name": "Produto A", "id": "produto-a", "queries": ["teste"], "pages": 1, "max_price": 100, "schedule": {"enabled": False, "mode": "interval", "interval_minutes": 360}})
        assert r.status_code == 201
        # status deve mostrar enabled false e next_run None
        r = client.get("/api/schedules/status")
        j = r.get_json()
        sched_a = next(s for s in j["schedules"] if s["product_id"] == "produto-a")
        assert sched_a["enabled"] is False
        assert sched_a["next_run"] is None

        # habilita via PUT (payload igual ao modal index.html:693)
        r = client.put("/api/products/produto-a", json={"schedule": {"enabled": True, "mode": "interval", "interval_minutes": 30, "jitter_minutes": 5, "cron": "0 */6 * * *", "timezone": "America/Sao_Paulo"}})
        assert r.status_code == 200
        prod = r.get_json()["product"]
        assert prod["schedule"]["enabled"] is True
        assert prod["schedule"]["interval_minutes"] == 30

        # status agora tem next_run
        r = client.get("/api/schedules/status")
        j = r.get_json()
        sched_a = next(s for s in j["schedules"] if s["product_id"] == "produto-a")
        assert sched_a["enabled"] is True
        assert sched_a["next_run"] is not None
        assert sched_a["interval_minutes"] == 30
        assert sched_a["jitter_minutes"] == 5
        # log contém agendado
        # verifica que realmente há job no scheduler
        app_mod = isolated_olx["app"]
        sched = app_mod._get_scheduler()
        jobs = sched.get_jobs()
        assert any(job.id == "produto-a" for job in jobs)

    def test_disable_removes_job(self, isolated_olx, client):
        _create_product(client, {"name": "Produto B", "id": "produto-b", "queries": ["teste"], "pages": 1, "max_price": 100, "schedule": {"enabled": True, "mode": "interval", "interval_minutes": 30}})
        r = client.get("/api/schedules/status")
        assert any(s["product_id"] == "produto-b" and s["next_run"] is not None for s in r.get_json()["schedules"])
        # desabilita
        r = client.put("/api/products/produto-b", json={"schedule": {"enabled": False, "mode": "interval", "interval_minutes": 30}})
        assert r.status_code == 200
        r = client.get("/api/schedules/status")
        sched_b = next(s for s in r.get_json()["schedules"] if s["product_id"] == "produto-b")
        assert sched_b["enabled"] is False
        assert sched_b["next_run"] is None
        # job removido
        app_mod = isolated_olx["app"]
        assert not any(job.id == "produto-b" for job in app_mod._get_scheduler().get_jobs())

    def test_cron_mode_creates_cron_job(self, isolated_olx, client):
        _create_product(client, {"name": "Produto C", "id": "produto-c", "queries": ["teste"], "pages": 1, "max_price": 100, "schedule": {"enabled": False, "mode": "interval", "interval_minutes": 360}})
        r = client.put("/api/products/produto-c", json={"schedule": {"enabled": True, "mode": "cron", "cron": "0 9,19 * * *", "jitter_minutes": 0}})
        assert r.status_code == 200
        r = client.get("/api/schedules/status")
        sched_c = next(s for s in r.get_json()["schedules"] if s["product_id"] == "produto-c")
        assert sched_c["mode"] == "cron"
        assert sched_c["cron"] == "0 9,19 * * *"
        assert sched_c["next_run"] is not None
        # cron deve apontar para 9h ou 19h BRT; verifica que hour está em 9 ou 19 (parse ISO)
        dt = datetime.fromisoformat(sched_c["next_run"])
        # dt pode ser aware; converte para BRT já é
        assert dt.hour in (9, 19)

    def test_invalid_interval_rejected_400(self, isolated_olx, client):
        _create_product(client, {"name": "Produto D", "id": "produto-d", "queries": ["teste"], "pages": 1, "max_price": 100, "schedule": {"enabled": False, "mode": "interval", "interval_minutes": 360}})
        r = client.put("/api/products/produto-d", json={"schedule": {"enabled": True, "mode": "interval", "interval_minutes": 10}})
        assert r.status_code == 400
        j = r.get_json()
        assert any("15" in e for e in j["errors"])
        # garante que não criou job (ainda disabled)
        r = client.get("/api/schedules/status")
        sched_d = next(s for s in r.get_json()["schedules"] if s["product_id"] == "produto-d")
        assert sched_d["enabled"] is False  # permanece falso, não persistiu inválido

    def test_invalid_cron_rejected(self, isolated_olx, client):
        _create_product(client, {"name": "Produto E", "id": "produto-e", "queries": ["teste"], "pages": 1, "max_price": 100, "schedule": {"enabled": False, "mode": "interval", "interval_minutes": 360}})
        r = client.put("/api/products/produto-e", json={"schedule": {"enabled": True, "mode": "cron", "cron": "invalido"}})
        assert r.status_code == 400
        assert any("5 campos" in e for e in r.get_json()["errors"])

    def test_jitter_validation(self, isolated_olx, client):
        _create_product(client, {"name": "Produto F", "id": "produto-f", "queries": ["teste"], "pages": 1, "max_price": 100, "schedule": {"enabled": False, "mode": "interval", "interval_minutes": 360}})
        r = client.put("/api/products/produto-f", json={"schedule": {"enabled": True, "mode": "interval", "interval_minutes": 30, "jitter_minutes": 99}})
        assert r.status_code == 400
        assert any("jitter" in e for e in r.get_json()["errors"])


# ---------------------------------------------------------------------------
# Regras de negócio: produto desabilitado não agenda
# ---------------------------------------------------------------------------

class TestProductDisabledNotScheduled:
    def test_product_disabled_even_if_schedule_enabled_no_job(self, isolated_olx, client):
        _create_product(client, {"name": "Produto G", "id": "produto-g", "queries": ["teste"], "pages": 1, "max_price": 100, "enabled": True, "schedule": {"enabled": True, "mode": "interval", "interval_minutes": 30}})
        r = client.get("/api/schedules/status")
        assert any(s["product_id"] == "produto-g" and s["next_run"] is not None for s in r.get_json()["schedules"])
        # desabilita produto (sem tocar schedule) — simula botão Ativar/Desativar index.html:730
        r = client.put("/api/products/produto-g", json={"enabled": False})
        assert r.status_code == 200
        # schedule continua enabled no disco mas scheduler deve ignorar
        prod = r.get_json()["product"]
        assert prod["schedule"]["enabled"] is True
        assert prod["enabled"] is False
        r = client.get("/api/schedules/status")
        sched_g = next(s for s in r.get_json()["schedules"] if s["product_id"] == "produto-g")
        assert sched_g["product_enabled"] is False
        assert sched_g["enabled"] is True  # schedule enabled
        assert sched_g["next_run"] is None  # mas sem job
        app_mod = isolated_olx["app"]
        assert not any(job.id == "produto-g" for job in app_mod._get_scheduler().get_jobs())
        # reabilita deve voltar a agendar
        r = client.put("/api/products/produto-g", json={"enabled": True})
        assert r.status_code == 200
        r = client.get("/api/schedules/status")
        sched_g = next(s for s in r.get_json()["schedules"] if s["product_id"] == "produto-g")
        assert sched_g["next_run"] is not None


# ---------------------------------------------------------------------------
# Persistência e _get_next_run_for_interval
# ---------------------------------------------------------------------------

class TestPersistenceAndRestart:
    def test_next_run_uses_last_run_after_restart(self, isolated_olx, client):
        scraper = isolated_olx["olx_scraper"]
        app_mod = isolated_olx["app"]
        # cria produto interval 360
        _create_product(client, {"name": "Produto H", "id": "produto-h", "queries": ["teste"], "pages": 1, "max_price": 100, "schedule": {"enabled": True, "mode": "interval", "interval_minutes": 360, "jitter_minutes": 0}})
        # simula que rodou agora e gravou last_run
        now = datetime.now(timezone.utc)
        scraper.record_schedule_run("produto-h", status="ok", at_iso=now.isoformat(), matched_count=5, results_count=10)
        # força reschedule como se fosse reboot (remove jobs e recria)
        app_mod.reschedule_all()
        r = client.get("/api/schedules/status")
        sched_h = next(s for s in r.get_json()["schedules"] if s["product_id"] == "produto-h")
        # next_run deve ser last_run + 360m
        expected = now + timedelta(minutes=360)
        actual = datetime.fromisoformat(sched_h["next_run"])
        # compara com tolerância 2s (jitter 0)
        assert abs((actual - expected).total_seconds()) < 5
        # last_run visível no status
        assert sched_h["last_run"] == now.isoformat()
        assert sched_h["last_status"] == "ok"
        assert sched_h["last_matched"] == 5

    def test_toggle_without_schedule_preserves(self, isolated_olx, client):
        # cria com schedule 30m
        _create_product(client, {"name": "Produto I", "id": "produto-i", "queries": ["teste"], "pages": 1, "max_price": 100, "schedule": {"enabled": True, "mode": "interval", "interval_minutes": 30, "jitter_minutes": 5}})
        # desabilita via enabled false sem enviar schedule (como toggle)
        r = client.put("/api/products/produto-i", json={"enabled": False})
        assert r.status_code == 200
        prod = r.get_json()["product"]
        assert prod["schedule"]["interval_minutes"] == 30
        assert prod["schedule"]["jitter_minutes"] == 5
        # reabilita
        r = client.put("/api/products/produto-i", json={"enabled": True})
        prod = r.get_json()["product"]
        assert prod["schedule"]["interval_minutes"] == 30


# ---------------------------------------------------------------------------
# Fila e concorrência (409 -> queue) — integração leve
# ---------------------------------------------------------------------------

class TestQueue:
    def test_trigger_enqueues_when_busy(self, isolated_olx, client, monkeypatch):
        app_mod = isolated_olx["app"]
        # simula job running
        with app_mod.job_lock:
            app_mod.job["running"] = True
        # trigger sem queue deve 409
        r = client.post("/api/schedules/trigger/produto-a", json={})
        # produto-a não existe, cria primeiro
        _create_product(client, {"name": "Produto A", "id": "produto-a", "queries": ["teste"], "pages": 1, "max_price": 100, "schedule": {"enabled": True, "mode": "interval", "interval_minutes": 360}})
        r = client.post("/api/schedules/trigger/produto-a", json={})
        assert r.status_code == 409
        # com queue deve enfileirar 202
        r = client.post("/api/schedules/trigger/produto-a?queue=1", json={})
        assert r.status_code == 202
        assert r.get_json()["queued"] is True
        # limpa
        with app_mod.job_lock:
            app_mod.job["running"] = False
            app_mod.schedule_queue.clear()
