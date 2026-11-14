"""Response bodies for the response-action and audit endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ResponseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    incident_id: UUID
    action: str
    parameters: dict[str, Any]
    status: str
    requested_at: datetime
    decided_at: Optional[datetime]
    decided_by: Optional[str]
    executed_at: Optional[datetime]
    verified_at: Optional[datetime]
    verification_result: Optional[dict[str, Any]]


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    actor: str
    action: str
    object_type: str
    object_id: Optional[str]
    detail: Optional[dict[str, Any]]
