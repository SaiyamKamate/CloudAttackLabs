"""Observation runs (scenario type `observation`, shipped as `benign_baseline`).

An observation run executes no script, collects the target's logs and the test
container's Docker events through the normal ingest code, and is checked
against every detection rule with the threshold of the scenario that uses it.
Attack runs are still checked against their own rule only.

Fake adapter and fake target, no Docker. The shipped scenarios are copied into
a temporary folder with the baseline shortened to 1 second.
"""
import json
import shutil
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import get_session  # noqa: E402
from app.detection.checks import observation_checks, run_checks  # noqa: E402
from app.detection.rules import load_rules  # noqa: E402
from app.lab_manager.container import TEST_TARGET_LABEL  # noqa: E402
from app.lab_manager.router import get_adapter, get_target_clients  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Alert, Base, Event  # noqa: E402
from app.scenario_engine import service as service_module  # noqa: E402
from app.scenario_engine.engine import ScenarioEngine  # noqa: E402
from app.scenario_engine.router import get_scenario_engine  # noqa: E402
from app.scenario_engine.runner import RunResult  # noqa: E402
from app.schemas.scenario import ScenarioError, load_scenario  # noqa: E402

from conftest import FakeAdapter, FakeTarget  # noqa: E402

API = "/api/v1"
TERMINAL = {"completed", "incomplete", "failed", "cleaned_up"}
SHIPPED = ROOT / "scenarios"
BASELINE = SHIPPED / "benign_baseline.yaml"
TARGETS = {"shopcloud", "test_container"}


# --- the scenario file --------------------------------------------------------
def test_shipped_baseline_is_an_observation_with_no_script_detection_or_response():
    scenario = load_scenario(BASELINE, TARGETS)
    assert scenario.type == "observation"
    assert scenario.execution.script is None
    assert scenario.execution.parameters["duration_seconds"] == 600
    assert scenario.detection is None and scenario.response is None


def _variant(tmp_path, change, source=BASELINE):
    data = yaml.safe_load(source.read_text())
    change(data)
    path = tmp_path / source.name
    path.write_text(yaml.safe_dump(data))
    return path


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["execution"].update(script="authentication_abuse.py"),        # observation with a script
        lambda d: d.update(detection={"rule": "repeated_failed_logins", "threshold": 10}),
        lambda d: d["execution"]["parameters"].pop("duration_seconds"),
        lambda d: d["execution"]["parameters"].update(duration_seconds=601),      # longer than the timeout
        lambda d: d["execution"]["parameters"].update(duration_seconds="600"),
        lambda d: d.update(response={"action": "temporary_rate_limit", "verification": {
            "check": "login_returns_429", "description": "x"}}),
    ],
)
def test_malformed_observation_is_rejected(tmp_path, change):
    with pytest.raises(ScenarioError):
        load_scenario(_variant(tmp_path, change), TARGETS)


@pytest.mark.parametrize("field", ["detection", "script"])
def test_attack_scenario_still_needs_a_script_and_a_rule(tmp_path, field):
    def change(d):
        if field == "script":
            d["execution"].pop("script")
        else:
            d.pop("detection")

    with pytest.raises(ScenarioError):
        load_scenario(_variant(tmp_path, change, SHIPPED / "authentication_abuse.yaml"), TARGETS)


def test_every_rule_is_checked_with_the_threshold_of_the_scenario_that_uses_it():
    engine = ScenarioEngine(SHIPPED, TARGETS, {"test_container"})
    checks = {c.rule: c for c in observation_checks(load_rules(ROOT / "rules"), engine.list())}
    assert set(checks) == {"repeated_failed_logins", "repeated_access_denied", "suspicious_container_process"}
    assert (checks["repeated_failed_logins"].threshold, checks["repeated_failed_logins"].taken_from) == (
        10, "scenario authentication_abuse")
    assert (checks["repeated_access_denied"].threshold, checks["repeated_access_denied"].window_seconds) == (5, 60)
    assert checks["suspicious_container_process"].threshold == 1


def test_attack_scenario_is_checked_against_its_own_rule_only():
    scenario = load_scenario(SHIPPED / "authentication_abuse.yaml", TARGETS)
    assert [c.rule for c in run_checks(scenario, {})] == ["repeated_failed_logins"]


# --- runs through the API ----------------------------------------------------------
class WatchedLab(FakeAdapter):
    """The fake lab plus a test container whose Docker events a test can add."""

    def __init__(self) -> None:
        super().__init__()
        self.event_lines: list[str] = []

    def exec_in_test_container(self, command: str, at: datetime) -> None:
        key, value = TEST_TARGET_LABEL
        ns = int(at.timestamp() * 1e9)
        self.event_lines.append(json.dumps({
            "Type": "container", "Action": f"exec_start: {command}",
            "Actor": {"ID": "faketc1" + "0" * 57,
                      "Attributes": {key: value, "name": "cloudattacklab-lab-test_container-1"}},
            "time": ns // 10**9, "timeNano": ns,
        }))

    def container_events(self, since, until):
        lo, hi = int(since.timestamp() * 1e9), int(until.timestamp() * 1e9)
        return [line for line in self.event_lines if lo <= json.loads(line)["timeNano"] <= hi]


@pytest.fixture(autouse=True)
def _clear_active_labs():
    service_module._active_labs.clear()
    yield
    service_module._active_labs.clear()


@pytest.fixture
def scenarios_dir(tmp_path):
    folder = tmp_path / "scenarios"
    folder.mkdir()
    for path in SHIPPED.glob("*.yaml"):
        shutil.copy(path, folder / path.name)
    _variant(folder, lambda d: d["execution"]["parameters"].update(duration_seconds=1))
    return folder


@pytest.fixture
def file_engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'obs.db'}", connect_args={"check_same_thread": False}, poolclass=NullPool
    )
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA busy_timeout=5000")
    yield engine
    engine.dispose()


@pytest.fixture
def adapter():
    return WatchedLab()


@pytest.fixture
def client(file_engine, adapter, scenarios_dir, monkeypatch):
    app = create_app()

    def _session():
        with Session(file_engine, expire_on_commit=False) as session:
            yield session

    target = FakeTarget(adapter)
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_adapter] = lambda: adapter
    app.dependency_overrides[get_target_clients] = lambda: {"shopcloud": target}
    app.dependency_overrides[get_scenario_engine] = lambda: ScenarioEngine(
        scenarios_dir, TARGETS, {"test_container"})
    with TestClient(app) as client:
        yield client


def _record(at, event, **fields):
    return json.dumps({
        "timestamp": at.isoformat().replace("+00:00", "Z"), "service": "shopcloud", "level": "info",
        "event": event, "request_id": str(uuid.uuid4()), "source_ip": "172.21.0.1", **fields,
    })


def _traffic(base: datetime, *, failed_logins: int, denials: int) -> str:
    lines = [_record(base, "login_success", method="POST", path="/api/login", status=200, username="lab_user")]
    for i in range(failed_logins):
        lines.append(_record(base + timedelta(milliseconds=10 * (i + 1)), "login_failed", method="POST",
                             path="/api/login", status=401, username="lab_user"))
    for i in range(denials):
        lines.append(_record(base + timedelta(milliseconds=5 + 10 * i), "access_denied", method="GET",
                             path="/api/orders", status=403, username="lab_restricted"))
    return "\n".join(lines)


def _running_lab(client):
    lab = client.post(f"{API}/labs", json={"name": "obs-lab", "target": "shopcloud"}).json()
    assert client.post(f"{API}/labs/{lab['id']}/start").status_code == 200
    return lab


def _wait(client, run_id, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"{API}/runs/{run_id}").json()
        if body["status"] in TERMINAL:
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish: {body}")


def _no_scripts(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("an observation run must not run a script")

    monkeypatch.setattr(service_module.runner, "execute_script", refuse)
    monkeypatch.setattr(service_module.runner, "execute_container_script", refuse)


def test_near_misses_raise_nothing(client, adapter, file_engine, monkeypatch):
    _no_scripts(monkeypatch)
    lab = _running_lab(client)
    # 9 failed logins (threshold 10) and 4 denials (threshold 5), no container process.
    adapter.log_text = _traffic(datetime.now(timezone.utc) + timedelta(milliseconds=300), failed_logins=9, denials=4)

    run = client.post(f"{API}/labs/{lab['id']}/runs", json={"scenario_id": "benign_baseline"}).json()
    done = _wait(client, run["id"])

    assert done["status"] == "completed", done["error"]
    assert done["summary"]["script"] is None
    assert {r["rule"] for r in done["parameters"]["detection_rules"]} == {
        "repeated_failed_logins", "repeated_access_denied", "suspicious_container_process"}
    with Session(file_engine) as session:
        types = sorted(e.event_type for e in session.scalars(select(Event).where(Event.run_id == uuid.UUID(run["id"]))))
        assert types.count("authentication_failure") == 9
        assert types.count("authorization_denied") == 4
        assert session.scalars(select(Alert)).first() is None
    report = client.get(f"{API}/runs/{run['id']}/report").json()
    assert report["detection"]["rule"] is None
    assert len(report["detection"]["rules"]) == 3
    assert report["alerts"] == []


def test_each_rule_fires_on_its_own_events(client, adapter, file_engine, monkeypatch):
    _no_scripts(monkeypatch)
    lab = _running_lab(client)
    base = datetime.now(timezone.utc) + timedelta(milliseconds=300)
    adapter.log_text = _traffic(base, failed_logins=10, denials=5)
    adapter.exec_in_test_container("date", base + timedelta(milliseconds=200))

    run = client.post(f"{API}/labs/{lab['id']}/runs", json={"scenario_id": "benign_baseline"}).json()
    done = _wait(client, run["id"])
    assert done["status"] == "completed", done["error"]

    report = client.get(f"{API}/runs/{run['id']}/report").json()
    by_rule = {a["rule_id"]: a for a in report["alerts"]}
    assert set(by_rule) == {"repeated_failed_logins", "repeated_access_denied", "suspicious_container_process"}
    # Latency is measured with each rule's own threshold.
    assert by_rule["suspicious_container_process"]["threshold_crossed_at"] is not None
    assert by_rule["repeated_failed_logins"]["event_count"] == 10
    assert all(a["incident_id"] for a in report["alerts"])
    assert report["telemetry"]["collected_total"] == 1 + 10 + 5 + 1


def test_attack_run_ignores_other_rules(client, adapter, monkeypatch):
    """An authentication_abuse run whose window also holds 6 denials and an exec raises only its own alert."""
    monkeypatch.setattr(service_module.runner, "execute_script", lambda *a, **k: RunResult(
        outcome="completed", summary={"attempts_sent": 20}, error=None))
    lab = _running_lab(client)
    base = datetime.now(timezone.utc) + timedelta(milliseconds=300)
    adapter.log_text = _traffic(base, failed_logins=20, denials=6)

    run = client.post(f"{API}/labs/{lab['id']}/runs", json={"scenario_id": "authentication_abuse"}).json()
    done = _wait(client, run["id"])
    assert done["status"] == "completed", done["error"]
    assert "detection_rules" not in done["parameters"]

    report = client.get(f"{API}/runs/{run['id']}/report").json()
    assert [a["rule_id"] for a in report["alerts"]] == ["repeated_failed_logins"]
    assert report["detection"]["rule"] == "repeated_failed_logins"
    assert report["detection"]["threshold"] == 10
