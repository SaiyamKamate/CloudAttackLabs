"""Unit tests for incidents, the Response Manager and GET /audit.

They use the fake adapter and the fake target from conftest, so nothing here
needs Docker. The fake target keeps a rate limit like ShopCloud does, and can be
told to ignore it (a target that does not apply the limit).
"""
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.detection.engine import DetectionEngine  # noqa: E402
from app.models import Alert, AuditLog, Incident, Lab, ResponseAction, ScenarioRun  # noqa: E402
from app.response_manager.verification import WRONG_PASSWORD  # noqa: E402
from app.scenario_engine.engine import ScenarioEngine  # noqa: E402

from test_detection import add_failures  # noqa: E402

API = "/api/v1"
LIMIT_ON = {"enabled": True, "max_attempts": 5, "window_seconds": 60}


@pytest.fixture
def seeded(db, adapter):
    """A running lab with a finished authentication_abuse run, its alert and its incident."""
    adapter.start()
    adapter.calls.clear()
    lab = Lab(name="resp-lab", target="shopcloud", status="running")
    db.add(lab)
    db.flush()
    run = ScenarioRun(
        lab_id=lab.id, scenario_id="authentication_abuse", scenario_version="1.0.0", status="completed"
    )
    db.add(run)
    db.flush()
    incident = Incident(lab_id=lab.id, run_id=run.id, title="Repeated Failed Login Attempts")
    alert = Alert(
        rule_id="repeated_failed_logins",
        severity="medium",
        title="Repeated failed logins",
        description="20 failures",
        lab_id=lab.id,
        run_id=run.id,
        event_count=20,
        incident=incident,
    )
    db.add_all([incident, alert])
    db.commit()
    return {"lab_id": str(lab.id), "run_id": str(run.id), "incident_id": str(incident.id), "alert_id": str(alert.id)}


def request_response(client, seeded, **kwargs):
    return client.post(f"{API}/incidents/{seeded['incident_id']}/responses", **kwargs)


def audit_actions(db, response_id):
    db.expire_all()
    rows = db.scalars(
        select(AuditLog).where(AuditLog.object_type == "response_action", AuditLog.object_id == response_id)
        .order_by(AuditLog.id)
    )
    return [row.action for row in rows]


# --- incidents ------------------------------------------------------------
def test_detection_links_every_alert_of_a_run_to_an_incident(db):
    scenario = ScenarioEngine(ROOT / "scenarios", {"shopcloud"}).get("authentication_abuse")
    lab = Lab(name="det-lab")
    db.add(lab)
    db.flush()
    run = ScenarioRun(lab_id=lab.id, scenario_id="authentication_abuse", scenario_version="1.0.0", status="running")
    db.add(run)
    db.flush()
    detection = DetectionEngine(ROOT / "rules")

    add_failures(db, run, 12, source_ip="172.20.0.5")
    first = detection.run_for_run(db, run, scenario)
    add_failures(db, run, 12, source_ip="172.20.0.9")  # a second alert in the same run
    second = detection.run_for_run(db, run, scenario)

    assert len(first) == 1 and len(second) == 1
    # Since step 9 the two source IPs are unrelated (no shared IP or username),
    # so each alert gets its own incident. See test_incidents_and_reports.py.
    incidents = list(db.scalars(select(Incident)))
    assert len(incidents) == 2
    assert all(i.run_id == run.id and i.lab_id == lab.id and i.status == "open" for i in incidents)
    assert {first[0].incident_id, second[0].incident_id} == {i.id for i in incidents}


def test_no_alert_means_no_incident(db):
    scenario = ScenarioEngine(ROOT / "scenarios", {"shopcloud"}).get("authentication_abuse")
    lab = Lab(name="quiet-lab")
    db.add(lab)
    db.flush()
    run = ScenarioRun(lab_id=lab.id, scenario_id="authentication_abuse", scenario_version="1.0.0", status="running")
    db.add(run)
    db.flush()
    add_failures(db, run, 3)

    assert DetectionEngine(ROOT / "rules").run_for_run(db, run, scenario) == []
    assert list(db.scalars(select(Incident))) == []


def test_list_and_get_incident(client, seeded):
    listed = client.get(f"{API}/incidents").json()
    assert [i["id"] for i in listed] == [seeded["incident_id"]]
    assert listed[0]["run_id"] == seeded["run_id"]
    assert client.get(f"{API}/incidents", params={"run_id": str(uuid.uuid4())}).json() == []

    response_id = request_response(client, seeded).json()["id"]
    detail = client.get(f"{API}/incidents/{seeded['incident_id']}").json()
    assert [a["id"] for a in detail["alerts"]] == [seeded["alert_id"]]
    assert [r["id"] for r in detail["responses"]] == [response_id]
    # This incident was inserted directly, so it was never scored; the timeline
    # holds its alert and the one response step so far.
    assert detail["risk_score"] is None
    assert [entry["type"] for entry in detail["timeline"]] == ["alert_fired", "response_requested"]


def test_alert_carries_its_incident_id(client, seeded):
    assert client.get(f"{API}/alerts/{seeded['alert_id']}").json()["incident_id"] == seeded["incident_id"]


def test_get_unknown_incident_is_404(client):
    response = client.get(f"{API}/incidents/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"] == "incident_not_found"


# --- request --------------------------------------------------------------
def test_request_creates_a_pending_response_from_the_scenario(client, seeded, target, db):
    response = request_response(client, seeded)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending_approval"
    assert body["action"] == "temporary_rate_limit"
    assert body["parameters"] == {"max_attempts": 5, "window_seconds": 60}
    assert body["decided_at"] is None and body["executed_at"] is None and body["verification_result"] is None
    assert audit_actions(db, body["id"]) == ["response.requested"]
    assert target.rate_limit_calls == [] and target.logins == []


def test_request_body_cannot_choose_the_action_parameters_or_target(client, seeded, target):
    hostile = {
        "action": "stop_container",
        "parameters": {"max_attempts": 1, "window_seconds": 3600},
        "target": "http://203.0.113.10:8000",
        "status": "verified",
    }
    body = request_response(client, seeded, json=hostile).json()
    assert body["action"] == "temporary_rate_limit"
    assert body["parameters"] == {"max_attempts": 5, "window_seconds": 60}
    assert body["status"] == "pending_approval"

    approved = client.post(f"{API}/responses/{body['id']}/approve", json=hostile).json()
    assert approved["status"] == "verified"
    assert target.rate_limit_calls == [LIMIT_ON]  # sent to the lab's configured target, with the scenario's values


def test_one_open_response_per_incident(client, seeded):
    assert request_response(client, seeded).status_code == 201
    second = request_response(client, seeded)
    assert second.status_code == 409
    assert second.json()["error"] == "response_already_open"


def test_request_for_unknown_incident_is_404(client):
    response = client.post(f"{API}/incidents/{uuid.uuid4()}/responses")
    assert response.status_code == 404
    assert response.json()["error"] == "incident_not_found"


# --- nothing runs without approval ----------------------------------------
def test_cannot_execute_without_approval(client, seeded, target, db):
    body = request_response(client, seeded).json()

    # Reading it, however often, executes nothing, and there is no "execute" endpoint.
    for _ in range(3):
        current = client.get(f"{API}/responses/{body['id']}").json()
        assert current["status"] == "pending_approval"
        assert current["executed_at"] is None
    assert client.post(f"{API}/responses/{body['id']}/execute").status_code == 404
    assert client.post(f"{API}/responses/{body['id']}/verify").status_code == 404

    assert target.rate_limit_calls == [] and target.logins == []
    assert target.rate_limit["enabled"] is False
    assert audit_actions(db, body["id"]) == ["response.requested"]


def test_reject_changes_nothing_on_the_target(client, seeded, target, db):
    body = request_response(client, seeded).json()
    rejected = client.post(f"{API}/responses/{body['id']}/reject")
    assert rejected.status_code == 200
    rejected = rejected.json()
    assert rejected["status"] == "rejected"
    assert rejected["decided_by"] == "anonymous" and rejected["decided_at"] is not None
    assert rejected["executed_at"] is None and rejected["verification_result"] is None

    # A rejected response can no longer be approved.
    late = client.post(f"{API}/responses/{body['id']}/approve")
    assert late.status_code == 409
    assert late.json()["error"] == "response_not_pending"

    assert target.rate_limit_calls == [] and target.logins == []
    assert target.rate_limit["enabled"] is False
    assert audit_actions(db, body["id"]) == ["response.requested", "response.rejected"]
    decided = db.scalars(select(AuditLog).where(AuditLog.action == "response.rejected")).one()
    assert decided.actor == "anonymous" and decided.detail["decided_by"] == "anonymous"

    # After a rejection a new response may be requested.
    assert request_response(client, seeded).status_code == 201


# --- approve: execute, then verify ----------------------------------------
def test_approve_executes_then_verifies(client, seeded, target, db):
    body = request_response(client, seeded).json()
    response = client.post(f"{API}/responses/{body['id']}/approve")
    assert response.status_code == 200
    approved = response.json()

    assert approved["status"] == "verified"
    assert approved["decided_by"] == "anonymous"
    assert approved["decided_at"] and approved["executed_at"] and approved["verified_at"]

    # Executed: one call, with enabled=true and the scenario's values.
    assert target.rate_limit_calls == [LIMIT_ON]
    assert target.rate_limit == LIMIT_ON

    # Verified: max_attempts + 2 wrong logins, the 2 beyond the limit got 429.
    assert target.logins == [("lab_user", WRONG_PASSWORD)] * 7
    result = approved["verification_result"]
    assert result["check"] == "login_returns_429" and result["passed"] is True
    assert result["sent"]["attempts"] == 7 and result["sent"]["username"] == "lab_user"
    assert result["received"]["statuses"] == [401, 401, 401, 401, 401, 429, 429]
    assert result["received"]["blocked_429"] == 2
    assert WRONG_PASSWORD not in str(result)

    assert audit_actions(db, body["id"]) == [
        "response.requested", "response.approved", "response.executed", "response.verified",
    ]
    approval = db.scalars(select(AuditLog).where(AuditLog.action == "response.approved")).one()
    assert approval.actor == "anonymous" and approval.detail["decided_by"] == "anonymous"
    executed = db.scalars(select(AuditLog).where(AuditLog.action == "response.executed")).one()
    assert executed.detail["sent"] == LIMIT_ON

    # The stored row matches what the API returned.
    stored = client.get(f"{API}/responses/{body['id']}").json()
    assert stored["status"] == "verified" and stored["verification_result"] == result


def test_a_response_cannot_be_approved_twice(client, seeded, target):
    body = request_response(client, seeded).json()
    assert client.post(f"{API}/responses/{body['id']}/approve").status_code == 200
    again = client.post(f"{API}/responses/{body['id']}/approve")
    assert again.status_code == 409
    assert client.post(f"{API}/responses/{body['id']}/reject").status_code == 409
    assert len(target.rate_limit_calls) == 1 and len(target.logins) == 7


def test_approve_is_refused_while_the_lab_is_stopped(client, seeded, target, db):
    body = request_response(client, seeded).json()
    lab = db.get(Lab, uuid.UUID(seeded["lab_id"]))
    lab.status = "stopped"
    db.commit()

    response = client.post(f"{API}/responses/{body['id']}/approve")
    assert response.status_code == 409
    assert response.json()["error"] == "lab_not_running"
    assert client.get(f"{API}/responses/{body['id']}").json()["status"] == "pending_approval"
    assert target.rate_limit_calls == []


def test_verification_fails_cleanly_if_the_target_does_not_apply_the_limit(client, seeded, target, db):
    target.applies_limit = False  # accepts POST /lab/rate-limit, but never answers 429
    body = request_response(client, seeded).json()
    response = client.post(f"{API}/responses/{body['id']}/approve")

    assert response.status_code == 200  # a failed verification is a result, not a server error
    failed = response.json()
    assert failed["status"] == "failed"
    assert failed["executed_at"] is not None
    result = failed["verification_result"]
    assert result["passed"] is False and result["stage"] == "verify"
    assert result["received"]["statuses"] == [401] * 7
    assert result["received"]["blocked_429"] == 0
    assert audit_actions(db, body["id"]) == [
        "response.requested", "response.approved", "response.executed", "response.failed",
    ]


def test_execution_failure_is_recorded_and_not_verified(client, seeded, target, db):
    target.fail_rate_limit = True  # ShopCloud refuses the lab control call
    body = request_response(client, seeded).json()
    failed = client.post(f"{API}/responses/{body['id']}/approve").json()

    assert failed["status"] == "failed"
    assert failed["executed_at"] is None and failed["verified_at"] is None
    assert failed["verification_result"]["stage"] == "execute"
    assert "403" in failed["verification_result"]["error"]
    assert target.logins == []  # no verification logins after a failed execution
    assert audit_actions(db, body["id"]) == ["response.requested", "response.approved", "response.failed"]


# --- reversible -----------------------------------------------------------
def test_cleanup_turns_the_limit_off_and_marks_the_response_reverted(client, seeded, target, db):
    body = request_response(client, seeded).json()
    assert client.post(f"{API}/responses/{body['id']}/approve").json()["status"] == "verified"
    assert target.rate_limit["enabled"] is True

    cleaned = client.post(f"{API}/runs/{seeded['run_id']}/cleanup")
    assert cleaned.status_code == 200 and cleaned.json()["status"] == "cleaned_up"

    assert target.rate_limit_calls[-1]["enabled"] is False  # the disable_rate_limit step
    assert target.rate_limit["enabled"] is False
    assert client.get(f"{API}/responses/{body['id']}").json()["status"] == "reverted"
    assert audit_actions(db, body["id"])[-1] == "response.reverted"
    reverted = db.scalars(select(AuditLog).where(AuditLog.action == "response.reverted")).one()
    assert reverted.detail["trigger"] == "cleanup:manual" and reverted.detail["status_before"] == "verified"


def test_cleanup_leaves_a_pending_response_pending(client, seeded, target):
    body = request_response(client, seeded).json()
    assert client.post(f"{API}/runs/{seeded['run_id']}/cleanup").status_code == 200
    assert client.get(f"{API}/responses/{body['id']}").json()["status"] == "pending_approval"


def test_lab_reset_clears_the_limit_and_marks_the_response_reverted(client, seeded, target, db):
    body = request_response(client, seeded).json()
    assert client.post(f"{API}/responses/{body['id']}/approve").json()["status"] == "verified"

    assert client.post(f"{API}/labs/{seeded['lab_id']}/reset").status_code == 200

    assert target.reset_calls == 1
    assert target.rate_limit["enabled"] is False
    assert client.get(f"{API}/responses/{body['id']}").json()["status"] == "reverted"
    reverted = db.scalars(select(AuditLog).where(AuditLog.action == "response.reverted")).one()
    assert reverted.detail["trigger"] == "lab.reset"

    # Reverted is final for that row; the incident may get a fresh response.
    assert db.scalars(select(ResponseAction)).one().status == "reverted"
    assert request_response(client, seeded).status_code == 201


# --- audit ----------------------------------------------------------------
def test_audit_endpoint_lists_the_trail_newest_first(client, seeded):
    body = request_response(client, seeded).json()
    client.post(f"{API}/responses/{body['id']}/approve")
    client.post(f"{API}/runs/{seeded['run_id']}/cleanup")

    rows = client.get(f"{API}/audit", params={"object_type": "response_action", "object_id": body["id"]}).json()
    assert [row["action"] for row in rows] == [
        "response.reverted", "response.verified", "response.executed", "response.approved", "response.requested",
    ]
    assert all(row["actor"] == "anonymous" for row in rows)

    everything = client.get(f"{API}/audit").json()
    assert "cleanup" in {row["action"] for row in everything}
    assert len(client.get(f"{API}/audit", params={"limit": 2}).json()) == 2
    assert client.get(f"{API}/audit", params={"action": "response.approved"}).json()[0]["detail"]["decided_by"] == "anonymous"


def test_get_unknown_response_is_404(client):
    response = client.get(f"{API}/responses/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"] == "response_not_found"
