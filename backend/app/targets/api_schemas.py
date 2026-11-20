"""Request and response bodies for Target Management. Secrets are write-only."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Secret = Annotated[str, StringConstraints(min_length=32, max_length=256)]


class TargetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")]
    kind: Annotated[str, StringConstraints(strip_whitespace=True, max_length=32)] = "shopcloud"
    base_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    integration_key: Secret = Field(description="ShopCloud's INTEGRATION_KEY. Never returned.")
    control_token: Annotated[str, StringConstraints(min_length=8, max_length=256)] = Field(
        description="ShopCloud's LAB_CONTROL_TOKEN (X-Lab-Token). Never returned."
    )


class AuthorizationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_ids: list[Annotated[str, StringConstraints(max_length=64)]] = Field(min_length=1, max_length=10)
    duration_minutes: int = 30
    authorized_by: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    acknowledgement: bool
    max_runs: int = Field(default=3, ge=1, le=10)


class AuthorizationOut(BaseModel):
    id: UUID
    scenario_ids: list[str]
    status: str          # active | revoked | expired (expired also covers a used-up run budget)
    authorized_by: str
    acknowledgement: str
    max_runs: int
    runs_used: int
    created_at: datetime
    expires_at: datetime
    revoked_at: Optional[datetime]


class TargetOut(BaseModel):
    """A registered target. There are deliberately no secret fields here."""

    id: UUID
    name: str
    kind: str
    base_url: str
    resolved_ip: str
    port: int
    status: str
    instance_id: Optional[str]
    app_version: Optional[str]
    capabilities: Optional[list[str]]
    verified_at: Optional[datetime]
    telemetry_verified_at: Optional[datetime]
    lab_id: UUID
    created_at: datetime
    active_authorization: Optional[AuthorizationOut]


class TargetDetail(TargetOut):
    authorizations: list[AuthorizationOut]
    url_allowed: bool             # the stored URL still passes the policy now
    url_error: Optional[str]
