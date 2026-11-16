"""Incident timeline: one time-ordered list of what happened.

It mixes, in time order: the scenario starting, the events behind the alerts,
each alert firing, the scenario finishing, every response step (requested,
approved or rejected, executed, verified or failed, reverted) and cleanup.

Every entry has a time, a type, a short description and a link to its evidence.
Nothing is stored: the list is built from the run, the events, the alerts and
the audit log each time it is asked for.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditLog, Incident, ScenarioRun

API = "/api/v1"

# audit_log action -> timeline entry type
RESPONSE_STEPS = {
    "response.requested": "response_requested",
    "response.approved": "response_approved",
    "response.rejected": "response_rejected",
    "response.executed": "response_executed",
    "response.verified": "response_verified",
    "response.failed": "response_failed",
    "response.reverted": "response_reverted",
}
RUN_STEPS = {"run.finish": "scenario_finished", "run.failure": "scenario_finished", "cleanup": "cleanup"}


def as_utc(value: datetime) -> datetime:
    """SQLite gives back times without a timezone; they were stored as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def build_timeline(session: Session, incident: Incident) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    run: Optional[ScenarioRun] = session.get(ScenarioRun, incident.run_id) if incident.run_id else None
    if run is not None and run.started_at is not None:
        entries.append(
            _entry(
                run.started_at,
                "scenario_started",
                f"Scenario {run.scenario_id} (version {run.scenario_version}) started.",
                kind="run",
                id=run.id,
                href=f"{API}/runs/{run.id}",
            )
        )

    alerts = sorted(incident.alerts, key=lambda alert: as_utc(alert.created_at))

    # The events behind the alerts, each one once.
    events = {event.event_id: event for alert in alerts for event in alert.events}
    for event in sorted(events.values(), key=lambda e: as_utc(e.timestamp)):
        who = " ".join(
            part
            for part in (
                f"from {event.source_ip}" if event.source_ip else "",
                f"for {event.username}" if event.username else "",
            )
            if part
        )
        # Name the source first (application, docker, falco), and Falco's rule.
        rule = f" (Falco rule: {event.rule_id})" if event.source == "falco" and event.rule_id else ""
        entries.append(
            _entry(
                event.timestamp,
                "event",
                f"{event.source} event: {event.event_type}{' ' + who if who else ''} on {event.resource}"
                f"{rule}: {event.message}",
                kind="event",
                id=event.event_id,
                href=f"{API}/events/{event.event_id}",
                raw_event_reference=event.raw_event_reference,
                source=event.source,
                rule_id=event.rule_id,
            )
        )

    for alert in alerts:
        entries.append(
            _entry(
                alert.created_at,
                "alert_fired",
                f"Alert {alert.rule_id} fired ({alert.severity}): {alert.description}",
                kind="alert",
                id=alert.id,
                href=f"{API}/alerts/{alert.id}",
            )
        )

    # Response steps and run steps come from the audit log, which has a row for each.
    response_ids = [str(response.id) for response in incident.responses]
    if response_ids:
        rows = session.scalars(
            select(AuditLog)
            .where(AuditLog.object_type == "response_action", AuditLog.object_id.in_(response_ids))
            .order_by(AuditLog.id)
        )
        for row in rows:
            if row.action in RESPONSE_STEPS:
                entries.append(
                    _entry(
                        row.timestamp,
                        RESPONSE_STEPS[row.action],
                        _response_text(row),
                        kind="response",
                        id=row.object_id,
                        href=f"{API}/responses/{row.object_id}",
                    )
                )
    if run is not None:
        rows = session.scalars(
            select(AuditLog)
            .where(AuditLog.object_type == "scenario_run", AuditLog.object_id == str(run.id))
            .order_by(AuditLog.id)
        )
        for row in rows:
            if row.action in RUN_STEPS:
                entries.append(
                    _entry(
                        row.timestamp,
                        RUN_STEPS[row.action],
                        _run_text(row),
                        kind="audit",
                        id=row.id,
                        href=f"{API}/audit?object_type=scenario_run&object_id={run.id}&action={row.action}",
                    )
                )

    # Stable sort: entries with the same time keep the order they were added in.
    entries.sort(key=lambda entry: entry["time"])
    return entries


def _entry(time: datetime, type: str, description: str, *, kind: str, id: Any, href: str, **extra: Any) -> dict:
    return {
        "time": as_utc(time),
        "type": type,
        "description": description,
        "evidence": {"kind": kind, "id": str(id), "href": href, **extra},
    }


def _response_text(row: AuditLog) -> str:
    detail = row.detail or {}
    action = detail.get("action", "response")
    if row.action == "response.requested":
        return f"Response {action} requested; waiting for approval. Nothing was changed on the target."
    if row.action == "response.approved":
        return f"Response {action} approved by {detail.get('decided_by', row.actor)}."
    if row.action == "response.rejected":
        return f"Response {action} rejected by {detail.get('decided_by', row.actor)}. The target was not changed."
    if row.action == "response.executed":
        return f"Response {action} executed: {detail.get('request', 'applied on the target')}."
    if row.action in ("response.verified", "response.failed"):
        word = "verified" if row.action == "response.verified" else "failed"
        received, sent = detail.get("received") or {}, detail.get("sent") or {}
        if "blocked_429" in received and "attempts" in sent:
            return (
                f"Response {action} {word}: {received['blocked_429']} of {sent['attempts']} "
                "follow-up logins were blocked with 429."
            )
        return f"Response {action} {word}" + (f": {detail['error']}" if detail.get("error") else ".")
    return f"Response {action} reverted by {detail.get('trigger', 'cleanup')}; the target is back at its baseline."


def _run_text(row: AuditLog) -> str:
    detail = row.detail or {}
    if row.action == "cleanup":
        done = ", ".join(detail.get("steps_done") or []) or "none"
        failed = ", ".join(detail.get("steps_failed") or {})
        text = f"Cleanup ({detail.get('trigger', 'manual')}) ran. Steps done: {done}."
        return text + (f" Steps failed: {failed}." if failed else "")
    text = f"Scenario finished: {detail.get('outcome', 'unknown')}."
    return text + (f" Error: {detail['error']}" if detail.get("error") else "")
