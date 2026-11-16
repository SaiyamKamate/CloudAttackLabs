"""Mark response actions as reverted once the target is back at its baseline.

Kept separate from the service so the Lab Manager (lab reset) and the Scenario
Engine (cleanup) can call it without importing the rest of the Response Manager.
This only updates rows; the caller has already turned the limit off on the
target, and the caller commits.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..models import Incident, ResponseAction, ResponseStatus

# Statuses that mean the action is currently applied on the target.
APPLIED = (ResponseStatus.executed.value, ResponseStatus.verified.value)


def mark_reverted(
    session: Session,
    *,
    lab_id: uuid.UUID,
    trigger: str,
    actor: str,
    actions: Optional[tuple[str, ...]] = None,
) -> list[ResponseAction]:
    """Every applied response in this lab becomes `reverted`, with one audit row each.

    It is per lab, not per run, because the target's rate limit is one setting:
    turning it off undoes every response that turned it on. `actions` limits it
    to the responses a cleanup step actually undid (turning the rate limit off
    does not restart a stopped container); None means all of them (lab reset).
    """
    query = (
        select(ResponseAction)
        .join(Incident, ResponseAction.incident_id == Incident.id)
        .where(Incident.lab_id == lab_id, ResponseAction.status.in_(APPLIED))
    )
    if actions is not None:
        query = query.where(ResponseAction.action.in_(actions))
    rows = list(session.scalars(query))
    for row in rows:
        before = row.status
        row.status = ResponseStatus.reverted.value
        audit.record(
            session,
            actor=actor,
            action="response.reverted",
            object_type="response_action",
            object_id=str(row.id),
            detail={
                "incident_id": str(row.incident_id),
                "action": row.action,
                "trigger": trigger,
                "status_before": before,
            },
        )
    return rows
