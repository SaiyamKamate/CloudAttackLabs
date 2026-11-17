"""Integration test for the whole response path against the real lab:

run authentication_abuse -> alert -> incident -> request -> approve -> verified
-> cleanup -> reverted, checking ShopCloud's own rate-limit setting at each end.

Opt-in (needs Docker and port 8081):

    pytest -m integration
"""
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.db import get_session  # noqa: E402
from app.lab_manager.router import get_adapter, get_target_clients  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402

pytestmark = pytest.mark.integration

API = "/api/v1"
TERMINAL = {"completed", "incomplete", "failed", "cleaned_up"}


@pytest.fixture
def client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'resp.db'}", connect_args={"check_same_thread": False}, poolclass=NullPool
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


def shopcloud_rate_limit() -> dict:
    """Ask the real ShopCloud what its rate limit is right now."""
    settings = get_settings()
    response = httpx.get(
        f"{settings.lab_targets['shopcloud']}/lab/rate-limit",
        headers={"X-Lab-Token": settings.lab_control_token},
        timeout=5,
    )
    response.raise_for_status()
    return response.json()


def test_response_is_approved_verified_and_reverted(client):
    lab = client.post(f"{API}/labs", json={"name": "resp", "target": "shopcloud"}).json()
    assert client.post(f"{API}/labs/{lab['id']}/start").status_code == 200

    run = client.post(f"{API}/labs/{lab['id']}/runs", json={"scenario_id": "authentication_abuse"}).json()
    assert wait_terminal(client, run["id"])["status"] == "completed"

    # The alert belongs to the run's one incident.
    alerts = client.get(f"{API}/alerts", params={"run_id": run["id"]}).json()
    assert len(alerts) == 1
    incidents = client.get(f"{API}/incidents", params={"run_id": run["id"]}).json()
    assert [i["id"] for i in incidents] == [alerts[0]["incident_id"]]

    # Requested: pending, and ShopCloud is untouched.
    pending = client.post(f"{API}/incidents/{incidents[0]['id']}/responses")
    assert pending.status_code == 201
    pending = pending.json()
    assert pending["status"] == "pending_approval"
    assert shopcloud_rate_limit()["enabled"] is False

    # Approved: executed on ShopCloud, then verified with follow-up logins.
    approved = client.post(f"{API}/responses/{pending['id']}/approve").json()
    assert approved["status"] == "verified", approved
    assert approved["verification_result"]["received"]["statuses"] == [401] * 5 + [429] * 2
    assert shopcloud_rate_limit() == {"enabled": True, "max_attempts": 5, "window_seconds": 60}

    # Cleanup turns it off again.
    assert client.post(f"{API}/runs/{run['id']}/cleanup").status_code == 200
    assert shopcloud_rate_limit()["enabled"] is False
    assert client.get(f"{API}/responses/{pending['id']}").json()["status"] == "reverted"

    trail = client.get(
        f"{API}/audit", params={"object_type": "response_action", "object_id": pending["id"]}
    ).json()
    assert [row["action"] for row in reversed(trail)] == [
        "response.requested", "response.approved", "response.executed", "response.verified", "response.reverted",
    ]

    assert client.post(f"{API}/labs/{lab['id']}/stop").status_code == 200
