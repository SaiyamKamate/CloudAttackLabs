"""Turn one ShopCloud log record into a NormalizedEvent.

The mapping is docs/shopcloud-interface.md section 5. Nothing is invented: a
record with no usable event name or no request_id is skipped by the caller.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from ..schemas.event import EventSource, EventType, NormalizedEvent, Severity

# ShopCloud `event` name -> normalized event type (interface spec, section 5).
EVENT_TYPE_MAP: dict[str, EventType] = {
    "login_success": EventType.authentication_success,
    "login_failed": EventType.authentication_failure,
    "login_rate_limited": EventType.rate_limit_triggered,
    "access_granted": EventType.resource_access,
    "access_denied": EventType.authorization_denied,
    "rate_limit_changed": EventType.config_changed,
    "lab_reset": EventType.config_changed,
}

# Severity: info for successes and config changes; low for a single failure or denial.
_LOW = {EventType.authentication_failure, EventType.authorization_denied, EventType.rate_limit_triggered}


@dataclass(frozen=True)
class RunContext:
    """What a run contributes to every event collected inside its window."""

    run_id: UUID
    lab_id: Optional[UUID]
    scenario_id: Optional[str]
    started_at: datetime
    container_id: Optional[str] = None


def _severity(event_type: EventType) -> Severity:
    return Severity.low if event_type in _LOW else Severity.info


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)  # accepts the trailing 'Z' on 3.11+
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _clean_ip(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def normalize(record: dict[str, Any], ctx: RunContext) -> Optional[NormalizedEvent]:
    """Return a NormalizedEvent, or None if the record cannot be used (never invents one)."""
    if not isinstance(record, dict):
        return None
    request_id = record.get("request_id")
    event_name = record.get("event")
    timestamp = _parse_timestamp(record.get("timestamp"))
    if not isinstance(request_id, str) or not request_id or not isinstance(event_name, str) or timestamp is None:
        return None

    event_type = EVENT_TYPE_MAP.get(event_name, EventType.other)
    source_ip = _clean_ip(record.get("source_ip"))
    username = record.get("username")
    message = record.get("message")

    return NormalizedEvent(
        timestamp=timestamp,
        source=EventSource.application,
        event_type=event_type,
        severity=_severity(event_type),
        source_ip=source_ip,
        resource="shopcloud",
        message=message if isinstance(message, str) and message else event_name,
        raw_event_reference=f"docker:shopcloud:{request_id}",
        scenario_id=ctx.scenario_id,
        lab_id=ctx.lab_id,
        run_id=ctx.run_id,
        username=username if isinstance(username, str) else None,
        container_id=ctx.container_id,
        correlation_key=f"run:{ctx.run_id}|ip:{source_ip or 'unknown'}",
    )
