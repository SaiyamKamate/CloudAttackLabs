"""Incidents, minimal version: one incident per run, holding that run's alerts.

No timeline, risk score or correlation across alerts yet (step 9).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Alert, Incident, ScenarioRun
from ..schemas.scenario import ScenarioDefinition


def attach_alerts(
    session: Session, run: ScenarioRun, scenario: ScenarioDefinition, alerts: list[Alert]
) -> Incident | None:
    """Link new alerts to the run's incident, creating it on the first alert. The caller commits."""
    if not alerts:
        return None
    incident = session.scalars(select(Incident).where(Incident.run_id == run.id)).first()
    if incident is None:
        incident = Incident(lab_id=run.lab_id, run_id=run.id, title=f"{scenario.name} ({scenario.id})")
        session.add(incident)
    for alert in alerts:
        alert.incident = incident
    return incident
