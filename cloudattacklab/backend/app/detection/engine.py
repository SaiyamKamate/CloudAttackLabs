"""Run a scenario's detection rule over a run's events and raise alerts.

One alert per run and group value (e.g. source_ip), not one per event. Every
contributing event is linked to the alert so it can be explained from evidence.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Alert, Event, ScenarioRun
from ..schemas.scenario import ScenarioDefinition
from .rules import RuleDefinition, load_rules

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
        groups: dict[str, list[Event]] = defaultdict(list)
        for event in events:
            key = getattr(event, rule.group_by)
            if key is not None:
                groups[str(key)].append(event)

        already = _groups_already_alerted(session, run, rule.id, rule.group_by)
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


def _build_alert(run, rule, detection, group_value, contributing) -> Alert:
    first = contributing[0].timestamp
    last = contributing[-1].timestamp
    span = int((last - first).total_seconds())
    return Alert(
        rule_id=rule.id,
        severity=rule.severity,
        title=f"{rule.title} ({rule.group_by}={group_value})",
        description=(
            f"{len(contributing)} {rule.event_type} events from {rule.group_by}={group_value} "
            f"within {span}s (threshold {detection.threshold} in {detection.window_seconds}s)."
        ),
        lab_id=run.lab_id,
        run_id=run.id,
        event_count=len(contributing),
        first_event_at=first,
        last_event_at=last,
        events=list(contributing),
    )


def _groups_already_alerted(session: Session, run: ScenarioRun, rule_id: str, group_by: str) -> set[str]:
    """Group values that already have an alert for this run+rule (so we don't duplicate)."""
    alerts = session.scalars(select(Alert).where(Alert.run_id == run.id, Alert.rule_id == rule_id))
    seen: set[str] = set()
    for alert in alerts:
        for event in alert.events:
            value = getattr(event, group_by)
            if value is not None:
                seen.add(str(value))
    return seen
