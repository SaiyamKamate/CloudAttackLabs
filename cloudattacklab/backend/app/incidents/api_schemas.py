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


class IncidentDetail(IncidentOut):
    """An incident with its alerts and any response actions requested for it."""

    alerts: list[AlertOut]
    responses: list[ResponseOut]
