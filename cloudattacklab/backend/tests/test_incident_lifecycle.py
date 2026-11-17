"""Audit bug 9: incidents can be closed, and a reverted response no longer lowers the risk score."""
import uuid

import test_runs_api as runs
from test_incidents_and_reports import adapter_r, run_client, run_scenario  # noqa: F401
from test_runs_api import _clear_active_labs, file_engine  # noqa: F401

API = "/api/v1"


def _auth_incident(client, adapter, monkeypatch):
    lab = runs.running_lab(client)
    run = run_scenario(client, adapter, monkeypatch, lab, "authentication_abuse")
    [incident] = client.get(f"{API}/incidents", params={"run_id": run["id"]}).json()
    return lab, run, incident


def _verified_response(client, incident):
    response = client.post(f"{API}/incidents/{incident['id']}/responses").json()
    assert client.post(f"{API}/responses/{response['id']}/approve").json()["status"] == "verified"
    return response


# --- close ------------------------------------------------------------------
def test_an_incident_can_be_closed_once_and_then_takes_no_new_response(run_client, adapter_r, monkeypatch):
    client = run_client
    _, _, incident = _auth_incident(client, adapter_r, monkeypatch)
    assert incident["status"] == "open"

    closed = client.post(f"{API}/incidents/{incident['id']}/close")

    assert closed.status_code == 200 and closed.json()["status"] == "closed"
    again = client.post(f"{API}/incidents/{incident['id']}/close")
    assert again.status_code == 409 and again.json()["error"] == "incident_closed"
    request = client.post(f"{API}/incidents/{incident['id']}/responses")
    assert request.status_code == 409 and request.json()["error"] == "incident_closed"
    detail = client.get(f"{API}/incidents/{incident['id']}").json()
    assert detail["status"] == "closed"
    assert detail["timeline"][-1]["type"] == "incident_closed"
    audit = client.get(f"{API}/audit", params={"action": "incident.closed"}).json()
    assert audit[0]["object_id"] == incident["id"]


def test_close_is_refused_while_a_response_waits_for_a_decision(run_client, adapter_r, monkeypatch):
    client = run_client
    _, _, incident = _auth_incident(client, adapter_r, monkeypatch)
    response = client.post(f"{API}/incidents/{incident['id']}/responses").json()

    refused = client.post(f"{API}/incidents/{incident['id']}/close")

    assert refused.status_code == 409 and refused.json()["error"] == "response_in_progress"
    client.post(f"{API}/responses/{response['id']}/reject")
    assert client.post(f"{API}/incidents/{incident['id']}/close").json()["status"] == "closed"


def test_close_an_unknown_incident_is_404(run_client):
    assert run_client.post(f"{API}/incidents/{uuid.uuid4()}/close").status_code == 404


# --- risk after a revert ----------------------------------------------------
def test_cleanup_revert_removes_the_verified_response_discount_and_says_so(run_client, adapter_r, monkeypatch):
    client = run_client
    _, run, incident = _auth_incident(client, adapter_r, monkeypatch)
    _verified_response(client, incident)
    verified = client.get(f"{API}/incidents/{incident['id']}").json()
    assert verified["risk_score"] == 40
    assert "-20: a response action (temporary_rate_limit) was verified as working." in verified["risk_explanation"]

    assert client.post(f"{API}/runs/{run['id']}/cleanup").status_code == 200

    after = client.get(f"{API}/incidents/{incident['id']}").json()
    assert after["responses"][0]["status"] == "reverted"
    assert after["risk_score"] == 60
    assert "-20:" not in after["risk_explanation"]
    assert (
        "+0: a response action (temporary_rate_limit) was verified as working, but it was reverted by "
        "cleanup:manual, so it no longer lowers the score" in after["risk_explanation"]
    )


def test_lab_reset_revert_is_named_in_the_explanation(run_client, adapter_r, monkeypatch):
    client = run_client
    lab, _, incident = _auth_incident(client, adapter_r, monkeypatch)
    _verified_response(client, incident)

    assert client.post(f"{API}/labs/{lab['id']}/reset").status_code == 200

    after = client.get(f"{API}/incidents/{incident['id']}").json()
    assert after["risk_score"] == 60
    assert "reverted by lab.reset" in after["risk_explanation"]
