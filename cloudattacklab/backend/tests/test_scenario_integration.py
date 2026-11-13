"""Integration test: runs the authentication_abuse scenario against the real lab.

It starts the real Docker lab, starts a run through the API, waits for it to
complete, then runs cleanup. Opt-in:

    pytest -m integration

Needs Docker and free port 8081. The database is file-backed SQLite (so the
background run thread has its own connection); the real Docker adapter, the real
ShopCloud HTTP client and the real scenario script are what is under test.
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
TERMINAL = {"completed", "failed", "cleaned_up"}


@pytest.fixture
def client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'it.db'}", connect_args={"check_same_thread": False}, poolclass=NullPool
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


def test_run_authentication_abuse_against_the_real_lab(client):
    settings = get_settings()
    shopcloud = settings.lab_targets["shopcloud"]
    lab_headers = {"X-Lab-Token": settings.lab_control_token}

    lab = client.post(f"{API}/labs", json={"name": "it-run", "target": "shopcloud"}).json()
    assert client.post(f"{API}/labs/{lab['id']}/start").status_code == 200, "lab should start"

    # Start the run; the API returns the run id straight away.
    response = client.post(f"{API}/labs/{lab['id']}/runs", json={"scenario_id": "authentication_abuse"})
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] in ("pending", "running")

    settled = wait_terminal(client, run["id"])
    assert settled["status"] == "completed", settled
    summary = settled["summary"]
    assert summary["attempts_sent"] == 20
    assert summary["unauthorized_401"] == 20  # all 20 wrong-password logins got 401
    assert summary["rate_limited_429"] == 0   # rate limit is off at baseline

    # Cleanup restores the baseline through ShopCloud's /lab endpoints.
    cleanup = client.post(f"{API}/runs/{run['id']}/cleanup")
    assert cleanup.status_code == 200
    assert cleanup.json()["status"] == "cleaned_up"
    assert httpx.get(f"{shopcloud}/lab/rate-limit", headers=lab_headers).json()["enabled"] is False

    assert client.post(f"{API}/labs/{lab['id']}/stop").status_code == 200
