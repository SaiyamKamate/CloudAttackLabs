"""Response bodies for the events and alerts endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EventOut(BaseModel):
    """A normalized event, without the raw record."""

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    timestamp: datetime
    source: str
    event_type: str
    severity: str
    source_ip: Optional[str]
    resource: str
    message: str
    raw_event_reference: str
    schema_version: str
    scenario_id: Optional[str]
    lab_id: Optional[UUID]
    run_id: Optional[UUID]
    username: Optional[str]
    container_id: Optional[str]
    rule_id: Optional[str]
    correlation_key: Optional[str]


class EventDetail(EventOut):
    """One event with its raw evidence."""

    raw: Optional[dict[str, Any]]


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    rule_id: str
    severity: str
    title: str
    description: str
    lab_id: Optional[UUID]
    run_id: Optional[UUID]
    event_count: int
    first_event_at: Optional[datetime]
    last_event_at: Optional[datetime]
    created_at: datetime


class AlertDetail(AlertOut):
    """An alert with the events that caused it."""

    events: list[EventOut]
