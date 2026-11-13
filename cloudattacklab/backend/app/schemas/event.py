"""Common event schema (guide section 4.2).

Every telemetry source (ShopCloud logs, Docker, Falco, the platform itself) is
converted into this one structure before detection rules run.

Change this file -> bump SCHEMA_VERSION -> run backend/scripts/export_schemas.py.
"""
from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "1.0"


class EventSource(str, Enum):
    application = "application"  # ShopCloud (or another target app) logs
    docker = "docker"            # container lifecycle / metadata
    falco = "falco"              # runtime security alerts
    platform = "platform"        # CloudAttackLab itself (scenario start, response, cleanup)


class Severity(str, Enum):
    info = "info"
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class EventType(str, Enum):
    # authentication (scenario 1)
    authentication_success = "authentication_success"
    authentication_failure = "authentication_failure"
    rate_limit_triggered = "rate_limit_triggered"
    # authorization (scenario 3)
    authorization_denied = "authorization_denied"
    resource_access = "resource_access"
    # container / runtime (scenario 2)
    container_started = "container_started"
    container_stopped = "container_stopped"
    suspicious_process = "suspicious_process"
    # platform bookkeeping
    scenario_started = "scenario_started"
    scenario_finished = "scenario_finished"
    response_executed = "response_executed"
    config_changed = "config_changed"
    other = "other"


class NormalizedEvent(BaseModel):
    """One normalized event. Field names follow the guide's table exactly."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    # --- required by the guide -------------------------------------------
    event_id: UUID = Field(default_factory=uuid4, description="Unique ID of the normalized event.")
    timestamp: datetime = Field(description="When the event happened. Always stored in UTC.")
    scenario_id: Optional[str] = Field(default=None, description="Scenario linked to the event, when known.")
    source: EventSource = Field(description="Where the event came from.")
    event_type: EventType = Field(description="Normalized category.")
    severity: Severity = Field(default=Severity.info, description="Normalized severity.")
    source_ip: Optional[str] = Field(default=None, description="Source address, if present.")
    resource: str = Field(min_length=1, description="Affected app, container, service or resource.")
    message: str = Field(min_length=1, description="Human-readable description.")
    raw_event_reference: str = Field(
        min_length=1,
        description="Pointer to the original log line/event so a detection can be explained "
        "(e.g. 'docker:shopcloud:<request_id>' or a Loki stream + timestamp).",
    )

    # --- additional fields the guide allows --------------------------------
    schema_version: str = Field(default=SCHEMA_VERSION)
    lab_id: Optional[UUID] = None
    run_id: Optional[UUID] = Field(default=None, description="Scenario run that produced the event.")
    username: Optional[str] = None
    container_id: Optional[str] = None
    rule_id: Optional[str] = Field(default=None, description="Set for Falco events: the Falco rule name.")
    correlation_key: Optional[str] = Field(
        default=None, description="Events sharing this key belong together, e.g. 'run:<run_id>|ip:<source_ip>'."
    )

    @field_validator("timestamp")
    @classmethod
    def _must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must include a timezone (use UTC, e.g. 2026-11-13T14:00:00Z)")
        return value.astimezone(timezone.utc)

    @field_validator("source_ip")
    @classmethod
    def _must_be_ip(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return str(ipaddress.ip_address(value))  # raises ValueError if not an IP
