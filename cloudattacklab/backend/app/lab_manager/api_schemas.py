"""Request and response bodies for the Labs endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints


class LabCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    target: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)] = "shopcloud"


class LabOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    target: str
    environment: str
    status: str
    created_at: datetime
    updated_at: datetime


class ContainerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    service: str
    name: str
    container_id: str
    state: str
    health: Optional[str]


class TargetHealthOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    reachable: bool
    healthy: bool
    status_code: Optional[int]
    body: Optional[dict[str, Any]]
    error: Optional[str]


class TelemetryStatusOut(BaseModel):
    """Which telemetry source the backend uses (TELEMETRY_SOURCE) and, for Loki,
    whether it can be read right now."""

    source: str                  # "docker" or "loki"
    loki_url: Optional[str]      # only for "loki"
    reachable: Optional[bool]    # only checked for "loki"
    error: Optional[str]


class FalcoStatusOut(BaseModel):
    """FALCO_ENABLED and, when it is on, the Falco container's state and health."""

    enabled: bool
    state: Optional[str]    # e.g. "running", "exited"; None if there is no Falco container
    health: Optional[str]   # "healthy", "starting", "unhealthy" or None
    healthy: bool           # enabled, running and its health check passes


class LabDetail(LabOut):
    containers: list[ContainerOut]
    target_health: TargetHealthOut
    telemetry: TelemetryStatusOut
    falco: FalcoStatusOut
