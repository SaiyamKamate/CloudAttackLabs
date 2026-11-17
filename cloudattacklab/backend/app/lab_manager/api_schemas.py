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


class TargetUrlOut(BaseModel):
    """The configured URL of the lab's target and whether it points into the lab."""

    url: str
    allowed: bool
    error: Optional[str]     # why it is refused; runs are refused until it is fixed


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


class ActiveRunOut(BaseModel):
    """The lab's run that has not finished. While it exists, lab reset and cleanup
    of other runs are refused (409 run_in_progress)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    scenario_id: str
    status: str


class LabDetail(LabOut):
    active_run: Optional[ActiveRunOut]
    containers: list[ContainerOut]
    target_health: TargetHealthOut
    target_url: TargetUrlOut
    telemetry: TelemetryStatusOut
    falco: FalcoStatusOut
