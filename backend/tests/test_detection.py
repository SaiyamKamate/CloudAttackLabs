"""Unit tests for the Detection Engine and the rule loader."""
import sys
import textwrap
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.detection.engine import DetectionEngine  # noqa: E402
from app.detection.rules import RuleError, load_rule, load_rules  # noqa: E402
from app.models import Alert, Base, Event, Lab, ScenarioRun  # noqa: E402
from app.scenario_engine.engine import ScenarioEngine  # noqa: E402

START = datetime(2026, 11, 13, 14, 0, 0, tzinfo=timezone.utc)
RULES_DIR = ROOT / "rules"


def _naive(value):
    return value.replace(tzinfo=None) if value.tzinfo else value


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def scenario():
    return ScenarioEngine(ROOT / "scenarios", {"shopcloud"}).get("authentication_abuse")


def make_run(session) -> ScenarioRun:
    lab = Lab(name=f"lab-{uuid.uuid4().hex[:8]}")
    session.add(lab)
    session.flush()
    run = ScenarioRun(
        lab_id=lab.id, scenario_id="authentication_abuse", scenario_version="1.0.0", status="running"
    )
    session.add(run)
    session.flush()
    return run


def add_failures(session, run, count, *, spacing_seconds=1, source_ip="172.20.0.5", event_type="authentication_failure"):
    for i in range(count):
        session.add(
            Event(
                timestamp=START + timedelta(seconds=i * spacing_seconds),
                source="application",
                event_type=event_type,
                severity="low",
                source_ip=source_ip,
                resource="shopcloud",
                message="Invalid username or password",
                raw_event_reference=f"docker:shopcloud:{uuid.uuid4()}",
                schema_version="1.0",
                lab_id=run.lab_id,
                run_id=run.id,
            )
        )
    session.flush()


# detection takes threshold 10 and window 60 from the scenario.
def test_scenario_drives_threshold_and_window(scenario):
    assert scenario.detection.rule == "repeated_failed_logins"
    assert scenario.detection.threshold == 10
    assert scenario.detection.window_seconds == 60


def test_nine_failures_do_not_raise_an_alert(session, scenario):
    run = make_run(session)
    add_failures(session, run, 9, spacing_seconds=1)
    alerts = DetectionEngine(RULES_DIR).run_for_run(session, run, scenario)
    assert alerts == []
    assert session.scalar(select(Alert)) is None


def test_ten_failures_within_the_window_raise_one_alert(session, scenario):
    run = make_run(session)
    add_failures(session, run, 10, spacing_seconds=1)  # 10 within ~9s, well inside 60s
    alerts = DetectionEngine(RULES_DIR).run_for_run(session, run, scenario)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.rule_id == "repeated_failed_logins"
    assert alert.event_count == 10
    assert len(alert.events) == 10
    # SQLite returns naive datetimes; compare without tzinfo.
    assert _naive(alert.first_event_at) == _naive(START)
    assert _naive(alert.last_event_at) == _naive(START + timedelta(seconds=9))


def test_twenty_within_the_window_make_one_alert_of_twenty(session, scenario):
    run = make_run(session)
    add_failures(session, run, 20, spacing_seconds=1)
    alerts = DetectionEngine(RULES_DIR).run_for_run(session, run, scenario)
    assert len(alerts) == 1
    assert alerts[0].event_count == 20


def test_ten_failures_spread_over_more_than_the_window_do_not_alert(session, scenario):
    run = make_run(session)
    add_failures(session, run, 10, spacing_seconds=10)  # span 90s > 60s window
    alerts = DetectionEngine(RULES_DIR).run_for_run(session, run, scenario)
    assert alerts == []


def test_successful_logins_never_raise_this_alert(session, scenario):
    run = make_run(session)
    add_failures(session, run, 20, event_type="authentication_success")
    alerts = DetectionEngine(RULES_DIR).run_for_run(session, run, scenario)
    assert alerts == []


def test_one_alert_per_source_ip(session, scenario):
    run = make_run(session)
    add_failures(session, run, 10, source_ip="172.20.0.5")
    add_failures(session, run, 10, source_ip="172.20.0.9")
    alerts = DetectionEngine(RULES_DIR).run_for_run(session, run, scenario)
    assert len(alerts) == 2
    assert {a.event_count for a in alerts} == {10}


def test_detection_is_not_duplicated_on_a_second_pass(session, scenario):
    run = make_run(session)
    add_failures(session, run, 10)
    engine = DetectionEngine(RULES_DIR)
    assert len(engine.run_for_run(session, run, scenario)) == 1
    assert engine.run_for_run(session, run, scenario) == []  # already alerted
    assert len(list(session.scalars(select(Alert)))) == 1


# --- rule loader ----------------------------------------------------------
def test_shipped_rule_loads():
    rules = load_rules(RULES_DIR)
    assert "repeated_failed_logins" in rules
    assert rules["repeated_failed_logins"].event_type == "authentication_failure"


def test_invalid_rule_is_skipped(tmp_path):
    (tmp_path / "repeated_failed_logins.yaml").write_text((RULES_DIR / "repeated_failed_logins.yaml").read_text())
    (tmp_path / "broken.yaml").write_text("id: broken\n")  # missing required fields
    loaded = load_rules(tmp_path)
    assert set(loaded) == {"repeated_failed_logins"}


def test_rule_id_must_match_filename(tmp_path):
    data = yaml.safe_load((RULES_DIR / "repeated_failed_logins.yaml").read_text())
    path = tmp_path / "other_name.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(RuleError, match="must match the file name"):
        load_rule(path)
