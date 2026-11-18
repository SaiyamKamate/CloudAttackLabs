"""Run report: everything about one scenario run, as one JSON document.

Built on request from what is already stored (the run, its events, alerts,
incidents, responses and the audit log). Nothing is stored for a report, and
it is JSON only: no PDF export and no sharing.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..detection.checks import RuleCheck, run_checks
from ..errors import ApiError
from ..incidents.timeline import as_utc
from ..models import Alert, AuditLog, Event, Incident, Lab, ScenarioRun
from ..scenario_engine.engine import ScenarioEngine
from ..schemas.scenario import ScenarioDefinition

REPORT_VERSION = "1.0"


def build_report(session: Session, engine: ScenarioEngine, run: ScenarioRun) -> dict[str, Any]:
    lab = session.get(Lab, run.lab_id)
    scenario = _scenario(engine, run.scenario_id)
    summary = dict(run.summary or {})
    post_run_verification = summary.pop("verification", None)

    events = list(session.scalars(select(Event).where(Event.run_id == run.id)))
    alerts = list(session.scalars(select(Alert).where(Alert.run_id == run.id).order_by(Alert.created_at)))
    incidents = list(
        session.scalars(select(Incident).where(Incident.run_id == run.id).order_by(Incident.created_at))
    )
    responses = sorted(
        (response for incident in incidents for response in incident.responses), key=lambda r: r.requested_at
    )
    checks = run_checks(scenario, run.parameters) if scenario else []
    thresholds = {check.rule: check.threshold for check in checks}

    return {
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc),
        "lab": None
        if lab is None
        else {
            "id": lab.id,
            "name": lab.name,
            "target": lab.target,
            "environment": lab.environment,
            "status": lab.status,
        },
        "scenario": {
            "id": run.scenario_id,
            "version": run.scenario_version,  # the version that was run
            "definition_available": scenario is not None,
            "name": scenario.name if scenario else None,
            "category": scenario.category if scenario else None,
            "description": scenario.description.strip() if scenario else None,
            "target": scenario.target if scenario else None,
            "type": scenario.type if scenario else None,
        },
        "run": {
            "id": run.id,
            "status": run.status,
            "parameters": run.parameters,
            "started_at": _utc(run.started_at),
            "finished_at": _utc(run.finished_at),
            "duration_seconds": _seconds(run.started_at, run.finished_at),
            "error": run.error,
        },
        "script_summary": summary or None,
        "telemetry": _telemetry(scenario, events),
        "detection": _detection(scenario, checks),
        "alerts": [_alert(alert, thresholds.get(alert.rule_id)) for alert in alerts],
        "incidents": [
            {
                "id": incident.id,
                "title": incident.title,
                "status": incident.status,
                "correlation_key": incident.correlation_key,
                "risk_score": incident.risk_score,
                "risk_explanation": incident.risk_explanation,
                "alert_ids": [alert.id for alert in sorted(incident.alerts, key=lambda a: as_utc(a.created_at))],
            }
            for incident in incidents
        ],
        "responses": [
            {
                "id": response.id,
                "incident_id": response.incident_id,
                "action": response.action,
                "parameters": response.parameters,
                "status": response.status,
                "requested_at": _utc(response.requested_at),
                "decided_at": _utc(response.decided_at),
                "decided_by": response.decided_by,
                "executed_at": _utc(response.executed_at),
                "verified_at": _utc(response.verified_at),
                "verification_result": response.verification_result,
            }
            for response in responses
        ],
        "post_run_verification": post_run_verification,
        "cleanup": _cleanup(session, run),
    }


def _detection(scenario: Optional[ScenarioDefinition], checks: list[RuleCheck]) -> Optional[dict[str, Any]]:
    """The rule(s) the run was checked against.

    `rule`, `threshold` and `window_seconds` describe an attack scenario's one
    rule (null for an observation run); `rules` lists every rule checked.
    """
    if scenario is None:
        return None
    single = checks[0] if scenario.detection is not None and checks else None
    return {
        "rule": single.rule if single else None,
        "threshold": single.threshold if single else None,
        "window_seconds": single.window_seconds if single else None,
        "rules": [
            {"rule": c.rule, "threshold": c.threshold, "window_seconds": c.window_seconds, "taken_from": c.taken_from}
            for c in checks
        ],
    }


def _scenario(engine: ScenarioEngine, scenario_id: str) -> Optional[ScenarioDefinition]:
    """The current definition, or None if the scenario file is gone or no longer valid."""
    try:
        return engine.get(scenario_id)
    except ApiError:
        return None


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    return as_utc(value) if value is not None else None


def _seconds(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    if start is None or end is None:
        return None
    return round((as_utc(end) - as_utc(start)).total_seconds(), 3)


def _telemetry(scenario: Optional[ScenarioDefinition], events: list[Event]) -> dict[str, Any]:
    """Expected telemetry (from the scenario) next to what was actually collected."""
    collected = Counter((event.source, event.event_type) for event in events)
    expected = []
    for item in scenario.expected_telemetry if scenario else []:
        count = collected.get((item.source, item.event_type), 0)
        expected.append(
            {
                "source": item.source,
                "event_type": item.event_type,
                "expected_min_count": item.min_count,
                "collected_count": count,
                "met": count >= item.min_count,
            }
        )
    return {
        "expected": expected,
        "all_expected_met": all(item["met"] for item in expected) if expected else None,
        "collected_total": len(events),
        "collected_by_type": [
            {"source": source, "event_type": event_type, "count": count}
            for (source, event_type), count in sorted(collected.items())
        ],
    }


def _alert(alert: Alert, threshold: Optional[int]) -> dict[str, Any]:
    """An alert with its detection latency.

    Latency is the time from the event that crossed the threshold (the
    `threshold`-th event linked to the alert, in time order) to the moment the
    alert was created.
    """
    events = sorted(alert.events, key=lambda event: as_utc(event.timestamp))
    crossing = events[threshold - 1] if threshold and len(events) >= threshold else None
    return {
        "id": alert.id,
        "rule_id": alert.rule_id,
        "severity": alert.severity,
        "title": alert.title,
        "description": alert.description,
        "incident_id": alert.incident_id,
        "event_count": alert.event_count,
        "first_event_at": _utc(alert.first_event_at),
        "last_event_at": _utc(alert.last_event_at),
        "created_at": _utc(alert.created_at),
        "threshold_crossed_at": _utc(crossing.timestamp) if crossing else None,
        "threshold_event_id": crossing.event_id if crossing else None,
        "threshold_event_reference": crossing.raw_event_reference if crossing else None,
        "detection_latency_seconds": _seconds(crossing.timestamp, alert.created_at) if crossing else None,
    }


def _cleanup(session: Session, run: ScenarioRun) -> dict[str, Any]:
    rows = list(
        session.scalars(
            select(AuditLog)
            .where(
                AuditLog.object_type == "scenario_run",
                AuditLog.object_id == str(run.id),
                AuditLog.action == "cleanup",
            )
            .order_by(AuditLog.id)
        )
    )
    runs = [
        {
            "time": _utc(row.timestamp),
            "trigger": (row.detail or {}).get("trigger"),
            "steps_done": (row.detail or {}).get("steps_done", []),
            "steps_failed": (row.detail or {}).get("steps_failed", {}),
            "responses_reverted": (row.detail or {}).get("responses_reverted", []),
            "outcome": (row.detail or {}).get("outcome"),
        }
        for row in rows
    ]
    if not runs:
        status = "not_run"
    else:
        status = "done" if runs[-1]["outcome"] == "success" else "partial"
    return {"status": status, "runs": runs}
