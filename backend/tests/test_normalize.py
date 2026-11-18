"""Unit tests for log normalization and ingestion (mapping, skips, dedup, window)."""
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.models import Base, Event  # noqa: E402
from app.telemetry.ingest import TelemetryIngestor  # noqa: E402
from app.telemetry.normalize import RunContext, normalize  # noqa: E402

STARTED = datetime(2026, 11, 13, 14, 0, 0, tzinfo=timezone.utc)


def ctx():
    return RunContext(run_id=uuid.uuid4(), lab_id=uuid.uuid4(), scenario_id="authentication_abuse", started_at=STARTED)


def record(event="login_failed", *, request_id=None, offset=1.0, **over):
    data = {
        "timestamp": (STARTED + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z"),
        "service": "shopcloud",
        "level": "warning",
        "event": event,
        "request_id": request_id or str(uuid.uuid4()),
        "source_ip": "172.20.0.5",
        "method": "POST",
        "path": "/api/login",
        "status": 401,
        "username": "lab_user",
        "message": "Invalid username or password",
    }
    data.update(over)
    return data


# --- mapping table (shopcloud-interface.md section 5) ---------------------
@pytest.mark.parametrize(
    "event_name,event_type,severity",
    [
        ("login_success", "authentication_success", "info"),
        ("login_failed", "authentication_failure", "low"),
        ("login_rate_limited", "rate_limit_triggered", "low"),
        ("access_granted", "resource_access", "info"),
        ("access_denied", "authorization_denied", "low"),
        ("rate_limit_changed", "config_changed", "info"),
        ("lab_reset", "config_changed", "info"),
    ],
)
def test_every_event_name_in_the_table_maps(event_name, event_type, severity):
    event = normalize(record(event_name), ctx())
    assert event is not None
    assert event.event_type == event_type
    assert event.severity == severity
    assert event.source == "application"
    assert event.resource == "shopcloud"


def test_reference_and_correlation_key():
    rid = "req-123"
    c = ctx()
    event = normalize(record(request_id=rid, source_ip="172.20.0.5"), c)
    assert event.raw_event_reference == "docker:shopcloud:req-123"
    assert event.correlation_key == f"run:{c.run_id}|ip:172.20.0.5"
    assert event.run_id == c.run_id and event.lab_id == c.lab_id and event.scenario_id == "authentication_abuse"


def test_unmapped_event_becomes_other_not_invented():
    event = normalize(record(event="something_new"), ctx())
    assert event.event_type == "other"


def test_record_without_request_id_is_not_usable():
    rec = record()
    rec["request_id"] = None
    assert normalize(rec, ctx()) is None


def test_bad_source_ip_becomes_none_without_crashing():
    event = normalize(record(source_ip="not-an-ip"), ctx())
    assert event is not None and event.source_ip is None


# --- ingestion: skip malformed, dedup, window -----------------------------
class FakeSource:
    def __init__(self, lines):
        self.lines = lines

    def collect(self, since=None):
        return list(self.lines)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_malformed_json_line_is_skipped_and_counted(session):
    c = ctx()
    lines = ["this is not json", json.dumps(record()), "{also not valid"]
    result = TelemetryIngestor(session, FakeSource(lines), c).ingest()
    assert result.invalid_json == 2
    assert result.stored == 1
    assert session.scalar(select(Event).where(Event.run_id == c.run_id)) is not None


def test_same_request_id_is_never_stored_twice(session):
    c = ctx()
    line = json.dumps(record(request_id="dup-1"))
    first = TelemetryIngestor(session, FakeSource([line, line]), c).ingest()
    assert first.stored == 1 and first.duplicates == 1

    # Ingesting again finds it already stored.
    second = TelemetryIngestor(session, FakeSource([line]), c).ingest()
    assert second.stored == 0 and second.duplicates == 1
    assert len(list(session.scalars(select(Event).where(Event.run_id == c.run_id)))) == 1


def test_lines_before_the_run_window_are_dropped(session):
    c = ctx()
    old = json.dumps(record(offset=-120))  # well before started_at - grace
    new = json.dumps(record(offset=5))
    result = TelemetryIngestor(session, FakeSource([old, new]), c, grace_seconds=3).ingest()
    assert result.stored == 1


def test_raw_record_is_kept_as_evidence(session):
    c = ctx()
    rec = record(request_id="keep-raw")
    TelemetryIngestor(session, FakeSource([json.dumps(rec)]), c).ingest()
    stored = session.scalar(select(Event).where(Event.run_id == c.run_id))
    assert stored.raw == rec
