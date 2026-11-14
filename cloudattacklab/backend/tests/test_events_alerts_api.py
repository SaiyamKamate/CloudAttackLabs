"""Unit tests for GET /events, /events/{id}, /alerts, /alerts/{id}."""
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.models import Alert, Event  # noqa: E402

API = "/api/v1"
START = datetime(2026, 11, 13, 14, 0, 0, tzinfo=timezone.utc)


def make_event(run_id, *, event_type="authentication_failure", offset=0, source_ip="172.20.0.5", ref=None):
    return Event(
        timestamp=START + timedelta(seconds=offset),
        source="application",
        event_type=event_type,
        severity="low" if event_type == "authentication_failure" else "info",
        source_ip=source_ip,
        resource="shopcloud",
        message="m",
        raw_event_reference=ref or f"docker:shopcloud:{uuid.uuid4()}",
        schema_version="1.0",
        run_id=run_id,
        raw={"event": event_type, "request_id": ref or "r"},
    )


@pytest.fixture
def seeded(db):
    run_id, other_run = uuid.uuid4(), uuid.uuid4()
    failures = [make_event(run_id, offset=i) for i in range(3)]
    success = make_event(run_id, event_type="authentication_success", offset=5)
    elsewhere = make_event(other_run, offset=0)
    db.add_all([*failures, success, elsewhere])
    db.flush()
    alert = Alert(
        rule_id="repeated_failed_logins",
        severity="medium",
        title="Repeated failed logins",
        description="3 failures",
        run_id=run_id,
        event_count=3,
        first_event_at=failures[0].timestamp,
        last_event_at=failures[-1].timestamp,
        events=failures,
    )
    db.add(alert)
    db.commit()
    return {"run_id": run_id, "other_run": other_run, "alert_id": alert.id, "failure_ids": [e.event_id for e in failures]}


# --- events ---------------------------------------------------------------
def test_list_events_filters_by_run(client, seeded):
    body = client.get(f"{API}/events", params={"run_id": str(seeded["run_id"])}).json()
    assert len(body) == 4  # 3 failures + 1 success for this run, not the other run's
    assert all(e["run_id"] == str(seeded["run_id"]) for e in body)
    assert "raw" not in body[0]  # list view omits the raw record


def test_list_events_filters_by_type(client, seeded):
    body = client.get(
        f"{API}/events", params={"run_id": str(seeded["run_id"]), "event_type": "authentication_failure"}
    ).json()
    assert len(body) == 3
    assert {e["event_type"] for e in body} == {"authentication_failure"}


def test_list_events_since_and_limit(client, seeded):
    since = (START + timedelta(seconds=4)).isoformat()
    body = client.get(f"{API}/events", params={"run_id": str(seeded["run_id"]), "since": since}).json()
    assert [e["event_type"] for e in body] == ["authentication_success"]

    limited = client.get(f"{API}/events", params={"run_id": str(seeded["run_id"]), "limit": 2}).json()
    assert len(limited) == 2


def test_get_event_includes_raw_evidence(client, seeded):
    event_id = str(seeded["failure_ids"][0])
    body = client.get(f"{API}/events/{event_id}").json()
    assert body["event_id"] == event_id
    assert body["raw"]["event"] == "authentication_failure"


def test_get_unknown_event_is_404(client):
    response = client.get(f"{API}/events/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"] == "event_not_found"


# --- alerts ---------------------------------------------------------------
def test_list_alerts(client, seeded):
    body = client.get(f"{API}/alerts").json()
    assert len(body) == 1
    assert body[0]["rule_id"] == "repeated_failed_logins"
    assert body[0]["event_count"] == 3


def test_list_alerts_filters(client, seeded):
    assert len(client.get(f"{API}/alerts", params={"run_id": str(seeded["run_id"])}).json()) == 1
    assert client.get(f"{API}/alerts", params={"run_id": str(seeded["other_run"])}).json() == []
    assert client.get(f"{API}/alerts", params={"rule_id": "nope"}).json() == []


def test_alert_detail_includes_its_events(client, seeded):
    body = client.get(f"{API}/alerts/{seeded['alert_id']}").json()
    assert body["event_count"] == 3
    assert len(body["events"]) == 3
    assert {e["event_type"] for e in body["events"]} == {"authentication_failure"}
    # events are ordered oldest-first
    stamps = [e["timestamp"] for e in body["events"]]
    assert stamps == sorted(stamps)


def test_get_unknown_alert_is_404(client):
    response = client.get(f"{API}/alerts/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"] == "alert_not_found"
