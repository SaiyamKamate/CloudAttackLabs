"""Step 9: incident correlation, the timeline, the risk score and the run report.

No Docker. The correlation, timeline and risk tests use the in-memory database
and the fake target from conftest; the report tests start runs through the API
with the fake lab (the fixtures from test_runs_api).
"""
import json
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.detection.engine import DetectionEngine  # noqa: E402
from app.incidents.risk import DISCLAIMER, compute_risk  # noqa: E402
from app.incidents.service import attach_alerts  # noqa: E402
from app.models import Alert, Event, Incident, Lab, ScenarioRun  # noqa: E402
from app.scenario_engine.engine import ScenarioEngine  # noqa: E402
from app.scenario_engine.runner import RunResult  # noqa: E402

import test_runs_api as runs  # noqa: E402
from test_runs_api import _clear_active_labs, file_engine  # noqa: E402,F401
from test_unauthorized_resource_access import good_summary, log_record  # noqa: E402

API = "/api/v1"
RULES_DIR = ROOT / "rules"
ENGINE = ScenarioEngine(ROOT / "scenarios", {"shopcloud"})
AUTH = ENGINE.get("authentication_abuse")
ACCESS = ENGINE.get("unauthorized_resource_access")


def now() -> datetime:
    return datetime.now(timezone.utc)


def make_lab(db, name=None, status="running") -> Lab:
    lab = Lab(name=name or f"lab-{uuid.uuid4().hex[:8]}", target="shopcloud", status=status)
    db.add(lab)
    db.flush()
    return lab


def make_run(db, lab, scenario=AUTH, *, started=None) -> ScenarioRun:
    started = started or now() - timedelta(seconds=120)
    run = ScenarioRun(
        lab_id=lab.id,
        scenario_id=scenario.id,
        scenario_version=scenario.version,
        status="completed",
        parameters=dict(scenario.execution.parameters),
        started_at=started,
        finished_at=started + timedelta(seconds=30),
    )
    db.add(run)
    db.flush()
    return run


def add_events(
    db, run, count, *, event_type="authentication_failure", source_ip="172.20.0.5", username="lab_user",
    resource="shopcloud", offset=1,
) -> list[Event]:
    events = [
        Event(
            timestamp=run.started_at + timedelta(seconds=offset + i),
            source="application",
            event_type=event_type,
            severity="low",
            source_ip=source_ip,
            resource=resource,
            message="Invalid username or password",
            raw_event_reference=f"docker:shopcloud:{uuid.uuid4()}",
            schema_version="1.0",
            lab_id=run.lab_id,
            run_id=run.id,
            username=username,
        )
        for i in range(count)
    ]
    db.add_all(events)
    db.flush()
    return events


def make_alert(db, run, events, *, rule_id="repeated_failed_logins", severity="medium") -> Alert:
    alert = Alert(
        rule_id=rule_id,
        severity=severity,
        title=rule_id,
        description=f"{len(events)} events",
        lab_id=run.lab_id,
        run_id=run.id,
        event_count=len(events),
        first_event_at=events[0].timestamp,
        last_event_at=events[-1].timestamp,
        events=list(events),
    )
    db.add(alert)
    return alert


def correlate(db, run, scenario, *alerts):
    incidents = attach_alerts(db, run, scenario, list(alerts))
    db.commit()
    return incidents


# --- correlation ----------------------------------------------------------
def test_alerts_with_the_same_source_ip_on_the_same_resource_join_one_incident(db):
    run = make_run(db, make_lab(db))
    logins = make_alert(db, run, add_events(db, run, 12, source_ip="172.20.0.5", username="lab_user"))
    denials = make_alert(
        db, run,
        add_events(db, run, 6, event_type="authorization_denied", source_ip="172.20.0.5", username="lab_restricted"),
        rule_id="repeated_access_denied",
    )
    correlate(db, run, AUTH, logins)
    correlate(db, run, AUTH, denials)  # arrives later; joins the existing incident

    incident = db.scalars(select(Incident)).one()
    assert {a.id for a in incident.alerts} == {logins.id, denials.id}
    assert incident.correlation_key == f"run:{run.id}|resource:shopcloud|ip:172.20.0.5"


def test_alerts_with_the_same_username_join_one_incident_even_from_two_ips(db):
    run = make_run(db, make_lab(db))
    first = make_alert(db, run, add_events(db, run, 12, source_ip="172.20.0.5", username="lab_user"))
    second = make_alert(db, run, add_events(db, run, 12, source_ip="172.20.0.9", username="lab_user"))
    incidents = correlate(db, run, AUTH, first, second)

    assert len(incidents) == 1
    assert first.incident_id == second.incident_id == incidents[0].id


def test_unrelated_alerts_in_the_same_run_stay_in_separate_incidents(db):
    run = make_run(db, make_lab(db))
    first = make_alert(db, run, add_events(db, run, 12, source_ip="172.20.0.5", username="lab_user"))
    second = make_alert(db, run, add_events(db, run, 12, source_ip="172.20.0.9", username="someone_else"))
    correlate(db, run, AUTH, first, second)

    incidents = list(db.scalars(select(Incident)))
    assert len(incidents) == 2
    assert first.incident_id != second.incident_id
    assert len({i.correlation_key for i in incidents}) == 2


def test_same_source_on_a_different_resource_is_a_separate_incident(db):
    run = make_run(db, make_lab(db))
    first = make_alert(db, run, add_events(db, run, 12, resource="shopcloud"))
    second = make_alert(db, run, add_events(db, run, 12, resource="another-service"))
    correlate(db, run, AUTH, first, second)
    assert first.incident_id != second.incident_id


def test_two_runs_never_share_an_incident_even_with_the_same_source(db):
    lab = make_lab(db)
    run_one, run_two = make_run(db, lab, AUTH), make_run(db, lab, ACCESS)
    first = make_alert(db, run_one, add_events(db, run_one, 12))
    second = make_alert(
        db, run_two, add_events(db, run_two, 6, event_type="authorization_denied"), rule_id="repeated_access_denied"
    )
    correlate(db, run_one, AUTH, first)
    correlate(db, run_two, ACCESS, second)

    incidents = list(db.scalars(select(Incident).order_by(Incident.created_at)))
    assert [i.run_id for i in incidents] == [run_one.id, run_two.id]
    assert first.incident_id != second.incident_id


def test_incident_links_to_its_alerts_events_and_raw_references(client, db):
    run = make_run(db, make_lab(db))
    events = add_events(db, run, 12)
    alert = DetectionEngine(RULES_DIR).run_for_run(db, run, AUTH)[0]

    incident = client.get(f"{API}/incidents/{alert.incident_id}").json()
    assert [a["id"] for a in incident["alerts"]] == [str(alert.id)]
    linked = client.get(f"{API}/alerts/{incident['alerts'][0]['id']}").json()["events"]
    assert {e["raw_event_reference"] for e in linked} == {e.raw_event_reference for e in events}


# --- timeline -------------------------------------------------------------
@pytest.fixture
def handled(client, db, adapter):
    """A detected incident whose response was requested, approved, verified, then cleaned up."""
    adapter.start()
    run = make_run(db, make_lab(db))
    events = add_events(db, run, 12)
    alert = DetectionEngine(RULES_DIR).run_for_run(db, run, AUTH)[0]
    incident_id = str(alert.incident_id)

    response = client.post(f"{API}/incidents/{incident_id}/responses").json()
    assert client.post(f"{API}/responses/{response['id']}/approve").json()["status"] == "verified"
    assert client.post(f"{API}/runs/{run.id}/cleanup").status_code == 200
    return {"incident_id": incident_id, "run": run, "events": events, "alert": alert, "response_id": response["id"]}


def test_timeline_is_in_time_order_and_includes_the_response_steps(client, handled):
    timeline = client.get(f"{API}/incidents/{handled['incident_id']}").json()["timeline"]

    times = [datetime.fromisoformat(entry["time"]) for entry in timeline]
    assert times == sorted(times)

    types = [entry["type"] for entry in timeline]
    assert types == (
        ["scenario_started"]
        + ["event"] * 12
        + ["alert_fired", "response_requested", "response_approved", "response_executed", "response_verified"]
        + ["response_reverted", "cleanup"]
    )


def test_every_timeline_entry_has_a_description_and_working_evidence_link(client, handled):
    timeline = client.get(f"{API}/incidents/{handled['incident_id']}").json()["timeline"]

    for entry in timeline:
        assert entry["time"] and entry["type"] and len(entry["description"]) > 10
        assert entry["evidence"]["href"].startswith(API)
    for href in {entry["evidence"]["href"] for entry in timeline}:
        assert client.get(href).status_code == 200, href

    event_entries = [entry for entry in timeline if entry["type"] == "event"]
    assert [e["evidence"]["raw_event_reference"] for e in event_entries] == [
        event.raw_event_reference for event in handled["events"]
    ]
    by_type = {entry["type"]: entry for entry in timeline}
    assert by_type["alert_fired"]["evidence"]["id"] == str(handled["alert"].id)
    assert by_type["response_verified"]["evidence"]["id"] == handled["response_id"]
    assert "2 of 7 follow-up logins were blocked with 429" in by_type["response_verified"]["description"]
    assert "disable_rate_limit" in by_type["cleanup"]["description"]


def test_a_rejected_response_shows_on_the_timeline(client, db, adapter):
    adapter.start()
    run = make_run(db, make_lab(db))
    add_events(db, run, 12)
    alert = DetectionEngine(RULES_DIR).run_for_run(db, run, AUTH)[0]
    response = client.post(f"{API}/incidents/{alert.incident_id}/responses").json()
    client.post(f"{API}/responses/{response['id']}/reject")

    types = [e["type"] for e in client.get(f"{API}/incidents/{alert.incident_id}").json()["timeline"]]
    assert types[-2:] == ["response_requested", "response_rejected"]


# --- risk score -----------------------------------------------------------
def points_in(explanation: str) -> list[int]:
    return [int(m) for m in re.findall(r"^([+-]\d+): ", explanation, flags=re.MULTILINE)]


def test_risk_score_is_deterministic_for_the_same_input():
    kwargs = dict(severities=["medium", "low"], event_counts=[20, 7], threshold=10, verified_response=None)
    assert compute_risk(**kwargs) == compute_risk(**kwargs)


def test_risk_explanation_matches_the_numbers():
    risk = compute_risk(severities=["medium"], event_counts=[20], threshold=10)
    # medium 40, 10 events over the threshold x 2 = 20, no extra alerts.
    assert risk.score == 60
    assert points_in(risk.explanation) == [40, 20, 0] == [f.points for f in risk.factors]
    assert sum(points_in(risk.explanation)) == risk.score
    assert risk.explanation.startswith("Risk score: 60 out of 100.")
    assert "10 event(s) over the detection threshold of 10" in risk.explanation

    lowered = compute_risk(
        severities=["medium"], event_counts=[20], threshold=10, verified_response="temporary_rate_limit"
    )
    assert lowered.score == 40
    assert points_in(lowered.explanation) == [40, 20, 0, -20]
    assert "temporary_rate_limit" in lowered.explanation


def test_risk_score_factors_are_capped_and_the_score_stays_in_range():
    high = compute_risk(severities=["critical"] * 6, event_counts=[100] * 6, threshold=5)
    assert points_in(high.explanation) == [80, 20, 15]  # volume capped at 20, extra alerts at 15
    assert high.score == 100 and "limited to the range 0-100" in high.explanation

    low = compute_risk(severities=["info"], event_counts=[5], threshold=5, verified_response="temporary_rate_limit")
    assert sum(points_in(low.explanation)) == -10
    assert low.score == 0 and "limited to the range 0-100" in low.explanation


def test_risk_explanation_says_what_the_score_is_not():
    explanation = compute_risk(severities=["high"], event_counts=[12], threshold=10).explanation
    assert DISCLAIMER in explanation
    assert "does not predict financial loss" in explanation
    assert "not a universal measure of risk" in explanation


def test_risk_score_is_stored_on_the_incident_and_drops_when_the_response_is_verified(client, db, adapter):
    adapter.start()
    run = make_run(db, make_lab(db))
    add_events(db, run, 20)
    alert = DetectionEngine(RULES_DIR).run_for_run(db, run, AUTH)[0]

    before = client.get(f"{API}/incidents/{alert.incident_id}").json()
    assert before["risk_score"] == 60
    assert sum(points_in(before["risk_explanation"])) == 60
    assert client.get(f"{API}/incidents").json()[0]["risk_score"] == 60  # the list carries it too

    response = client.post(f"{API}/incidents/{alert.incident_id}/responses").json()
    assert client.get(f"{API}/incidents/{alert.incident_id}").json()["risk_score"] == 60  # only requested
    client.post(f"{API}/responses/{response['id']}/approve")

    after = client.get(f"{API}/incidents/{alert.incident_id}").json()
    assert after["risk_score"] == 40
    assert sum(points_in(after["risk_explanation"])) == 40
    assert "-20: a response action (temporary_rate_limit) was verified as working." in after["risk_explanation"]


def test_a_failed_verification_does_not_lower_the_risk_score(client, db, adapter, target):
    adapter.start()
    target.applies_limit = False  # the target accepts the limit but never answers 429
    run = make_run(db, make_lab(db))
    add_events(db, run, 20)
    alert = DetectionEngine(RULES_DIR).run_for_run(db, run, AUTH)[0]
    response = client.post(f"{API}/incidents/{alert.incident_id}/responses").json()
    assert client.post(f"{API}/responses/{response['id']}/approve").json()["status"] == "failed"
    assert client.get(f"{API}/incidents/{alert.incident_id}").json()["risk_score"] == 60


# --- run report (runs started through the API, fake lab) -------------------
@pytest.fixture
def adapter_r():
    return runs.FakeAdapter()


@pytest.fixture
def run_client(file_engine, adapter_r, monkeypatch):
    """The API on a file-backed database, so the background run thread can use it."""
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.db import get_session
    from app.lab_manager.router import get_adapter, get_target_clients
    from app.main import create_app

    app = create_app()

    def _session():
        with Session(file_engine, expire_on_commit=False) as session:
            yield session

    target = runs.FakeTarget(adapter_r)
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_adapter] = lambda: adapter_r
    app.dependency_overrides[get_target_clients] = lambda: {"shopcloud": target}
    with TestClient(app) as client:
        yield client


def recent_records(records):
    """Stamp the records from now on, 1 ms apart. Called while the fake script "runs",
    so they fall inside the run (from its start until its script finished)."""
    base = now()
    for i, record in enumerate(records):
        record["timestamp"] = (base + timedelta(milliseconds=i)).isoformat().replace("+00:00", "Z")
    return "\n".join(json.dumps(record) for record in records)


def run_scenario(client, adapter, monkeypatch, lab, scenario_id):
    if scenario_id == "authentication_abuse":
        summary = runs.fake_summary()
        records = [
            log_record("login_failed", 401, username="lab_user", path="/api/login", method="POST") for _ in range(20)
        ]
    else:
        summary = good_summary()
        records = [log_record("access_denied", 403, username="lab_restricted") for _ in range(8)]
    def fake_execute(script_path, base_url, parameters, timeout_seconds):
        # Like the real script: ShopCloud logs each request while the script runs.
        adapter.log_text = recent_records(records)
        time.sleep(0.05)
        return RunResult("completed", summary, None)

    monkeypatch.setattr(runs.service_module.runner, "execute_script", fake_execute)
    response = runs.start_run(client, lab["id"], scenario_id)
    assert response.status_code == 201, response.text
    run = runs.wait_terminal(client, response.json()["id"])
    assert run["status"] == "completed", run
    return run


REPORT_FIELDS = {
    "report_version", "generated_at", "lab", "scenario", "run", "script_summary", "telemetry", "detection",
    "alerts", "incidents", "responses", "post_run_verification", "cleanup",
}


def test_report_for_authentication_abuse_with_response_and_cleanup(run_client, adapter_r, monkeypatch):
    client = run_client
    lab = runs.running_lab(client)
    run = run_scenario(client, adapter_r, monkeypatch, lab, "authentication_abuse")
    incident = client.get(f"{API}/incidents", params={"run_id": run["id"]}).json()[0]
    response = client.post(f"{API}/incidents/{incident['id']}/responses").json()
    assert client.post(f"{API}/responses/{response['id']}/approve").json()["status"] == "verified"

    reply = client.get(f"{API}/runs/{run['id']}/report")
    assert reply.status_code == 200
    report = reply.json()
    assert set(report) == REPORT_FIELDS

    assert report["lab"]["id"] == lab["id"] and report["lab"]["name"] == "run-lab"
    assert report["scenario"]["id"] == "authentication_abuse"
    assert report["scenario"]["version"] == "1.0.0" and report["scenario"]["name"] == AUTH.name
    assert report["run"]["parameters"] == {"username": "lab_user", "attempts": 20, "interval_seconds": 1}
    assert report["run"]["started_at"] and report["run"]["finished_at"]
    assert report["run"]["duration_seconds"] >= 0
    assert report["script_summary"] == runs.fake_summary()

    assert report["telemetry"]["expected"] == [
        {
            "source": "application", "event_type": "authentication_failure",
            "expected_min_count": 10, "collected_count": 20, "met": True,
        }
    ]
    assert report["telemetry"]["all_expected_met"] is True and report["telemetry"]["collected_total"] == 20

    assert report["detection"] == {
        "rule": "repeated_failed_logins", "threshold": 10, "window_seconds": 60,
        "rules": [{"rule": "repeated_failed_logins", "threshold": 10, "window_seconds": 60,
                   "taken_from": "scenario authentication_abuse"}],
    }
    [alert] = report["alerts"]
    assert alert["rule_id"] == "repeated_failed_logins" and alert["event_count"] == 20
    # Latency runs from the 10th event (the one that crossed the threshold) to the alert.
    events = client.get(f"{API}/alerts/{alert['id']}").json()["events"]
    assert alert["threshold_event_id"] == events[9]["event_id"]
    crossed = datetime.fromisoformat(alert["threshold_crossed_at"])
    created = datetime.fromisoformat(alert["created_at"])
    assert alert["detection_latency_seconds"] == pytest.approx((created - crossed).total_seconds(), abs=0.001)
    assert alert["detection_latency_seconds"] >= 0

    [reported] = report["incidents"]
    assert reported["id"] == incident["id"] and reported["alert_ids"] == [alert["id"]]
    assert reported["risk_score"] == 40 and "Risk score: 40 out of 100." in reported["risk_explanation"]

    [action] = report["responses"]
    assert action["action"] == "temporary_rate_limit" and action["status"] == "verified"
    assert action["verification_result"]["passed"] is True
    assert report["post_run_verification"] is None
    assert report["cleanup"] == {"status": "not_run", "runs": []}

    assert client.post(f"{API}/runs/{run['id']}/cleanup").status_code == 200
    after = client.get(f"{API}/runs/{run['id']}/report").json()
    assert after["run"]["status"] == "cleaned_up"
    assert after["cleanup"]["status"] == "done"
    assert after["cleanup"]["runs"][0]["steps_done"] == ["disable_rate_limit", "reset_target_state"]
    assert after["responses"][0]["status"] == "reverted"


def test_report_for_unauthorized_resource_access(run_client, adapter_r, monkeypatch):
    client = run_client
    lab = runs.running_lab(client)
    run = run_scenario(client, adapter_r, monkeypatch, lab, "unauthorized_resource_access")

    report = client.get(f"{API}/runs/{run['id']}/report").json()
    assert set(report) == REPORT_FIELDS
    assert report["scenario"]["id"] == "unauthorized_resource_access" and report["scenario"]["version"] == "1.0.0"
    assert report["run"]["parameters"]["attempts"] == 8
    assert report["script_summary"] == good_summary()  # the verification is reported separately
    assert report["post_run_verification"]["check"] == "access_remains_denied"
    assert report["post_run_verification"]["still_denied"] is True

    assert report["telemetry"]["expected"][0] == {
        "source": "application", "event_type": "authorization_denied",
        "expected_min_count": 5, "collected_count": 8, "met": True,
    }
    [alert] = report["alerts"]
    assert alert["rule_id"] == "repeated_access_denied"
    assert alert["detection_latency_seconds"] >= 0
    [incident] = report["incidents"]
    # medium 40 + 3 events over the threshold of 5 x 2 = 46
    assert incident["risk_score"] == 46
    assert report["responses"] == []  # this scenario defines no response action
    assert report["cleanup"]["status"] == "not_run"


def test_two_scenarios_run_one_after_the_other_give_two_incidents(run_client, adapter_r, monkeypatch):
    client = run_client
    lab = runs.running_lab(client)
    first = run_scenario(client, adapter_r, monkeypatch, lab, "authentication_abuse")
    second = run_scenario(client, adapter_r, monkeypatch, lab, "unauthorized_resource_access")

    incidents = client.get(f"{API}/incidents").json()
    assert [i["run_id"] for i in incidents] == [first["id"], second["id"]]
    assert [i["risk_score"] for i in incidents] == [60, 46]
    assert len({i["correlation_key"] for i in incidents}) == 2


def test_report_for_a_run_with_no_alert_still_has_every_field(client, db):
    run = make_run(db, make_lab(db))
    add_events(db, run, 3)
    db.commit()
    report = client.get(f"{API}/runs/{run.id}/report").json()
    assert set(report) == REPORT_FIELDS
    assert report["telemetry"]["expected"][0]["collected_count"] == 3
    assert report["telemetry"]["expected"][0]["met"] is False
    assert report["alerts"] == [] and report["incidents"] == [] and report["responses"] == []


def test_report_for_an_unknown_run_is_404(client):
    reply = client.get(f"{API}/runs/{uuid.uuid4()}/report")
    assert reply.status_code == 404 and reply.json()["error"] == "run_not_found"
