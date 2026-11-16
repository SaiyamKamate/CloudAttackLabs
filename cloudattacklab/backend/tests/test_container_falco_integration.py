"""Integration test: suspicious_container_activity against the real lab with Falco on.

Runs the scenario, checks the alert holds the Docker exec event and Falco's
alert (with its rule name), requests and approves stop_container, checks it is
verified, runs cleanup and checks the test container is back.

Opt-in (needs Docker, port 8081, and a kernel where Falco's modern eBPF engine
loads in a privileged container):

    pytest -m integration backend/tests/test_container_falco_integration.py

It is skipped, with the reason, when Falco cannot run here.
"""
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.db import get_session  # noqa: E402
from app.lab_manager.adapter import LabAdapterError  # noqa: E402
from app.lab_manager.docker_adapter import DockerComposeAdapter  # noqa: E402
from app.lab_manager.router import get_adapter, get_target_clients  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402

pytestmark = pytest.mark.integration

API = "/api/v1"
TERMINAL = {"completed", "failed", "cleaned_up"}
FALCO_RULE = "CloudAttackLab marker process in test container"


def falco_unavailable_reason(adapter: DockerComposeAdapter) -> str | None:
    """Start the lab with Falco and wait for Falco to be healthy; return why not, if it is not."""
    try:
        adapter.start()
    except LabAdapterError as exc:
        return f"the lab with Falco did not start: {exc}"
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        falco = next((c for c in adapter.status() if c.service == "falco"), None)
        if falco is not None and falco.state == "running" and falco.health == "healthy":
            return None
        if falco is not None and falco.state in ("exited", "dead"):
            tail = " | ".join(adapter.logs("falco").strip().splitlines()[-3:])
            return f"the Falco container exited ({tail})"
        time.sleep(2)
    return "Falco did not become healthy within 120 s"


@pytest.fixture
def client(tmp_path):
    settings = replace(get_settings(), falco_enabled=True)
    adapter = DockerComposeAdapter(
        settings.lab_compose_file,
        docker_bin=settings.docker_bin,
        lab_control_token=settings.lab_control_token,
        start_timeout_seconds=settings.lab_start_timeout_seconds,
        falco_enabled=True,
    )
    reason = falco_unavailable_reason(adapter)
    if reason is not None:
        adapter.stop()
        pytest.skip(f"Falco is not available on this machine: {reason}")

    engine = create_engine(
        f"sqlite:///{tmp_path / 'falco.db'}", connect_args={"check_same_thread": False}, poolclass=NullPool
    )
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA busy_timeout=5000")

    app = create_app()

    def _session():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_adapter] = lambda: adapter
    get_target_clients.cache_clear()
    try:
        with TestClient(app) as client:
            yield client
    finally:
        adapter.stop()
        engine.dispose()


def wait_terminal(client, run_id, timeout=90.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"{API}/runs/{run_id}").json()
        if body["status"] in TERMINAL:
            return body
        time.sleep(0.5)
    raise AssertionError(f"run {run_id} did not settle: {body}")


def test_container_scenario_with_falco_stop_container_and_cleanup(client):
    lab = client.post(f"{API}/labs", json={"name": "falco", "target": "shopcloud"}).json()
    assert client.post(f"{API}/labs/{lab['id']}/start").status_code == 200
    detail = client.get(f"{API}/labs/{lab['id']}").json()
    assert detail["falco"]["healthy"] is True, detail["falco"]
    before = next(c for c in detail["containers"] if c["service"] == "test_container")

    run = client.post(f"{API}/labs/{lab['id']}/runs", json={"scenario_id": "suspicious_container_activity"}).json()
    settled = wait_terminal(client, run["id"])
    assert settled["status"] == "completed", settled
    assert settled["summary"]["command"] == "sh -c id; : cloudattacklab-scenario-marker"
    assert settled["summary"]["output"].startswith("uid=0(root)")

    # One alert holding the Docker exec event and Falco's alert, for one container.
    alerts = client.get(f"{API}/alerts", params={"run_id": run["id"]}).json()
    assert [a["rule_id"] for a in alerts] == ["suspicious_container_process"]
    events = client.get(f"{API}/alerts/{alerts[0]['id']}").json()["events"]
    sources = {e["source"] for e in events}
    assert sources == {"docker", "falco"}, events
    falco = [e for e in events if e["source"] == "falco"]
    assert FALCO_RULE in {e["rule_id"] for e in falco}
    assert {e["container_id"] for e in events} == {before["container_id"][:12]}
    assert all(e["raw_event_reference"].startswith(("docker:test_container:", "docker:falco:")) for e in events)

    incidents = [i for i in client.get(f"{API}/incidents").json() if i["run_id"] == run["id"]]
    assert len(incidents) == 1

    # stop_container: request, approve, verified.
    requested = client.post(f"{API}/incidents/{incidents[0]['id']}/responses").json()
    assert requested["action"] == "stop_container", requested
    approved = client.post(f"{API}/responses/{requested['id']}/approve").json()
    assert approved["status"] == "verified", approved
    assert approved["verification_result"]["received"] == {"running": False}
    stopped = next(c for c in client.get(f"{API}/labs/{lab['id']}").json()["containers"] if c["service"] == "test_container")
    assert stopped["state"] == "exited"

    # Cleanup recreates the test container: running again, a new container.
    cleaned = client.post(f"{API}/runs/{run['id']}/cleanup").json()
    assert cleaned["status"] == "cleaned_up"
    after = next(c for c in client.get(f"{API}/labs/{lab['id']}").json()["containers"] if c["service"] == "test_container")
    assert after["state"] == "running"
    assert after["container_id"] != before["container_id"]
    assert client.get(f"{API}/responses/{requested['id']}").json()["status"] == "reverted"

    # ShopCloud is untouched by all of this.
    shop = next(c for c in client.get(f"{API}/labs/{lab['id']}").json()["containers"] if c["service"] == "shopcloud")
    assert shop["state"] == "running"
    assert client.post(f"{API}/labs/{lab['id']}/stop").status_code == 200
