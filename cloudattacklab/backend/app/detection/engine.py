"""Run a scenario's detection rule over a run's events and raise alerts.

One alert per run and group (one source_ip, or one username on one path),
not one per event. Every
contributing event is linked to the alert so it can be explained from evidence.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..incidents.service import attach_alerts
from ..models import Alert, Event, ScenarioRun
from ..schemas.scenario import ScenarioDefinition
from .rules import RuleDefinition, field_value, load_rules

log = logging.getLogger("cloudattacklab.detection")


class DetectionEngine:
    def __init__(self, rules_dir: Path) -> None:
        self.rules_dir = Path(rules_dir)

    def rules(self) -> dict[str, RuleDefinition]:
        return load_rules(self.rules_dir)

    def run_for_run(self, session: Session, run: ScenarioRun, scenario: ScenarioDefinition) -> list[Alert]:
        """Evaluate the scenario's detection rule over this run's events. Returns new alerts."""
        detection = scenario.detection
        rule = self.rules().get(detection.rule)
        if rule is None:
            log.warning("run %s: detection rule '%s' not found; no alert", run.id, detection.rule)
            return []

        threshold = detection.threshold
        window = timedelta(seconds=detection.window_seconds)

        events = list(
            session.scalars(
                select(Event)
                .where(Event.run_id == run.id, Event.event_type == rule.event_type)
                .order_by(Event.timestamp)
            )
        )
        groups: dict[tuple[str, ...], list[Event]] = defaultdict(list)
        for event in events:
            if not _matches(event, rule):
                continue
            key = _group_key(event, rule)
            if key is not None:
                groups[key].append(event)

        already = _groups_already_alerted(session, run, rule)
        new_alerts: list[Alert] = []
        for key, group in groups.items():
            if key in already:
                continue  # one alert per run and group value
            contributing = _first_window_reaching(group, threshold, window)
            if contributing is None:
                continue
            new_alerts.append(_build_alert(run, rule, detection, key, contributing))

        session.add_all(new_alerts)
        if new_alerts:
            attach_alerts(session, run, scenario, new_alerts)  # one incident per run
            session.commit()
        return new_alerts


def _first_window_reaching(events: list[Event], threshold: int, window: timedelta) -> list[Event] | None:
    """The events of the first time-window that holds at least `threshold` of them, else None.

    events must be sorted by timestamp. Returns every event within `window` of
    that window's start, so the alert carries all the evidence from the burst.
    """
    if len(events) < threshold:
        return None
    times = [event.timestamp for event in events]
    start = 0
    for end in range(len(events)):
        while times[end] - times[start] > window:
            start += 1
        if end - start + 1 >= threshold:
            window_end = times[start] + window
            return [event for event in events if times[start] <= event.timestamp <= window_end]
    return None


def _matches(event: Event, rule: RuleDefinition) -> bool:
    """True if the event has every value the rule's `match` asks for."""
    return all(field_value(event, name) == wanted for name, wanted in rule.match.items())


def _group_key(event: Event, rule: RuleDefinition) -> tuple[str, ...] | None:
    """The event's group: one value per `group_by` field. None if any is missing."""
    values = [field_value(event, name) for name in rule.group_fields]
    if any(value is None for value in values):
        return None
    return tuple(str(value) for value in values)


def _build_alert(run, rule, detection, key, contributing) -> Alert:
    # e.g. "source_ip=172.20.0.5" or "username=lab_restricted, raw.path=/api/orders"
    group = ", ".join(f"{name}={value}" for name, value in zip(rule.group_fields, key))
    first = contributing[0].timestamp
    last = contributing[-1].timestamp
    span = int((last - first).total_seconds())
    return Alert(
        rule_id=rule.id,
        severity=rule.severity,
        title=f"{rule.title} ({group})",
        description=(
            f"{len(contributing)} {rule.event_type} events from {group} "
            f"within {span}s (threshold {detection.threshold} in {detection.window_seconds}s)."
        ),
        lab_id=run.lab_id,
        run_id=run.id,
        event_count=len(contributing),
        first_event_at=first,
        last_event_at=last,
        events=list(contributing),
    )


def _groups_already_alerted(session: Session, run: ScenarioRun, rule: RuleDefinition) -> set[tuple[str, ...]]:
    """Groups that already have an alert for this run+rule (so we don't duplicate)."""
    alerts = session.scalars(select(Alert).where(Alert.run_id == run.id, Alert.rule_id == rule.id))
    seen: set[tuple[str, ...]] = set()
    for alert in alerts:
        for event in alert.events:
            key = _group_key(event, rule)
            if key is not None:
                seen.add(key)
    return seen
