"""Incidents: group a run's alerts into incidents (correlation).

The correlation rules, kept deliberately simple:

1. Scenario: only alerts of the same scenario run can share an incident. Two
   scenarios run one after the other are two runs, so always two incidents.
2. Source and resource: inside a run, two alerts are related when the events
   behind them share a resource AND share either a source_ip or a username.
3. A new alert joins the oldest incident of its run that already holds a
   related alert. If there is none, it opens a new incident.
4. Incidents are never merged or split afterwards.

Time needs no rule of its own: a run is one bounded burst of activity, and each
alert already covers one detection window inside it.

`correlation_key` on the incident records what opened it, for example
`run:<run_id>|resource:shopcloud|ip:172.20.0.5` (or `|user:<name>` when the
events carry no source IP).

Evidence chain: incident -> alerts -> alert_events -> events -> raw_event_reference.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Alert, Incident, ScenarioRun
from ..schemas.scenario import ScenarioDefinition
from .risk import refresh_risk


@dataclass(frozen=True)
class AlertContext:
    """Who and what an alert is about, read from the events linked to it."""

    resources: frozenset[str]
    source_ips: frozenset[str]
    usernames: frozenset[str]


def alert_context(alert: Alert) -> AlertContext:
    events = alert.events
    return AlertContext(
        resources=frozenset(e.resource for e in events if e.resource),
        source_ips=frozenset(e.source_ip for e in events if e.source_ip),
        usernames=frozenset(e.username for e in events if e.username),
    )


def related(a: AlertContext, b: AlertContext) -> bool:
    """Rule 2: same resource, and the same source_ip or the same username."""
    same_resource = bool(a.resources & b.resources)
    same_actor = bool(a.source_ips & b.source_ips) or bool(a.usernames & b.usernames)
    return same_resource and same_actor


def attach_alerts(
    session: Session,
    run: ScenarioRun,
    scenario: ScenarioDefinition,
    alerts: list[Alert],
    *,
    threshold: Optional[int] = None,
) -> list[Incident]:
    """Put each new alert into an incident and refresh those incidents' risk scores.

    threshold: the detection threshold the alerts were raised with (default:
    the scenario's own). Returns the incidents that received an alert. The
    caller commits.
    """
    if threshold is None and scenario.detection is not None:
        threshold = scenario.detection.threshold
    if not alerts:
        return []
    incidents = list(
        session.scalars(select(Incident).where(Incident.run_id == run.id).order_by(Incident.created_at))
    )
    touched: list[Incident] = []
    for alert in alerts:
        context = alert_context(alert)
        incident = next(
            (i for i in incidents if any(related(context, alert_context(other)) for other in i.alerts)),
            None,
        )
        if incident is None:
            incident = Incident(
                lab_id=run.lab_id,
                run_id=run.id,
                title=_title(scenario, context),
                correlation_key=_correlation_key(run, context),
            )
            session.add(incident)
            incidents.append(incident)
        alert.incident = incident
        if incident not in touched:
            touched.append(incident)

    session.flush()
    for incident in touched:
        refresh_risk(session, incident, threshold)
    return touched


def _actor(context: AlertContext) -> tuple[str, str] | None:
    """The source IP the alert is about, or the username when there is no IP."""
    if context.source_ips:
        return "ip", sorted(context.source_ips)[0]
    if context.usernames:
        return "user", sorted(context.usernames)[0]
    return None


def _correlation_key(run: ScenarioRun, context: AlertContext) -> str:
    parts = [f"run:{run.id}"]
    if context.resources:
        parts.append(f"resource:{sorted(context.resources)[0]}")
    actor = _actor(context)
    if actor:
        parts.append(f"{actor[0]}:{actor[1]}")
    return "|".join(parts)[:200]


def _title(scenario: ScenarioDefinition, context: AlertContext) -> str:
    title = f"{scenario.name} ({scenario.id})"
    actor = _actor(context)
    if actor:
        title += f": {'source ' if actor[0] == 'ip' else 'user '}{actor[1]}"
    return title[:200]


# --- closing ------------------------------------------------------------------
# A response still waiting for a decision, or being executed, keeps the incident open.
UNDECIDED = ("pending_approval", "approved")


def close_incident(session: Session, incident: Incident, *, actor: str) -> Incident:
    """Mark an incident closed and audit it. The caller commits.

    Refused (409) when it is already closed, or while one of its responses is
    still pending approval or being executed: decide that response first.
    """
    from .. import audit
    from ..errors import ApiError
    from ..models import IncidentStatus

    if incident.status == IncidentStatus.closed.value:
        raise ApiError(409, "incident_closed", f"Incident {incident.id} is already closed.")
    undecided = [r for r in incident.responses if r.status in UNDECIDED]
    if undecided:
        raise ApiError(
            409,
            "response_in_progress",
            f"Incident {incident.id} has a response that is {undecided[0].status}; approve or reject it first.",
        )
    incident.status = IncidentStatus.closed.value
    audit.record(
        session,
        actor=actor,
        action="incident.closed",
        object_type="incident",
        object_id=str(incident.id),
        detail={
            "title": incident.title,
            "risk_score": incident.risk_score,
            "responses": {str(r.id): r.status for r in incident.responses},
        },
    )
    return incident
