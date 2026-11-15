"""Response bodies for the incidents endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ..observability.api_schemas import AlertOut
from ..response_manager.api_schemas import ResponseOut


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    lab_id: Optional[UUID]
    run_id: Optional[UUID]
    title: str
    status: str
    created_at: datetime
    correlation_key: Optional[str]
    # 0-100 heuristic and how it was reached; null for an incident that was never scored.
    risk_score: Optional[int]
    risk_explanation: Optional[str]


class Evidence(BaseModel):
    """Where the proof for a timeline entry is: an API path, and for an event its raw reference."""

    kind: str  # run, event, alert, response or audit
    id: str
    href: str
    raw_event_reference: Optional[str] = None


class TimelineEntry(BaseModel):
    time: datetime
    type: str
    description: str
    evidence: Evidence


class IncidentDetail(IncidentOut):
    """An incident with its alerts, its response actions and its timeline."""

    alerts: list[AlertOut]
    responses: list[ResponseOut]
    timeline: list[TimelineEntry]
