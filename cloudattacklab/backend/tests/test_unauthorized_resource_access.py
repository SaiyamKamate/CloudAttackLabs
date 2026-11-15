"""Tests for the unauthorized_resource_access scenario: its definition, its
script, the repeated_access_denied rule and the "still denied" verification.

No Docker: the script is run for real against a tiny local HTTP server that
plays ShopCloud, and the API tests use the fake adapter and fake target.
"""
import json
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.detection.engine import DetectionEngine  # noqa: E402
from app.detection.rules import RuleError, load_rule  # noqa: E402
from app.models import Alert, AuditLog, Event, Lab, ScenarioRun  # noqa: E402
from app.scenario_engine import runner  # noqa: E402
from app.scenario_engine.engine import ScenarioEngine  # noqa: E402
from app.scenario_engine.runner import RunResult  # noqa: E402
from app.scenario_engine.verification import run_verification  # noqa: E402
from app.schemas.scenario import load_scenario  # noqa: E402
from app.telemetry.normalize import RunContext, normalize  # noqa: E402

from conftest import FakeAdapter, FakeTarget  # noqa: E402
# Fixtures and helpers shared with the runs API tests (file-backed DB, background thread).
from test_runs_api import (  # noqa: E402,F401
    API,
    _clear_active_labs,
    adapter,
    client,
    file_engine,
    patch_script,
    rdb,
    running_lab,
    start_run,
    target,
    wait_terminal,
)

SCENARIO_ID = "unauthorized_resource_access"
SCENARIO_FILE = ROOT / "scenarios" / f"{SCENARIO_ID}.yaml"
SCRIPT = ROOT / "scenarios" / "scripts" / f"{SCENARIO_ID}.py"
RULES_DIR = ROOT / "rules"
START = datetime(2026, 11, 13, 14, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def scenario():
    return ScenarioEngine(ROOT / "scenarios", {"shopcloud"}).get(SCENARIO_ID)


# --- the scenario file ------------------------------------------------------
def test_scenario_file_is_valid_under_the_existing_schema():
    scenario = load_scenario(SCENARIO_FILE, allowed_targets={"shopcloud"})
    assert scenario.target == "shopcloud"
    assert scenario.type == "application"
    assert scenario.requires == ["protected_resource"]
    assert [(p.check, p.detail) for p in scenario.prerequisites] == [
        ("http_health", None),
        ("test_account", "lab_restricted"),
    ]
    assert scenario.execution.parameters["attempts"] == 8
    assert scenario.execution.parameters["interval_seconds"] == 1
    assert [(e.source, e.event_type) for e in scenario.expected_telemetry] == [
        ("application", "authorization_denied")
    ]
    assert (scenario.detection.rule, scenario.detection.threshold, scenario.detection.window_seconds) == (
        "repeated_access_denied",
        5,
        60,
    )
    assert scenario.response is None
    assert scenario.cleanup.steps == ["reset_target_state"]


def test_both_scenarios_are_listed():
    engine = ScenarioEngine(ROOT / "scenarios", {"shopcloud"})
    assert [s.id for s in engine.list()] == ["authentication_abuse", SCENARIO_ID]


# --- the rule ---------------------------------------------------------------
def test_rule_loads_and_the_first_rule_is_unchanged():
    rule = load_rule(RULES_DIR / "repeated_access_denied.yaml")
    assert rule.event_type == "authorization_denied"
    assert rule.group_fields == ["username", "raw.path"]
    assert rule.match == {"raw.status": 403}

    old = load_rule(RULES_DIR / "repeated_failed_logins.yaml")
    assert old.group_by == "source_ip" and old.group_fields == ["source_ip"] and old.match == {}


def test_rule_with_an_unknown_field_is_rejected(tmp_path):
    path = tmp_path / "bad_rule.yaml"
    path.write_text(
        "id: bad_rule\nevent_type: authorization_denied\ngroup_by: [username, password]\n"
        "title: Bad rule\ndescription: Groups by a field rules may not read.\n",
        encoding="utf-8",
    )
    with pytest.raises(RuleError):
        load_rule(path)


def make_run(session) -> ScenarioRun:
    lab = Lab(name=f"lab-{uuid.uuid4().hex[:8]}")
    session.add(lab)
    session.flush()
    run = ScenarioRun(lab_id=lab.id, scenario_id=SCENARIO_ID, scenario_version="1.0.0", status="running")
    session.add(run)
    session.flush()
    return run


def log_record(event, status, *, username, path="/api/orders", offset=0, method="GET"):
    """One ShopCloud log record, as docs/shopcloud-interface.md section 5 describes it."""
    return {
        "timestamp": (START + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z"),
        "service": "shopcloud",
        "level": "warning" if status >= 400 else "info",
        "event": event,
        "request_id": str(uuid.uuid4()),
        "source_ip": "172.20.0.5",
        "method": method,
        "path": path,
        "status": status,
        "username": username,
        "message": event,
    }


def add_records(session, run, records):
    """Store records the way the pipeline does: through the real normalizer."""
    ctx = RunContext(run_id=run.id, lab_id=run.lab_id, scenario_id=SCENARIO_ID, started_at=START)
    for record in records:
        event = normalize(record, ctx)
        session.add(Event(**event.model_dump(), raw=record))
    session.flush()


def denials(count, *, username="lab_restricted", status=403, path="/api/orders", start=0, spacing=1):
    return [
        log_record("access_denied", status, username=username, path=path, offset=start + i * spacing)
        for i in range(count)
    ]


def detect(db, run, scenario):
    return DetectionEngine(RULES_DIR).run_for_run(db, run, scenario)


def test_four_denials_raise_no_alert(db, scenario):
    run = make_run(db)
    add_records(db, run, denials(4))
    assert detect(db, run, scenario) == []


def test_five_denials_raise_one_alert(db, scenario):
    run = make_run(db)
    add_records(db, run, denials(5))
    alerts = detect(db, run, scenario)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.rule_id == "repeated_access_denied"
    assert alert.event_count == 5
    assert "username=lab_restricted" in alert.title and "raw.path=/api/orders" in alert.title
    assert {e.username for e in alert.events} == {"lab_restricted"}
    assert alert.incident_id is not None  # linked to the run's incident like any alert


def test_eight_denials_make_one_alert_of_eight_and_no_duplicate(db, scenario):
    run = make_run(db)
    add_records(db, run, denials(8))
    alerts = detect(db, run, scenario)
    assert [a.event_count for a in alerts] == [8]
    assert detect(db, run, scenario) == []  # a second pass adds nothing
    assert len(list(db.scalars(select(Alert).where(Alert.run_id == run.id)))) == 1


def test_401_without_a_token_does_not_count(db, scenario):
    run = make_run(db)
    # Ten 401s with no token (no username), plus four real 403 denials: still under 5.
    add_records(db, run, denials(10, username=None, status=401) + denials(4, start=10))
    assert detect(db, run, scenario) == []


def test_401_does_not_count_even_with_a_username(db, scenario):
    run = make_run(db)
    add_records(db, run, denials(10, status=401))
    assert detect(db, run, scenario) == []


def test_lab_endpoint_403_without_a_user_does_not_count(db, scenario):
    run = make_run(db)
    add_records(db, run, denials(10, username=None, path="/lab/rate-limit"))
    assert detect(db, run, scenario) == []


def test_denials_are_counted_per_user_and_per_resource(db, scenario):
    run = make_run(db)
    # Four on one path and four on another: 8 denials, but no single resource reaches 5.
    add_records(db, run, denials(4) + denials(4, path="/api/admin", start=4))
    assert detect(db, run, scenario) == []
    # Two users with four each on the same path: neither reaches 5.
    other = make_run(db)
    add_records(db, other, denials(4) + denials(4, username="someone_else", start=4))
    assert detect(db, other, scenario) == []


def test_five_denials_spread_over_more_than_the_window_do_not_alert(db, scenario):
    run = make_run(db)
    add_records(db, run, denials(5, spacing=20))  # 80 seconds
    assert detect(db, run, scenario) == []


def test_lab_user_reading_orders_normally_raises_nothing(db, scenario):
    run = make_run(db)
    records = [log_record("login_success", 200, username="lab_user", path="/api/login", method="POST")]
    records += [log_record("access_granted", 200, username="lab_user", offset=i + 1) for i in range(20)]
    add_records(db, run, records)
    assert detect(db, run, scenario) == []


# --- the script, run for real against a local stand-in for ShopCloud --------
class _Shop(BaseHTTPRequestHandler):
    """Just enough of ShopCloud: login for lab_restricted, and GET /api/orders."""

    orders_status = 403     # what the protected resource answers
    seen_tokens: list = []

    def log_message(self, *args):  # keep test output quiet
        pass

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/api/login" and body == {"username": "lab_restricted", "password": "lab-restricted-pass"}:
            return self._send(200, {"token": "test-token", "username": "lab_restricted", "role": "restricted"})
        return self._send(401, {"error": "invalid_credentials"})

    def do_GET(self):
        type(self).seen_tokens.append(self.headers.get("Authorization"))
        if self.headers.get("Authorization") != "Bearer test-token":
            return self._send(401, {"error": "not_authenticated"})
        status = type(self).orders_status
        return self._send(status, {"orders": []} if status == 200 else {"error": "forbidden"})


@pytest.fixture
def shop():
    _Shop.orders_status = 403
    _Shop.seen_tokens = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Shop)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


PARAMS = {"username": "lab_restricted", "attempts": 8, "interval_seconds": 0}


def test_script_counts_eight_denials(shop):
    result = runner.execute_script(SCRIPT, shop, PARAMS, timeout_seconds=30)
    assert result.outcome == "completed", result.error
    summary = result.summary
    assert summary["attempts_sent"] == 8
    assert summary["denied_403"] == 8
    assert summary["other"] == 0
    assert summary["access_control_failed"] is False
    assert _Shop.seen_tokens == ["Bearer test-token"] * 8  # it used the token it logged in with
    assert "test-token" not in result.stdout              # and never printed it


def test_script_fails_the_run_when_a_request_returns_200(shop):
    _Shop.orders_status = 200  # the restriction is broken
    result = runner.execute_script(SCRIPT, shop, PARAMS, timeout_seconds=30)
    assert result.outcome == "failed"
    assert "access control failed" in result.error
    assert result.summary["access_control_failed"] is True
    assert result.summary["granted_200"] == 8
    assert result.summary["denied_403"] == 0
    assert result.summary["other"] == 8


def test_script_fails_when_refusals_are_not_403(shop):
    _Shop.orders_status = 500
    result = runner.execute_script(SCRIPT, shop, PARAMS, timeout_seconds=30)
    assert result.outcome == "failed"
    assert result.summary["other"] == 8
    assert result.summary["access_control_failed"] is False  # nothing was granted


def test_script_fails_when_it_cannot_log_in(shop):
    result = runner.execute_script(SCRIPT, shop, {**PARAMS, "username": "nobody"}, timeout_seconds=30)
    assert result.outcome == "failed"
    assert result.summary["attempts_sent"] == 0


# --- verification -----------------------------------------------------------
ACCOUNTS = {"lab_user": "lab-user-pass", "lab_restricted": "lab-restricted-pass"}


def _running_target():
    fake_adapter = FakeAdapter()
    fake_adapter.start()
    return FakeTarget(fake_adapter)


def test_verification_records_that_access_is_still_denied(scenario):
    fake = _running_target()
    result = run_verification(scenario, fake, ACCOUNTS)
    assert result["check"] == "access_remains_denied"
    assert result["passed"] is True and result["still_denied"] is True
    assert result["sent"] == {"request": "GET /api/orders", "username": "lab_restricted"}
    assert result["received"] == {"login_status": 200, "status": 403}
    assert fake.order_reads == ["lab_restricted"]  # exactly one more request


def test_verification_reports_when_access_is_no_longer_denied(scenario):
    fake = _running_target()
    fake.restriction_broken = True
    result = run_verification(scenario, fake, ACCOUNTS)
    assert result["passed"] is False and result["still_denied"] is False
    assert result["received"]["status"] == 200


def test_verification_that_cannot_reach_the_target_is_not_passed(scenario):
    fake = _running_target()
    fake.fail_orders = True
    result = run_verification(scenario, fake, ACCOUNTS)
    assert result["passed"] is False and result["still_denied"] is False
    assert "ConnectError" in result["error"]


def test_authentication_abuse_has_no_post_run_verification():
    other = ScenarioEngine(ROOT / "scenarios", {"shopcloud"}).get("authentication_abuse")
    assert run_verification(other, _running_target(), ACCOUNTS) is None


# --- through the API, with the fake lab -------------------------------------
def _now_records(count):
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    records = denials(count)
    for i, record in enumerate(records):
        record["timestamp"] = (now + timedelta(milliseconds=10 * i)).isoformat().replace("+00:00", "Z")
    return "\n".join(json.dumps(record) for record in records)


def good_summary():
    return {"scenario": SCENARIO_ID, "attempts_sent": 8, "denied_403": 8, "other": 0, "access_control_failed": False}


def test_run_raises_the_alert_and_records_the_verification(client, rdb, adapter, target, monkeypatch):
    calls = []
    patch_script(monkeypatch, RunResult("completed", good_summary(), None), record=calls)
    lab = running_lab(client)
    adapter.log_text = _now_records(8)

    response = start_run(client, lab["id"], SCENARIO_ID)
    assert response.status_code == 201, response.text
    run = wait_terminal(client, response.json()["id"])

    assert run["status"] == "completed", run
    assert run["summary"]["denied_403"] == 8
    assert run["summary"]["verification"]["still_denied"] is True
    assert calls[0]["parameters"]["attempts"] == 8
    assert target.order_reads == ["lab_restricted"]
    # Pre-flight logged in as lab_restricted, the scenario's test account.
    assert ("lab_restricted", "lab-restricted-pass") in target.logins

    alerts = client.get(f"{API}/alerts", params={"run_id": run["id"]}).json()
    assert [a["rule_id"] for a in alerts] == ["repeated_access_denied"]
    detail = client.get(f"{API}/alerts/{alerts[0]['id']}").json()
    assert len(detail["events"]) == 8
    assert {e["event_type"] for e in detail["events"]} == {"authorization_denied"}

    # No response action is defined for this scenario.
    refused = client.post(f"{API}/incidents/{alerts[0]['incident_id']}/responses")
    assert refused.status_code == 409 and refused.json()["error"] == "no_response_defined"

    # Cleanup is reset_target_state only.
    resets_before = target.reset_calls
    cleaned = client.post(f"{API}/runs/{run['id']}/cleanup").json()
    assert cleaned["status"] == "cleaned_up"
    assert target.reset_calls == resets_before + 1
    assert target.rate_limit_calls == []


def test_a_200_response_fails_the_run_and_keeps_the_summary(client, rdb, target, monkeypatch):
    broken = {**good_summary(), "denied_403": 0, "other": 8, "granted_200": 8, "access_control_failed": True}
    patch_script(
        monkeypatch,
        RunResult("failed", broken, "script exited with code 2: access control failed: 8 of 8 requests returned 200"),
    )
    target.restriction_broken = True
    lab = running_lab(client)

    run = wait_terminal(client, start_run(client, lab["id"], SCENARIO_ID).json()["id"])

    assert run["status"] == "failed"
    assert "access control failed" in run["error"]
    assert run["summary"]["access_control_failed"] is True
    assert run["summary"]["verification"]["still_denied"] is False
    assert target.reset_calls >= 1  # cleanup ran automatically after the failure
    failure = rdb.scalars(select(AuditLog).where(AuditLog.action == "run.failure")).one()
    assert failure.detail["summary"]["access_control_failed"] is True


def test_authentication_abuse_run_is_unchanged(client, target, monkeypatch):
    summary = {"scenario": "authentication_abuse", "attempts_sent": 20, "unauthorized_401": 20, "rate_limited_429": 0}
    patch_script(monkeypatch, RunResult("completed", summary, None))
    lab = running_lab(client)
    run = wait_terminal(client, start_run(client, lab["id"]).json()["id"])
    assert run["status"] == "completed"
    assert run["summary"] == summary        # no verification added
    assert target.order_reads == []         # and no extra request sent
