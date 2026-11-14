"""Unit tests for the Scenarios and runs endpoints, using the fake adapter and
fake target and a monkeypatched script runner (no real subprocess)."""
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import get_session  # noqa: E402
from app.lab_manager.router import get_adapter, get_target_clients  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import AuditLog, Base, ScenarioRun  # noqa: E402
from app.scenario_engine import service as service_module  # noqa: E402
from app.scenario_engine.runner import RunResult  # noqa: E402

from conftest import FakeAdapter, FakeTarget  # noqa: E402

API = "/api/v1"
TERMINAL = {"completed", "failed", "cleaned_up"}


@pytest.fixture(autouse=True)
def _clear_active_labs():
    # The "one active run per lab" registry is module-level; keep tests independent.
    service_module._active_labs.clear()
    yield
    service_module._active_labs.clear()


@pytest.fixture
def file_engine(tmp_path):
    """A file-backed SQLite DB so the background run thread gets its own connection."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'runs.db'}", connect_args={"check_same_thread": False}, poolclass=NullPool
    )
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA busy_timeout=5000")
    yield engine
    engine.dispose()


@pytest.fixture
def adapter():
    return FakeAdapter()


@pytest.fixture
def target(adapter):
    return FakeTarget(adapter)


@pytest.fixture
def client(file_engine, adapter, target):
    app = create_app()

    def _session():
        with Session(file_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_adapter] = lambda: adapter
    app.dependency_overrides[get_target_clients] = lambda: {"shopcloud": target}
    with TestClient(app) as client:
        yield client


@pytest.fixture
def rdb(file_engine):
    with Session(file_engine) as session:
        yield session


# --- helpers --------------------------------------------------------------
def running_lab(client, name="run-lab"):
    lab = client.post(f"{API}/labs", json={"name": name, "target": "shopcloud"}).json()
    assert client.post(f"{API}/labs/{lab['id']}/start").status_code == 200
    return lab


def start_run(client, lab_id, scenario_id="authentication_abuse"):
    return client.post(f"{API}/labs/{lab_id}/runs", json={"scenario_id": scenario_id})


def wait_terminal(client, run_id, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"{API}/runs/{run_id}").json()
        if body["status"] in TERMINAL:
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not settle: last={body}")


def fake_summary(**over):
    base = {"scenario": "authentication_abuse", "attempts_sent": 20, "unauthorized_401": 20, "rate_limited_429": 0}
    base.update(over)
    return base


def patch_script(monkeypatch, result: RunResult, record=None):
    def fake_execute(script_path, base_url, parameters, timeout_seconds):
        if record is not None:
            record.append({"base_url": base_url, "parameters": parameters, "timeout": timeout_seconds})
        return result

    monkeypatch.setattr(service_module.runner, "execute_script", fake_execute)


# --- scenarios ------------------------------------------------------------
def test_list_scenarios(client):
    body = client.get(f"{API}/scenarios").json()
    assert [s["id"] for s in body] == ["authentication_abuse"]
    assert body[0]["target"] == "shopcloud"


def test_get_scenario(client):
    body = client.get(f"{API}/scenarios/authentication_abuse").json()
    assert body["id"] == "authentication_abuse"
    assert body["execution"]["parameters"]["attempts"] == 20


def test_get_unknown_scenario_is_404(client):
    response = client.get(f"{API}/scenarios/does_not_exist")
    assert response.status_code == 404
    assert response.json()["error"] == "scenario_not_found"


# --- starting a run -------------------------------------------------------
def test_run_completes_and_stores_the_summary(client, rdb, monkeypatch):
    calls = []
    patch_script(monkeypatch, RunResult("completed", fake_summary(), None), record=calls)
    lab = running_lab(client)

    response = start_run(client, lab["id"])
    assert response.status_code == 201
    run = response.json()
    assert run["status"] in ("pending", "running")
    assert run["parameters"]["attempts"] == 20

    settled = wait_terminal(client, run["id"])
    assert settled["status"] == "completed"
    assert settled["summary"]["unauthorized_401"] == 20
    assert settled["started_at"] and settled["finished_at"]

    # The target URL came from lab config, not the request.
    assert calls[0]["base_url"] == "http://127.0.0.1:8081"
    assert calls[0]["timeout"] == 60

    actions = [a.action for a in rdb.scalars(select(AuditLog).order_by(AuditLog.id))]
    assert "run.start" in actions and "run.finish" in actions


def test_run_refused_when_lab_is_not_running(client, monkeypatch):
    patch_script(monkeypatch, RunResult("completed", fake_summary(), None))
    lab = client.post(f"{API}/labs", json={"name": "idle", "target": "shopcloud"}).json()
    response = start_run(client, lab["id"])
    assert response.status_code == 409
    assert response.json()["error"] == "lab_not_running"


def test_run_refused_when_preflight_health_fails(client, adapter, monkeypatch):
    patch_script(monkeypatch, RunResult("completed", fake_summary(), None))
    lab = running_lab(client)
    adapter.running = False  # container down -> target health fails pre-flight
    response = start_run(client, lab["id"])
    assert response.status_code == 409
    assert response.json()["error"] == "preflight_failed"
    assert "http_health" in response.json()["detail"]


def test_run_refused_when_test_account_fails(client, target, monkeypatch):
    patch_script(monkeypatch, RunResult("completed", fake_summary(), None))
    lab = running_lab(client)
    target.fail_login = True
    response = start_run(client, lab["id"])
    assert response.status_code == 409
    assert "test_account" in response.json()["detail"]


def test_unknown_scenario_run_is_404(client):
    lab = running_lab(client)
    response = start_run(client, lab["id"], scenario_id="nope")
    assert response.status_code == 404


def test_only_one_run_per_lab_at_a_time(client, monkeypatch):
    # A run that blocks until released, so the second request overlaps it.
    import threading

    gate = threading.Event()

    def blocking_execute(script_path, base_url, parameters, timeout_seconds):
        gate.wait(timeout=10)
        return RunResult("completed", fake_summary(), None)

    monkeypatch.setattr(service_module.runner, "execute_script", blocking_execute)
    lab = running_lab(client)

    first = start_run(client, lab["id"])
    assert first.status_code == 201
    second = start_run(client, lab["id"])
    assert second.status_code == 409
    assert second.json()["error"] == "run_in_progress"

    gate.set()
    wait_terminal(client, first.json()["id"])


# --- failure triggers cleanup --------------------------------------------
def test_failed_run_triggers_cleanup(client, rdb, target, monkeypatch):
    patch_script(monkeypatch, RunResult("failed", None, "script exited with code 1"))
    lab = running_lab(client)

    run = start_run(client, lab["id"]).json()
    settled = wait_terminal(client, run["id"])
    assert settled["status"] == "failed"
    assert settled["error"] == "script exited with code 1"

    # Cleanup ran automatically: disable_rate_limit + reset_target_state.
    assert target.rate_limit_calls and target.rate_limit_calls[-1]["enabled"] is False
    assert target.reset_calls >= 1
    actions = [a.action for a in rdb.scalars(select(AuditLog).order_by(AuditLog.id))]
    assert "run.failure" in actions and "cleanup" in actions


def test_timeout_is_reported_as_failure_with_cleanup(client, target, monkeypatch):
    patch_script(monkeypatch, RunResult("failed", None, "script timed out after 60s"))
    lab = running_lab(client)
    run = start_run(client, lab["id"]).json()
    settled = wait_terminal(client, run["id"])
    assert settled["status"] == "failed"
    assert "timed out" in settled["error"]
    assert target.reset_calls >= 1  # cleanup ran


# --- cleanup endpoint -----------------------------------------------------
def test_cleanup_endpoint_runs_steps_and_marks_cleaned_up(client, rdb, target, monkeypatch):
    patch_script(monkeypatch, RunResult("completed", fake_summary(), None))
    lab = running_lab(client)
    run = start_run(client, lab["id"]).json()
    wait_terminal(client, run["id"])

    before = target.reset_calls
    response = client.post(f"{API}/runs/{run['id']}/cleanup")
    assert response.status_code == 200
    assert response.json()["status"] == "cleaned_up"
    assert target.reset_calls == before + 1
    assert any(c["enabled"] is False for c in target.rate_limit_calls)

    actions = [a.action for a in rdb.scalars(select(AuditLog).order_by(AuditLog.id))]
    assert actions.count("cleanup") >= 1


def test_cleanup_unknown_run_is_404(client):
    import uuid

    response = client.post(f"{API}/runs/{uuid.uuid4()}/cleanup")
    assert response.status_code == 404
