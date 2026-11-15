"""Integration test: the whole unauthorized_resource_access path against the real lab.

Starts the real Docker lab, runs the scenario through the API with the real
script, collects ShopCloud's real logs, and checks the alert, its linked
events, the "still denied" verification and cleanup. Opt-in:

    pytest -m integration

Needs Docker and free port 8081.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from test_scenario_integration import API, client, wait_terminal  # noqa: E402,F401

pytestmark = pytest.mark.integration

SCENARIO_ID = "unauthorized_resource_access"


def test_unauthorized_resource_access_against_the_real_lab(client):
    lab = client.post(f"{API}/labs", json={"name": "it-access", "target": "shopcloud"}).json()
    assert client.post(f"{API}/labs/{lab['id']}/start").status_code == 200, "lab should start"

    response = client.post(f"{API}/labs/{lab['id']}/runs", json={"scenario_id": SCENARIO_ID})
    assert response.status_code == 201, response.text
    run = wait_terminal(client, response.json()["id"])

    # The script: 8 requests as lab_restricted, every one refused with 403.
    assert run["status"] == "completed", run
    summary = run["summary"]
    assert summary["attempts_sent"] == 8
    assert summary["denied_403"] == 8
    assert summary["other"] == 0
    assert summary["access_control_failed"] is False

    # Verification: one more request after the run, still denied.
    verification = summary["verification"]
    assert verification["check"] == "access_remains_denied"
    assert verification["still_denied"] is True
    assert verification["received"] == {"login_status": 200, "status": 403}

    # Telemetry: the 8 denials were collected from ShopCloud's real logs.
    events = client.get(
        f"{API}/events", params={"run_id": run["id"], "event_type": "authorization_denied"}
    ).json()
    assert len(events) == 8
    assert {e["username"] for e in events} == {"lab_restricted"}

    # Detection: one alert from the new rule, with all 8 events linked.
    alerts = client.get(f"{API}/alerts", params={"run_id": run["id"]}).json()
    assert [a["rule_id"] for a in alerts] == ["repeated_access_denied"]
    detail = client.get(f"{API}/alerts/{alerts[0]['id']}").json()
    assert detail["event_count"] == 8
    assert len(detail["events"]) == 8
    assert all(e["raw_event_reference"].startswith("docker:shopcloud:") for e in detail["events"])

    cleanup = client.post(f"{API}/runs/{run['id']}/cleanup")
    assert cleanup.status_code == 200
    assert cleanup.json()["status"] == "cleaned_up"

    assert client.post(f"{API}/labs/{lab['id']}/stop").status_code == 200
