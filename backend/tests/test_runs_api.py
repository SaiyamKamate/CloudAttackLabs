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
TERMINAL = {"completed", "incomplete", "failed", "cleaned_up"}


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


def login_failures(count=20):
    """ShopCloud log text with `count` login_failed records, stamped just after now,
    so they fall inside the run that starts next."""
    import json
    import uuid
    from datetime import datetime, timedelta, timezone

    base = datetime.now(timezone.utc) + timedelta(milliseconds=500)
    return "\n".join(
        json.dumps(
            {
                "timestamp": (base + timedelta(milliseconds=10 * i)).isoformat().replace("+00:00", "Z"),
                "service": "shopcloud", "level": "warning", "event": "login_failed",
                "request_id": str(uuid.uuid4()), "source_ip": "172.21.0.1", "method": "POST",
                "path": "/api/login", "status": 401, "username": "lab_user",
                "message": "Invalid username or password",
            }
        )
        for i in range(count)
    )


def patch_script(monkeypatch, result: RunResult, record=None):
    def fake_execute(script_path, base_url, parameters, timeout_seconds):
        if record is not None:
            record.append({"base_url": base_url, "parameters": parameters, "timeout": timeout_seconds})
        return result

    monkeypatch.setattr(service_module.runner, "execute_script", fake_execute)


# --- scenarios ------------------------------------------------------------
def test_list_scenarios(client):
    body = client.get(f"{API}/scenarios").json()
    assert [s["id"] for s in body] == [
        "authentication_abuse",
        "benign_baseline",
        "suspicious_container_activity",
        "unauthorized_resource_access",
    ]
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
def test_run_completes_and_stores_the_summary(client, rdb, adapter, monkeypatch):
    calls = []
    patch_script(monkeypatch, RunResult("completed", fake_summary(), None), record=calls)
    lab = running_lab(client)
    adapter.log_text = login_failures(20)  # the expected telemetry is there

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


# --- audit bug 3: one thing at a time ---------------------------------------
@pytest.fixture
def blocking_script(monkeypatch):
    """A script that runs until the test releases it, so other requests overlap the run."""
    import threading

    gate, started = threading.Event(), threading.Event()
    monkeypatch.setattr(service_module.runner, "execute_script", _blocking(gate, started))
    yield gate, started
    gate.set()


def _finished_run(client, lab_id, monkeypatch):
    patch_script(monkeypatch, RunResult("completed", fake_summary(), None))
    run = start_run(client, lab_id).json()
    return wait_terminal(client, run["id"])


def test_cleanup_of_another_run_is_refused_while_a_run_is_active(client, adapter, target, monkeypatch):
    import threading

    lab = running_lab(client)
    old = _finished_run(client, lab["id"], monkeypatch)  # an earlier, finished run
    gate, started = threading.Event(), threading.Event()
    monkeypatch.setattr(service_module.runner, "execute_script", _blocking(gate, started))

    active = start_run(client, lab["id"]).json()
    assert started.wait(5)
    resets_before, limits_before = target.reset_calls, len(target.rate_limit_calls)

    response = client.post(f"{API}/runs/{old['id']}/cleanup")

    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "run_in_progress"
    assert active["id"] in body["detail"] and "Cleanup of run" in body["detail"]
    # Nothing was done to the target.
    assert target.reset_calls == resets_before and len(target.rate_limit_calls) == limits_before
    # The lab says which run blocks it (the dashboard disables the buttons from this).
    detail = client.get(f"{API}/labs/{lab['id']}").json()
    assert detail["active_run"]["id"] == active["id"]

    gate.set()
    wait_terminal(client, active["id"])
    assert client.post(f"{API}/runs/{old['id']}/cleanup").status_code == 200
    assert client.get(f"{API}/labs/{lab['id']}").json()["active_run"] is None


def test_lab_reset_is_refused_while_a_run_is_active(client, adapter, target, blocking_script):
    gate, started = blocking_script
    lab = running_lab(client)
    active = start_run(client, lab["id"]).json()
    assert started.wait(5)

    response = client.post(f"{API}/labs/{lab['id']}/reset")

    assert response.status_code == 409
    assert response.json()["error"] == "run_in_progress"
    assert "Lab reset is refused" in response.json()["detail"]
    assert "reset" not in adapter.calls and target.reset_calls == 0

    gate.set()
    wait_terminal(client, active["id"])
    assert client.post(f"{API}/labs/{lab['id']}/reset").status_code == 200


def _blocking(gate, started):
    def blocking_execute(script_path, base_url, parameters, timeout_seconds):
        started.set()
        gate.wait(timeout=10)
        return RunResult("completed", fake_summary(), None)

    return blocking_execute


# --- audit bug 4: honest run status -----------------------------------------
def test_run_without_its_expected_telemetry_is_incomplete(client, rdb, monkeypatch):
    # The audit's out-of-lab case: the script "succeeded" but no event reached the lab's logs.
    patch_script(monkeypatch, RunResult("completed", fake_summary(), None))
    lab = running_lab(client)

    run = wait_terminal(client, start_run(client, lab["id"]).json()["id"])

    assert run["status"] == "incomplete"
    assert "expected at least 10 application authentication_failure event(s), collected 0" in run["error"]
    report = client.get(f"{API}/runs/{run['id']}/report").json()
    assert report["run"]["status"] == "incomplete"
    assert report["run"]["error"] == run["error"]
    assert report["telemetry"]["all_expected_met"] is False
    finish = rdb.scalars(select(AuditLog).where(AuditLog.action == "run.finish")).one()
    assert finish.detail["outcome"] == "incomplete" and finish.detail["error"] == run["error"]


def test_run_whose_script_reported_request_errors_is_incomplete(client, adapter, monkeypatch):
    # The audit's reset-during-run case: 5 requests got no answer, but enough events arrived.
    patch_script(monkeypatch, RunResult("completed", fake_summary(unauthorized_401=15, errors=5), None))
    lab = running_lab(client)
    adapter.log_text = login_failures(15)

    run = wait_terminal(client, start_run(client, lab["id"]).json()["id"])

    assert run["status"] == "incomplete"
    assert run["error"] == (
        "Run incomplete: the script reported 5 request error(s) (requests the target never answered)."
    )
    assert client.get(f"{API}/runs/{run['id']}/report").json()["run"]["error"] == run["error"]


def test_run_with_its_evidence_and_no_errors_is_completed(client, adapter, monkeypatch):
    patch_script(monkeypatch, RunResult("completed", fake_summary(errors=0), None))
    lab = running_lab(client)
    adapter.log_text = login_failures(20)

    run = wait_terminal(client, start_run(client, lab["id"]).json()["id"])

    assert run["status"] == "completed" and run["error"] is None


# --- audit bug 7: a run's window and platform bookkeeping --------------------
def _record(event, status, at, path="/api/login"):
    import json
    import uuid

    return json.dumps(
        {
            "timestamp": at.isoformat().replace("+00:00", "Z"), "service": "shopcloud", "level": "info",
            "event": event, "request_id": str(uuid.uuid4()), "source_ip": "172.21.0.1", "method": "POST",
            "path": path, "status": status, "username": "lab_user", "message": event,
        }
    )


def test_run_keeps_only_events_inside_its_window_and_no_bookkeeping(client, rdb, adapter, monkeypatch):
    from datetime import datetime, timedelta, timezone

    import uuid

    from app.models import Event

    patch_script(monkeypatch, RunResult("completed", fake_summary(), None))
    lab = running_lab(client)
    now = datetime.now(timezone.utc)
    adapter.log_text = "\n".join(
        [_record("login_failed", 401, now - timedelta(seconds=2))]                  # before the run: not ours
        + [_record("login_failed", 401, now + timedelta(milliseconds=300 + i)) for i in range(20)]
        + [_record("lab_reset", 200, now + timedelta(milliseconds=400), "/lab/reset")]          # bookkeeping
        + [_record("rate_limit_changed", 200, now + timedelta(milliseconds=450), "/lab/rate-limit")]
        + [_record("login_failed", 401, now + timedelta(seconds=60))]               # long after it finished
    )

    run = wait_terminal(client, start_run(client, lab["id"]).json()["id"])

    events = list(rdb.scalars(select(Event).where(Event.run_id == uuid.UUID(run["id"]))))
    assert run["status"] == "completed"
    assert len(events) == 20
    assert {e.event_type for e in events} == {"authentication_failure"}  # no config_changed
    report = client.get(f"{API}/runs/{run['id']}/report").json()
    assert report["telemetry"]["collected_total"] == 20
    assert all(a["event_count"] <= 20 for a in report["alerts"])
