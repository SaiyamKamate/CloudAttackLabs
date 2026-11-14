"""Integration test: run authentication_abuse against the real lab, then assert
the collected events and the raised alert.

Opt-in (needs Docker and port 8081):

    pytest -m integration
"""
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import get_session  # noqa: E402
from app.lab_manager.router import get_adapter, get_target_clients  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402

pytestmark = pytest.mark.integration

API = "/api/v1"
TERMINAL = {"completed", "failed", "cleaned_up"}


@pytest.fixture
def client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'tele.db'}", connect_args={"check_same_thread": False}, poolclass=NullPool
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
    get_adapter.cache_clear()
    get_target_clients.cache_clear()
    adapter = get_adapter()
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


def test_run_produces_events_and_one_alert(client):
    lab = client.post(f"{API}/labs", json={"name": "tele", "target": "shopcloud"}).json()
    assert client.post(f"{API}/labs/{lab['id']}/start").status_code == 200

    run = client.post(f"{API}/labs/{lab['id']}/runs", json={"scenario_id": "authentication_abuse"}).json()
    settled = wait_terminal(client, run["id"])
    assert settled["status"] == "completed", settled

    # 20 authentication_failure events were collected for this run.
    failures = client.get(
        f"{API}/events", params={"run_id": run["id"], "event_type": "authentication_failure", "limit": 1000}
    ).json()
    assert len(failures) == 20, [e["message"] for e in failures]

    # Exactly one alert, and it links its contributing events.
    alerts = client.get(f"{API}/alerts", params={"run_id": run["id"]}).json()
    assert len(alerts) == 1
    assert alerts[0]["rule_id"] == "repeated_failed_logins"

    detail = client.get(f"{API}/alerts/{alerts[0]['id']}").json()
    assert detail["event_count"] == 20
    assert len(detail["events"]) == 20
    assert {e["event_type"] for e in detail["events"]} == {"authentication_failure"}

    # Cleanup, then stop.
    assert client.post(f"{API}/runs/{run['id']}/cleanup").status_code == 200
    assert client.post(f"{API}/labs/{lab['id']}/stop").status_code == 200
