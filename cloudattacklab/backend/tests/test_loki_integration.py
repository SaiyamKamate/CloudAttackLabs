"""Integration test: with the monitoring stack up and TELEMETRY_SOURCE=loki, run
authentication_abuse against the real lab and check the events came from Loki.

Opt-in. Needs Docker, port 8081 and the monitoring stack:

    docker compose -f monitoring/docker-compose.monitoring.yml up -d
    pytest -m integration backend/tests/test_loki_integration.py
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
from app.lab_manager.router import get_adapter, get_target_clients  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402
from app.telemetry.loki import LokiSource  # noqa: E402
from app.telemetry.source import TelemetrySourceError  # noqa: E402

pytestmark = pytest.mark.integration

API = "/api/v1"
TERMINAL = {"completed", "failed", "cleaned_up"}


@pytest.fixture
def client(tmp_path):
    settings = replace(get_settings(), telemetry_source="loki")
    try:
        LokiSource(settings.loki_url, settings.telemetry_service).check()
    except TelemetrySourceError as exc:
        pytest.skip(f"the monitoring stack is not up: {exc}")

    engine = create_engine(
        f"sqlite:///{tmp_path / 'loki.db'}", connect_args={"check_same_thread": False}, poolclass=NullPool
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
    get_adapter.cache_clear()
    get_target_clients.cache_clear()
    adapter = get_adapter()
    try:
        with TestClient(app) as client:
            yield client
    finally:
        adapter.stop()
        engine.dispose()


def wait_terminal(client, run_id, timeout=120.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"{API}/runs/{run_id}").json()
        if body["status"] in TERMINAL:
            return body
        time.sleep(0.5)
    raise AssertionError(f"run {run_id} did not settle: {body}")


def test_run_with_loki_collects_every_event_with_loki_references(client):
    lab = client.post(f"{API}/labs", json={"name": "loki", "target": "shopcloud"}).json()
    assert client.post(f"{API}/labs/{lab['id']}/start").status_code == 200
    detail = client.get(f"{API}/labs/{lab['id']}").json()
    assert detail["telemetry"]["source"] == "loki" and detail["telemetry"]["reachable"] is True

    run = client.post(f"{API}/labs/{lab['id']}/runs", json={"scenario_id": "authentication_abuse"}).json()
    settled = wait_terminal(client, run["id"])
    assert settled["status"] == "completed", settled

    # All 20 failed logins arrived through Loki, each once, with a loki: reference.
    failures = client.get(
        f"{API}/events", params={"run_id": run["id"], "event_type": "authentication_failure", "limit": 1000}
    ).json()
    assert len(failures) == 20, [e["message"] for e in failures]
    references = [e["raw_event_reference"] for e in failures]
    assert all(ref.startswith("loki:shopcloud:") for ref in references)
    assert len(set(references)) == 20

    # The original record is kept as evidence.
    one = client.get(f"{API}/events/{failures[0]['event_id']}").json()
    assert one["raw"]["request_id"] == references[0].split(":", 2)[2]
    assert one["raw"]["event"] == "login_failed"

    # Detection is unchanged: one alert over the 20 events.
    alerts = client.get(f"{API}/alerts", params={"run_id": run["id"]}).json()
    assert [a["rule_id"] for a in alerts] == ["repeated_failed_logins"]
    assert client.get(f"{API}/alerts/{alerts[0]['id']}").json()["event_count"] == 20

    assert client.post(f"{API}/runs/{run['id']}/cleanup").status_code == 200
    assert client.post(f"{API}/labs/{lab['id']}/stop").status_code == 200
