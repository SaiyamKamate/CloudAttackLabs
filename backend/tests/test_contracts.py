"""Tests for the Week 1 contracts. Run from the repo root:  pytest backend/tests"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.schemas.event import NormalizedEvent  # noqa: E402
from app.schemas.scenario import ScenarioError, load_scenario  # noqa: E402

SCENARIO = ROOT / "scenarios" / "authentication_abuse.yaml"
TARGETS = {"shopcloud"}


def make_event(**overrides):
    data = dict(
        timestamp="2026-11-13T14:00:00Z",
        source="application",
        event_type="authentication_failure",
        severity="low",
        source_ip="172.20.0.5",
        resource="shopcloud",
        message="Failed login for lab_user",
        raw_event_reference="docker:shopcloud:req-123",
    )
    data.update(overrides)
    return NormalizedEvent(**data)


# --- event schema ---------------------------------------------------------
def test_valid_event():
    event = make_event()
    assert event.event_id is not None
    assert event.timestamp.tzinfo == timezone.utc


def test_timestamp_converted_to_utc():
    event = make_event(timestamp="2026-11-13T19:30:00+05:30")
    assert event.timestamp == datetime(2026, 11, 13, 14, 0, tzinfo=timezone.utc)


def test_timestamp_without_timezone_rejected():
    with pytest.raises(ValidationError):
        make_event(timestamp="2026-11-13T14:00:00")


@pytest.mark.parametrize(
    "field,value",
    [("source_ip", "not-an-ip"), ("event_type", "made_up"), ("severity", "urgent"), ("resource", "")],
)
def test_bad_values_rejected(field, value):
    with pytest.raises(ValidationError):
        make_event(**{field: value})


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        make_event(surprise="x")


# --- scenario format ------------------------------------------------------
def test_shipped_scenario_is_valid():
    scenario = load_scenario(SCENARIO, TARGETS)
    assert scenario.id == "authentication_abuse"
    assert scenario.response.requires_approval is True


def _write_variant(tmp_path, change):
    data = yaml.safe_load(SCENARIO.read_text())
    change(data)
    path = tmp_path / "authentication_abuse.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["execution"]["parameters"].update(attempts=100000),       # unbounded attempts
        lambda d: d["execution"]["parameters"].update(interval_seconds=0),     # flooding
        lambda d: d["execution"].update(script="../../evil.py"),               # path escape
        lambda d: d["response"].update(requires_approval=False),               # no approval
        lambda d: d["cleanup"].update(enabled=False),                          # no cleanup
        lambda d: d.pop("cleanup"),                                            # missing cleanup
        lambda d: d.update(id="something_else"),                               # id != file name
        lambda d: d.update(extra_field=1),                                     # unknown field
    ],
)
def test_unsafe_or_malformed_scenarios_rejected(tmp_path, change):
    with pytest.raises(ScenarioError):
        load_scenario(_write_variant(tmp_path, change), TARGETS)


def test_target_outside_lab_rejected():
    with pytest.raises(ScenarioError, match="not a configured lab target"):
        load_scenario(SCENARIO, {"some_other_app"})


def test_missing_file():
    with pytest.raises(ScenarioError, match="not found"):
        load_scenario(ROOT / "scenarios" / "nope.yaml", TARGETS)
