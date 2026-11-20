"""Runs, responses and cleanup against a registered (external) ShopCloud, end to end.

The real ShopCloud app runs in-process with the integration on. Every request
CloudAttackLab makes to it (handshake, event feed, health, login, rate limit,
reset) goes through the ASGI test client, so the whole path is real except the
network socket. Only the scenario script's subprocess is replaced: it sends its
failed logins to the same ShopCloud.
"""
import dataclasses
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db import get_session
from app.lab_manager.router import get_adapter, get_target_clients
from app.main import create_app
from app.models import Alert, AuditLog, Base, Event, Incident, TargetAuthorization
from app.scenario_engine import service as service_module
from app.scenario_engine.runner import RunResult
from app.targets.integration_client import IntegrationClient
from app.targets.resolve import get_client_factory
from app.targets.service import make_rechecker, pinned_of

from conftest import FakeAdapter, FakeTarget

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shopcloud"))
from shop.main import create_app as create_shop  # noqa: E402

API = "/api/v1"
KEY = "integration-key-for-tests-0123456789abcdef"
TOKEN = "control-token-for-tests"
URL = "http://127.0.0.1:8090"
TERMINAL = {"completed", "incomplete", "failed", "cleaned_up"}


@pytest.fixture(autouse=True)
def _clear_active_labs():
    service_module._active_labs.clear()
    yield
    service_module._active_labs.clear()


@pytest.fixture
def file_engine(tmp_path):
    """File-backed SQLite so the background run thread gets its own connection."""
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
def shop(tmp_path):
    app = create_shop(db_path=str(tmp_path / "shop.db"), lab_token=TOKEN, integration_key=KEY, instance_id="shop-1")
    return TestClient(app, client=("172.23.0.1", 40000))


@pytest.fixture
def script_calls(monkeypatch, shop):
    """Replace the script subprocess: send `attempts` wrong-password logins to the shop."""
    calls = []

    def fake_execute(script_path, base_url, parameters, timeout_seconds):
        calls.append({"script": Path(script_path).name, "base_url": base_url})
        statuses = [
            shop.post("/api/login", json={"username": parameters["username"], "password": "wrong"}).status_code
            for _ in range(min(int(parameters["attempts"]), 12))
        ]
        summary = {"attempts_sent": len(statuses), "unauthorized_401": statuses.count(401), "errors": 0}
        return RunResult(outcome="completed", summary=summary, error=None, exit_code=0)

    monkeypatch.setattr(service_module.runner, "execute_script", fake_execute)
    return calls


@pytest.fixture
def api(file_engine, shop, script_calls):
    adapter = FakeAdapter()
    app = create_app()
    fast = dataclasses.replace(
        get_settings(),
        telemetry_collect_interval_seconds=0.1,
        loki_settle_interval_seconds=0.1,
        loki_settle_timeout_seconds=3,
    )

    def _session():
        with Session(file_engine, expire_on_commit=False) as session:
            yield session

    def factory(target, settings):
        return IntegrationClient(pinned_of(target), target.integration_key, recheck=make_rechecker(settings), http=shop)

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: fast
    app.dependency_overrides[get_adapter] = lambda: adapter
    app.dependency_overrides[get_target_clients] = lambda: {"shopcloud": FakeTarget(adapter)}
    app.dependency_overrides[get_client_factory] = lambda: factory
    with TestClient(app) as client:
        client.adapter = adapter
        yield client


@pytest.fixture
def db(file_engine):
    with Session(file_engine) as session:
        yield session


def ready_target(api, *, authorize=True, max_runs=3):
    target = api.post(f"{API}/targets", json={
        "name": "shop-demo", "kind": "shopcloud", "base_url": URL, "integration_key": KEY, "control_token": TOKEN,
    }).json()
    assert api.post(f"{API}/targets/{target['id']}/verify").status_code == 200
    assert api.post(f"{API}/targets/{target['id']}/verify-telemetry").status_code == 200
    if authorize:
        response = api.post(f"{API}/targets/{target['id']}/authorizations", json={
            "scenario_ids": ["authentication_abuse"], "duration_minutes": 30, "authorized_by": "Sam",
            "acknowledgement": True, "max_runs": max_runs,
        })
        assert response.status_code == 201, response.text
        target["authorization"] = response.json()
    return target


def start_run(api, lab_id, scenario_id="authentication_abuse"):
    return api.post(f"{API}/labs/{lab_id}/runs", json={"scenario_id": scenario_id})


def wait_terminal(api, run_id, timeout=20.0):
    deadline = time.monotonic() + timeout
    body = None
    while time.monotonic() < deadline:
        body = api.get(f"{API}/runs/{run_id}").json()
        if body["status"] in TERMINAL:
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish: {body}")


def rate_limit(shop):
    return shop.get("/lab/rate-limit", headers={"X-Lab-Token": TOKEN}).json()


# --- the demonstration, end to end ------------------------------------------------
def test_authentication_abuse_against_a_registered_shopcloud(api, db, shop, script_calls):
    target = ready_target(api)
    run = start_run(api, target["lab_id"])
    assert run.status_code == 201, run.text
    finished = wait_terminal(api, run.json()["id"])
    assert finished["status"] == "completed", finished
    run_id = finished["id"]

    # The script got the registered URL, never anything from the request.
    assert script_calls == [{"script": "authentication_abuse.py", "base_url": URL}]

    # Real ShopCloud records, collected from its signed feed.
    events = list(db.scalars(select(Event).where(Event.run_id == __import__("uuid").UUID(run_id))))
    failures = [e for e in events if e.event_type == "authentication_failure"]
    assert len(failures) == 12
    assert all(e.raw_event_reference.startswith("integration:shopcloud:") for e in failures)
    assert all(e.raw["instance_id"] == "shop-1" and e.container_id is None for e in failures)

    # A real alert and incident, by the unchanged detection engine.
    alerts = api.get(f"{API}/alerts", params={"run_id": run_id}).json()
    assert [a["rule_id"] for a in alerts] == ["repeated_failed_logins"]
    incidents = api.get(f"{API}/incidents", params={"run_id": run_id}).json()
    assert len(incidents) == 1

    # The authorization was spent and the run start says which one.
    auth = db.get(TargetAuthorization, __import__("uuid").UUID(target["authorization"]["id"]))
    assert auth.runs_used == 1
    start = db.scalars(select(AuditLog).where(AuditLog.action == "run.start")).one()
    assert start.detail["authorization_id"] == target["authorization"]["id"]
    assert start.detail["base_url"] == URL and start.detail["instance_id"] == "shop-1"

    # The response goes to the same ShopCloud with its own control token, and is verified there.
    response = api.post(f"{API}/incidents/{incidents[0]['id']}/responses").json()
    approved = api.post(f"{API}/responses/{response['id']}/approve").json()
    assert approved["status"] == "verified", approved
    assert rate_limit(shop)["enabled"] is True

    # Cleanup restores ShopCloud's baseline.
    cleaned = api.post(f"{API}/runs/{run_id}/cleanup").json()
    assert cleaned["status"] == "cleaned_up"
    assert rate_limit(shop)["enabled"] is False

    # Docker was never asked to do anything for the external lab.
    assert not {"start", "stop", "reset", "recreate"} & set(api.adapter.calls)


# --- authorization is required, scoped and spent ------------------------------------
def test_no_run_without_an_authorization(api, script_calls):
    target = ready_target(api, authorize=False)
    response = start_run(api, target["lab_id"])
    assert response.status_code == 409
    assert response.json()["error"] == "not_authorized"
    assert script_calls == []


def test_no_run_after_revocation(api, script_calls):
    target = ready_target(api)
    api.post(f"{API}/targets/{target['id']}/authorizations/{target['authorization']['id']}/revoke")
    assert start_run(api, target["lab_id"]).json()["error"] == "not_authorized"
    assert script_calls == []


def test_the_run_budget_is_enforced(api, script_calls):
    target = ready_target(api, max_runs=1)
    first = start_run(api, target["lab_id"])
    wait_terminal(api, first.json()["id"])
    assert start_run(api, target["lab_id"]).json()["error"] == "not_authorized"
    assert len(script_calls) == 1


def test_only_application_scenarios_run_against_a_registered_target(api):
    target = ready_target(api)
    response = start_run(api, target["lab_id"], "suspicious_container_activity")
    assert response.json()["error"] == "scenario_not_supported"


def test_a_failed_handshake_stops_the_run_before_any_traffic(api, db, script_calls):
    target = ready_target(api)
    from app.models import Target
    row = db.get(Target, __import__("uuid").UUID(target["id"]))
    row.integration_key = "a-key-shopcloud-does-not-know-0123456789"
    db.commit()
    response = start_run(api, target["lab_id"])
    assert response.status_code == 502
    assert response.json()["error"] == "handshake_failed"
    assert script_calls == []
    assert api.get(f"{API}/labs/{target['lab_id']}").json()["status"] == "error"


def test_a_tightened_url_policy_stops_runs(api, file_engine, script_calls):
    target = ready_target(api)
    strict = dataclasses.replace(api.app.dependency_overrides[get_settings](), target_allowed_ports=(9000,))
    api.app.dependency_overrides[get_settings] = lambda: strict
    response = start_run(api, target["lab_id"])
    assert response.json()["error"] == "target_url_refused"
    assert script_calls == []


def test_response_approval_needs_a_live_authorization(api, db, shop):
    target = ready_target(api)
    run = wait_terminal(api, start_run(api, target["lab_id"]).json()["id"])
    incident = api.get(f"{API}/incidents", params={"run_id": run["id"]}).json()[0]
    response = api.post(f"{API}/incidents/{incident['id']}/responses").json()
    api.post(f"{API}/targets/{target['id']}/authorizations/{target['authorization']['id']}/revoke")
    refused = api.post(f"{API}/responses/{response['id']}/approve")
    assert refused.status_code == 409
    assert refused.json()["error"] == "not_authorized"
    assert rate_limit(shop)["enabled"] is False  # nothing was sent


def test_the_docker_lab_still_runs_as_before(api, script_calls):
    ready_target(api)
    lab = api.post(f"{API}/labs", json={"name": "docker-lab", "target": "shopcloud"}).json()
    assert api.post(f"{API}/labs/{lab['id']}/start").status_code == 200
    run = start_run(api, lab["id"])
    assert run.status_code == 201, run.text
    wait_terminal(api, run.json()["id"])
    assert script_calls[-1]["base_url"] == "http://127.0.0.1:8081"
