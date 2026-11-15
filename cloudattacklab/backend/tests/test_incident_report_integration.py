"""Integration test for step 9 against the real lab:

scenario -> alert -> incident (risk score, timeline) -> response -> report.

Opt-in (needs Docker and free port 8081):

    pytest -m integration
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from test_scenario_integration import API, client, wait_terminal  # noqa: E402,F401

pytestmark = pytest.mark.integration


def test_scenario_alert_incident_response_and_report_against_the_real_lab(client):
    lab = client.post(f"{API}/labs", json={"name": "it-report", "target": "shopcloud"}).json()
    assert client.post(f"{API}/labs/{lab['id']}/start").status_code == 200, "lab should start"

    # Scenario and alert.
    started = client.post(f"{API}/labs/{lab['id']}/runs", json={"scenario_id": "authentication_abuse"})
    assert started.status_code == 201, started.text
    run = wait_terminal(client, started.json()["id"])
    assert run["status"] == "completed", run
    alerts = client.get(f"{API}/alerts", params={"run_id": run["id"]}).json()
    assert [a["rule_id"] for a in alerts] == ["repeated_failed_logins"]

    # Incident: one for the run, scored 60 (medium 40 + 10 events over the threshold x 2).
    incidents = client.get(f"{API}/incidents", params={"run_id": run["id"]}).json()
    assert [i["id"] for i in incidents] == [alerts[0]["incident_id"]]
    assert incidents[0]["risk_score"] == 60
    incident_id = incidents[0]["id"]

    # Response: requested, approved, executed on the real ShopCloud, verified.
    pending = client.post(f"{API}/incidents/{incident_id}/responses").json()
    approved = client.post(f"{API}/responses/{pending['id']}/approve").json()
    assert approved["status"] == "verified", approved

    incident = client.get(f"{API}/incidents/{incident_id}").json()
    assert incident["risk_score"] == 40  # 20 points off for the verified response
    assert "does not predict financial loss" in incident["risk_explanation"]

    timeline = incident["timeline"]
    times = [datetime.fromisoformat(entry["time"]) for entry in timeline]
    assert times == sorted(times)
    types = [entry["type"] for entry in timeline]
    assert types[0] == "scenario_started"
    assert types.count("event") == 20
    for step in ("alert_fired", "response_requested", "response_approved", "response_executed", "response_verified"):
        assert step in types
    assert all(
        entry["evidence"]["raw_event_reference"].startswith("docker:shopcloud:")
        for entry in timeline
        if entry["type"] == "event"
    )

    # Cleanup, then the report.
    assert client.post(f"{API}/runs/{run['id']}/cleanup").status_code == 200
    report = client.get(f"{API}/runs/{run['id']}/report").json()
    assert report["lab"]["name"] == "it-report"
    assert report["scenario"]["id"] == "authentication_abuse" and report["scenario"]["version"] == "1.0.0"
    assert report["run"]["status"] == "cleaned_up"
    assert report["script_summary"]["attempts_sent"] == 20
    assert report["telemetry"]["expected"][0]["collected_count"] == 20
    assert report["telemetry"]["all_expected_met"] is True
    assert report["alerts"][0]["detection_latency_seconds"] >= 0
    assert report["incidents"][0]["id"] == incident_id and report["incidents"][0]["risk_score"] == 40
    assert report["responses"][0]["status"] == "reverted"
    assert report["responses"][0]["verification_result"]["passed"] is True
    assert report["cleanup"]["status"] == "done"

    assert client.post(f"{API}/labs/{lab['id']}/stop").status_code == 200
