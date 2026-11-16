"""Unit tests for the Loki telemetry source and the TELEMETRY_SOURCE setting.

No Loki is needed: Loki's HTTP answers come from a recorded sample
(fixtures/loki_query_range_shopcloud.json, a real query_range response for
{service="shopcloud"} with two streams) through httpx.MockTransport.
"""
import json
import sys
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool, StaticPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.db import get_session  # noqa: E402
from app.lab_manager.router import get_adapter, get_target_clients  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Alert, AuditLog, Base, Event  # noqa: E402
from app.scenario_engine import service as service_module  # noqa: E402
from app.scenario_engine.runner import RunResult  # noqa: E402
from app.telemetry.factory import make_source  # noqa: E402
from app.telemetry.ingest import IngestResult, TelemetryIngestor, ingest_until_settled  # noqa: E402
from app.telemetry.loki import LokiSource, parse_query_range  # noqa: E402
from app.telemetry.normalize import RunContext, normalize  # noqa: E402
from app.telemetry.source import DockerLogSource, TelemetrySourceError  # noqa: E402

from conftest import FakeAdapter, FakeTarget  # noqa: E402

API = "/api/v1"
TERMINAL = {"completed", "failed", "cleaned_up"}
SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "loki_query_range_shopcloud.json").read_text())
LOKI = "http://loki.test:3100"


def sample_records():
    """The ShopCloud records in the sample, oldest first."""
    pairs = [(int(ts), line) for stream in SAMPLE["data"]["result"] for ts, line in stream["values"]]
    return [json.loads(line) for _, line in sorted(pairs)]


def loki_answering(body, *, status=200, seen=None):
    """A LokiSource whose HTTP calls get `body` back (and are recorded in `seen`)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(dict(request.url.params))
        return httpx.Response(status, json=body) if isinstance(body, dict) else httpx.Response(status, text=body)

    return LokiSource(LOKI, "shopcloud", client=httpx.Client(transport=httpx.MockTransport(handler)))


def loki_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 111] Connection refused", request=request)

    return LokiSource(LOKI, "shopcloud", client=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def ctx_before_sample(run_id=None):
    first = datetime.fromisoformat(sample_records()[0]["timestamp"])
    return RunContext(
        run_id=run_id or uuid.uuid4(),
        lab_id=uuid.uuid4(),
        scenario_id="authentication_abuse",
        started_at=first - timedelta(seconds=1),
    )


# --- parsing the recorded response ----------------------------------------
def test_recorded_response_is_parsed_into_lines_oldest_first_across_streams():
    entries = parse_query_range(SAMPLE)
    # Two streams (detected_level info and warn), merged and sorted by time.
    assert len(SAMPLE["data"]["result"]) == 2
    assert [ts for ts, _ in entries] == sorted(ts for ts, _ in entries)
    events = [json.loads(line)["event"] for _, line in entries]
    assert events == ["login_failed", "login_success", "login_failed"]


def test_loki_source_returns_the_same_lines_shopcloud_wrote():
    seen = []
    source = loki_answering(SAMPLE, seen=seen)
    since = datetime(2026, 11, 15, 16, 0, tzinfo=timezone.utc)
    lines = source.collect(since=since)
    assert [json.loads(line) for line in lines] == sample_records()
    # One query_range call for the service's stream, from the run's start, oldest first.
    assert seen[0]["query"] == '{service="shopcloud"}'
    assert seen[0]["start"] == str(int(since.timestamp() * 1_000_000_000))
    assert seen[0]["direction"] == "forward"


def test_loki_lines_normalize_like_docker_lines_with_loki_references(session):
    c = ctx_before_sample()
    result = TelemetryIngestor(session, loki_answering(SAMPLE), c).ingest()
    assert result.stored == 3 and result.invalid_json == 0 and result.skipped == 0
    stored = list(session.scalars(select(Event).where(Event.run_id == c.run_id).order_by(Event.timestamp)))
    records = sample_records()
    assert [e.raw_event_reference for e in stored] == [f"loki:shopcloud:{r['request_id']}" for r in records]
    assert [e.event_type for e in stored] == ["authentication_failure", "authentication_success", "authentication_failure"]
    # The original record is kept as evidence, unchanged.
    assert [e.raw for e in stored] == records

    # The same records as Docker would give them normalize to the same events;
    # only the reference prefix differs.
    for row, record in zip(stored, records):
        from_docker = normalize(record, c, "docker")
        assert from_docker.raw_event_reference == "docker" + row.raw_event_reference[len("loki"):]
        assert (from_docker.event_type, from_docker.source_ip, from_docker.username, from_docker.message) == (
            row.event_type, row.source_ip, row.username, row.message
        )


def test_a_full_page_is_followed_by_the_next_page(monkeypatch):
    monkeypatch.setattr("app.telemetry.loki.PAGE_LIMIT", 2)
    entries = parse_query_range(SAMPLE)
    calls = []

    def handler(request):
        start = int(request.url.params["start"])
        page = [entry for entry in entries if entry[0] >= start][:2]
        calls.append(start)
        body = {"status": "success", "data": {"resultType": "streams", "result": [
            {"stream": {"service": "shopcloud"}, "values": [[str(ts), line] for ts, line in page]}
        ]}}
        return httpx.Response(200, json=body)

    source = LokiSource(LOKI, "shopcloud", client=httpx.Client(transport=httpx.MockTransport(handler)))
    lines = source.collect(since=datetime(2026, 11, 15, 16, 0, tzinfo=timezone.utc))
    assert [json.loads(line) for line in lines] == sample_records()
    assert len(calls) == 2


# --- late-arriving logs -----------------------------------------------------
class GrowingSource:
    """Returns more of the sample on each read, as Loki does while logs arrive."""

    name = "loki"
    late_arriving = True

    def __init__(self, counts):
        self.lines = [json.dumps(r) for r in sample_records()]
        self.counts = list(counts)
        self.reads = 0

    def collect(self, since=None):
        count = self.counts[min(self.reads, len(self.counts) - 1)]
        self.reads += 1
        return self.lines[:count]

    def check(self):
        return None


def test_late_arriving_logs_are_picked_up_until_the_count_stops_growing(session):
    c = ctx_before_sample()
    source = GrowingSource([1, 2, 3, 3, 3])
    sleeps = []
    total = ingest_until_settled(
        lambda: TelemetryIngestor(session, source, c).ingest(),
        interval_seconds=2,
        timeout_seconds=15,
        sleep=sleeps.append,
    )
    assert total.stored == 3
    assert source.reads == 4  # 1, 2, 3, then 3 again: settled
    assert sleeps == [2, 2, 2]
    assert session.scalar(select(Event).where(Event.raw_event_reference.like("loki:%")).limit(1)) is not None


def test_polling_stops_at_the_timeout_even_if_records_keep_coming():
    now = [0.0]
    counts = iter(range(1, 100))

    def ingest_once():
        return IngestResult(collected=next(counts))

    def sleep(seconds):
        now[0] += seconds

    total = ingest_until_settled(
        ingest_once, interval_seconds=2, timeout_seconds=7, sleep=sleep, clock=lambda: now[0]
    )
    assert now[0] <= 7
    assert total.collected == 4  # read at t=0, 2, 4, 6


def test_an_empty_answer_is_not_taken_as_settled():
    now = [0.0]
    counts = iter([0, 0, 0, 4, 4])

    def sleep(seconds):
        now[0] += seconds

    total = ingest_until_settled(
        lambda: IngestResult(collected=next(counts)), interval_seconds=2, timeout_seconds=20,
        sleep=sleep, clock=lambda: now[0],
    )
    assert total.collected == 4 and now[0] == 8  # kept polling past the empty answers


# --- duplicates -------------------------------------------------------------
def test_the_same_request_id_is_not_stored_twice_across_reads_and_sources(session):
    c = ctx_before_sample()
    source = loki_answering(SAMPLE)
    first = TelemetryIngestor(session, source, c).ingest()
    second = TelemetryIngestor(session, source, c).ingest()
    assert first.stored == 3
    assert second.stored == 0 and second.duplicates == 3

    # The same records read through Docker are recognised as already stored.
    adapter = FakeAdapter()
    adapter.log_text = "\n".join(json.dumps(r) for r in sample_records())
    third = TelemetryIngestor(session, DockerLogSource(adapter, "shopcloud"), c).ingest()
    assert third.stored == 0 and third.duplicates == 3
    assert len(list(session.scalars(select(Event)))) == 3


def test_a_record_returned_twice_in_one_answer_is_stored_once(session):
    doubled = json.loads(json.dumps(SAMPLE))
    doubled["data"]["result"].append(doubled["data"]["result"][0])  # Loki can repeat a stream
    result = TelemetryIngestor(session, loki_answering(doubled), ctx_before_sample()).ingest()
    assert result.stored == 3 and result.duplicates == 1


# --- Loki unreachable -------------------------------------------------------
def test_unreachable_loki_raises_a_clear_error():
    source = loki_unreachable()
    with pytest.raises(TelemetrySourceError) as raised:
        source.collect(since=datetime.now(timezone.utc))
    message = str(raised.value)
    assert "Loki at http://loki.test:3100 could not be reached" in message
    assert "TELEMETRY_SOURCE=docker" in message
    with pytest.raises(TelemetrySourceError):
        source.check()


def test_a_loki_error_answer_raises_a_clear_error():
    with pytest.raises(TelemetrySourceError, match="answered HTTP 500"):
        loki_answering("too many outstanding requests", status=500).collect()
    with pytest.raises(TelemetrySourceError, match="unexpected answer"):
        loki_answering({"status": "error", "error": "bad query"}).collect()


# --- the setting ------------------------------------------------------------
def test_docker_is_the_default_and_loki_is_chosen_by_the_setting(monkeypatch):
    monkeypatch.delenv("TELEMETRY_SOURCE", raising=False)
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.telemetry_source == "docker"
        assert isinstance(make_source(settings, FakeAdapter()), DockerLogSource)

        monkeypatch.setenv("TELEMETRY_SOURCE", "loki")
        monkeypatch.setenv("LOKI_URL", "http://127.0.0.1:3100/")
        get_settings.cache_clear()
        settings = get_settings()
        source = make_source(settings, FakeAdapter())
        assert isinstance(source, LokiSource) and source.base_url == "http://127.0.0.1:3100"

        monkeypatch.setenv("TELEMETRY_SOURCE", "kafka")
        get_settings.cache_clear()
        with pytest.raises(ValueError, match="TELEMETRY_SOURCE must be 'docker' or 'loki'"):
            get_settings()
    finally:
        get_settings.cache_clear()


# --- through the API --------------------------------------------------------
@pytest.fixture(autouse=True)
def _clear_active_labs():
    service_module._active_labs.clear()
    yield
    service_module._active_labs.clear()


@pytest.fixture
def file_engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'loki.db'}", connect_args={"check_same_thread": False}, poolclass=NullPool
    )
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA busy_timeout=5000")
    yield engine
    engine.dispose()


def make_client(file_engine, **settings_over):
    adapter = FakeAdapter()
    target = FakeTarget(adapter)
    app = create_app()

    def _session():
        with Session(file_engine, expire_on_commit=False) as session:
            yield session

    settings = replace(
        get_settings(),
        **{
            "telemetry_collect_interval_seconds": 0.05,
            "loki_settle_interval_seconds": 0.05,
            "loki_settle_timeout_seconds": 2,
            **settings_over,
        },
    )
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_adapter] = lambda: adapter
    app.dependency_overrides[get_target_clients] = lambda: {"shopcloud": target}
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def run_to_end(client, scenario_id="authentication_abuse", timeout=15.0):
    lab = client.post(f"{API}/labs", json={"name": "loki-lab", "target": "shopcloud"}).json()
    assert client.post(f"{API}/labs/{lab['id']}/start").status_code == 200
    response = client.post(f"{API}/labs/{lab['id']}/runs", json={"scenario_id": scenario_id})
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"{API}/runs/{run_id}").json()
        if body["status"] in TERMINAL:
            return body
        time.sleep(0.05)
    raise AssertionError(f"run did not settle: {body}")


def test_run_with_unreachable_loki_fails_clearly_without_running_the_script(file_engine, monkeypatch):
    calls = []
    monkeypatch.setattr(service_module, "make_source", lambda settings, adapter: loki_unreachable())
    monkeypatch.setattr(
        service_module.runner, "execute_script", lambda *a, **k: calls.append(a) or RunResult("completed", {}, None)
    )
    with make_client(file_engine, telemetry_source="loki") as client:
        run = run_to_end(client)
    assert run["status"] == "failed"
    assert run["error"].startswith("Telemetry source 'loki' is not available: Loki at http://loki.test:3100 could not be reached")
    assert calls == []  # nothing was sent to the target
    with Session(file_engine) as db:
        assert list(db.scalars(select(Event))) == []  # no events invented, no Docker fallback
        failure = db.scalars(select(AuditLog).where(AuditLog.action == "run.failure")).one()
        assert failure.detail["script_ran"] is False


def test_loki_failing_during_a_run_fails_the_run_and_raises_no_alert(file_engine, monkeypatch):
    class DiesAfterCheck(GrowingSource):
        def collect(self, since=None):
            raise TelemetrySourceError("Loki at http://loki.test:3100 could not be reached (ConnectError).")

    monkeypatch.setattr(service_module, "make_source", lambda settings, adapter: DiesAfterCheck([0]))
    monkeypatch.setattr(service_module.runner, "execute_script", lambda *a, **k: RunResult("completed", {"ok": 1}, None))
    with make_client(file_engine, telemetry_source="loki") as client:
        run = run_to_end(client)
    assert run["status"] == "failed"
    assert run["error"] == (
        "Telemetry source 'loki' failed during the run: Loki at http://loki.test:3100 could not be reached (ConnectError)."
    )
    with Session(file_engine) as db:
        assert list(db.scalars(select(Alert))) == []


def test_a_run_fails_clearly_when_loki_never_receives_the_logs(file_engine, monkeypatch):
    monkeypatch.setattr(service_module, "make_source", lambda settings, adapter: GrowingSource([0]))
    monkeypatch.setattr(service_module.runner, "execute_script", lambda *a, **k: RunResult("completed", {"ok": 1}, None))
    with make_client(file_engine, telemetry_source="loki", loki_settle_timeout_seconds=0.3) as client:
        run = run_to_end(client)
    assert run["status"] == "failed"
    assert run["error"] == (
        "Telemetry source 'loki' failed during the run: loki returned no shopcloud log lines for this run "
        "after waiting 0.3 s. Check that the monitoring stack's Alloy is running and shipping the lab "
        "container's logs."
    )


def test_a_run_picks_up_records_that_reach_loki_after_the_script_ends(file_engine, monkeypatch):
    """The script's 12 failed logins only appear in 'Loki' after it finishes, in two
    steps; the run still stores all 12 (with loki: references) and raises the alert."""
    state = {"lines": [], "reads_after": 0}

    class LateLoki:
        name = "loki"
        late_arriving = True

        def check(self):
            return None

        def collect(self, since=None):
            if not state["lines"]:
                return []
            state["reads_after"] += 1
            visible = 5 if state["reads_after"] == 1 else len(state["lines"])
            return state["lines"][:visible]

    def script(*args, **kwargs):
        now = datetime.now(timezone.utc)
        records = [
            {
                "timestamp": (now + timedelta(milliseconds=10 * i)).isoformat().replace("+00:00", "Z"),
                "service": "shopcloud", "level": "warning", "event": "login_failed",
                "request_id": f"late-{i}", "source_ip": "172.21.0.1", "method": "POST",
                "path": "/api/login", "status": 401, "username": "lab_user",
                "message": "Invalid username or password",
            }
            for i in range(12)
        ]
        state["pending"] = [json.dumps(r) for r in records]
        return RunResult("completed", {"attempts_sent": 12}, None)

    real_set = service_module.threading.Event.set

    def set_then_publish(self):
        # The run's stop signal: from now on the records "reach Loki".
        if state.get("pending") and not state["lines"]:
            state["lines"] = state["pending"]
        real_set(self)

    monkeypatch.setattr(service_module, "make_source", lambda settings, adapter: LateLoki())
    monkeypatch.setattr(service_module.runner, "execute_script", script)
    monkeypatch.setattr(service_module.threading.Event, "set", set_then_publish)
    with make_client(file_engine, telemetry_source="loki") as client:
        run = run_to_end(client)
        assert run["status"] == "completed", run
        events = client.get(f"{API}/events", params={"run_id": run["id"], "limit": 1000}).json()
        alerts = client.get(f"{API}/alerts", params={"run_id": run["id"]}).json()
    assert len(events) == 12
    assert all(e["raw_event_reference"].startswith("loki:shopcloud:late-") for e in events)
    assert state["reads_after"] >= 3  # 5 records, then 12, then 12 again: settled
    assert [a["rule_id"] for a in alerts] == ["repeated_failed_logins"]


def test_lab_detail_shows_the_active_telemetry_source(file_engine):
    with make_client(file_engine) as client:
        lab = client.post(f"{API}/labs", json={"name": "t-lab", "target": "shopcloud"}).json()
        body = client.get(f"{API}/labs/{lab['id']}").json()
    assert body["telemetry"] == {"source": "docker", "loki_url": None, "reachable": None, "error": None}

    # Port 9 (discard) is closed: Loki is configured but cannot be reached.
    with make_client(file_engine, telemetry_source="loki", loki_url="http://127.0.0.1:9") as client:
        lab = client.post(f"{API}/labs", json={"name": "t-lab-2", "target": "shopcloud"}).json()
        body = client.get(f"{API}/labs/{lab['id']}").json()
    assert body["telemetry"]["source"] == "loki"
    assert body["telemetry"]["reachable"] is False
    assert "could not be reached" in body["telemetry"]["error"]
