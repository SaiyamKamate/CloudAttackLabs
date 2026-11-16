"""Telemetry for the container scenario: Docker events (base) and Falco alerts (extra).

Docker events
    The lab's test container's runtime events, read through the adapter
    (`docker events`, filtered on the lab project and the test-target label).
    `exec_start` (a process started in the container by `docker exec`) becomes
    a `suspicious_process` event: in this lab nothing should ever start a process
    in the test container, so any such process is the activity the scenario is
    about. `start` becomes `container_started`; `die` and `stop` become
    `container_stopped`. Other actions (create, exec_create, exec_die, ...)
    are skipped.

Falco alerts
    Falco's JSON lines from its stdout, read like ShopCloud's logs (Docker logs
    or Loki, following TELEMETRY_SOURCE). Only alerts about the run's test
    container (by container id) are kept; each becomes a `suspicious_process`
    event with source `falco` and rule_id set to the Falco rule name.

The original record is kept in events.raw. raw_event_reference is
`<where it was read>:<what>:<id>`, for example
`docker:test_container:exec_start-1794831543602722263` or
`loki:falco:1794831543658816106-1a2b3c4d`. Nothing is invented: a record that
cannot be read is skipped.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from ..lab_manager.adapter import LabAdapter
from ..lab_manager.container import TEST_TARGET_LABEL
from ..schemas.event import EventSource, EventType, NormalizedEvent, Severity
from .normalize import RunContext

# Docker action -> normalized type. Actions not listed are skipped.
DOCKER_ACTIONS: dict[str, EventType] = {
    "exec_start": EventType.suspicious_process,
    "start": EventType.container_started,
    "die": EventType.container_stopped,
    "stop": EventType.container_stopped,
}
DOCKER_SEVERITY: dict[EventType, Severity] = {
    EventType.suspicious_process: Severity.medium,
    EventType.container_started: Severity.info,
    EventType.container_stopped: Severity.info,
}
# Falco priority -> normalized severity.
FALCO_SEVERITY: dict[str, Severity] = {
    "emergency": Severity.critical,
    "alert": Severity.critical,
    "critical": Severity.critical,
    "error": Severity.high,
    "warning": Severity.medium,
    "notice": Severity.low,
    "informational": Severity.info,
    "info": Severity.info,
    "debug": Severity.info,
}


class DockerEventSource:
    """The lab's test-container events from the Docker adapter, as JSON lines."""

    name = "docker"
    late_arriving = False

    def __init__(self, adapter: LabAdapter) -> None:
        self.adapter = adapter

    def collect(self, since: Optional[datetime] = None) -> list[str]:
        now = datetime.now(timezone.utc)
        return self.adapter.container_events(since or now, now)

    def check(self) -> None:
        return None


def _ns_to_datetime(value: Any) -> Optional[datetime]:
    try:
        ns = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ns // 1_000_000_000, tz=timezone.utc).replace(microsecond=(ns % 1_000_000_000) // 1000)


def _correlation_key(ctx: RunContext, container_id: str) -> str:
    return f"run:{ctx.run_id}|container:{container_id}"


def make_docker_event_normalizer(target: str):
    """A normalizer for Docker event records about the test container `target`."""

    def normalize_docker_event(record: dict[str, Any], ctx: RunContext, origin: str = "docker") -> Optional[NormalizedEvent]:
        if record.get("Type") != "container":
            return None
        action_text = record.get("Action") or record.get("status")
        if not isinstance(action_text, str) or not action_text:
            return None
        action, _, detail = action_text.partition(":")
        event_type = DOCKER_ACTIONS.get(action.strip())
        actor = record.get("Actor") or {}
        attributes = actor.get("Attributes") or {}
        container_id = str(actor.get("ID") or record.get("id") or "")[:12]
        key, value = TEST_TARGET_LABEL
        if event_type is None or not container_id or attributes.get(key) != value:
            return None
        timestamp = _ns_to_datetime(record.get("timeNano"))
        if timestamp is None:
            return None
        name = attributes.get("name") or container_id
        detail = detail.strip()
        if event_type == EventType.suspicious_process:
            message = f"docker exec started a process in {name}: {detail}" if detail else f"docker exec started a process in {name}"
        elif event_type == EventType.container_started:
            message = f"container {name} started"
        else:
            exit_code = attributes.get("exitCode")
            message = f"container {name} {action.strip()}" + (f" (exit code {exit_code})" if exit_code else "")
        return NormalizedEvent(
            timestamp=timestamp,
            source=EventSource.docker,
            event_type=event_type,
            severity=DOCKER_SEVERITY[event_type],
            resource=target,
            message=message,
            raw_event_reference=f"{origin}:{target}:{action.strip()}-{record.get('timeNano')}",
            scenario_id=ctx.scenario_id,
            lab_id=ctx.lab_id,
            run_id=ctx.run_id,
            container_id=container_id,
            correlation_key=_correlation_key(ctx, container_id),
        )

    return normalize_docker_event


def make_falco_normalizer(target: str):
    """A normalizer for Falco JSON alerts; keeps only those about the run's test container
    (ctx.container_id)."""

    def normalize_falco(record: dict[str, Any], ctx: RunContext, origin: str = "docker") -> Optional[NormalizedEvent]:
        rule = record.get("rule")
        fields = record.get("output_fields")
        if not isinstance(rule, str) or not rule or not isinstance(fields, dict):
            return None
        container_id = str(fields.get("container.id") or "")[:12]
        if not container_id or not ctx.container_id or container_id != ctx.container_id[:12]:
            return None  # an alert about another container (or the host): not this run's evidence
        ns = fields.get("evt.time.iso8601") or fields.get("evt.time")
        timestamp = _ns_to_datetime(ns) or _parse_time(record.get("time"))
        if timestamp is None:
            return None
        if not isinstance(ns, int):
            ns = int(timestamp.timestamp() * 1_000_000_000)
        output = record.get("output")
        priority = str(record.get("priority") or "").lower()
        rule_hash = hashlib.sha1(rule.encode()).hexdigest()[:8]
        return NormalizedEvent(
            timestamp=timestamp,
            source=EventSource.falco,
            event_type=EventType.suspicious_process,
            severity=FALCO_SEVERITY.get(priority, Severity.medium),
            resource=target,
            message=_strip_time(output) if isinstance(output, str) and output.strip() else f"Falco rule '{rule}' fired",
            raw_event_reference=f"{origin}:falco:{ns}-{rule_hash}",
            scenario_id=ctx.scenario_id,
            lab_id=ctx.lab_id,
            run_id=ctx.run_id,
            container_id=container_id,
            rule_id=rule[:100],
            correlation_key=_correlation_key(ctx, container_id),
        )

    return normalize_falco


def _parse_time(value: Any) -> Optional[datetime]:
    """Falco's `time` has nanoseconds; Python keeps microseconds."""
    if not isinstance(value, str) or "T" not in value:
        return None
    text = value.rstrip("Z")
    if "." in text:
        head, fraction = text.split(".", 1)
        text = f"{head}.{fraction[:6]}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _strip_time(output: str) -> str:
    """Falco's `output` starts with the time and priority; the message is the rest."""
    parts = output.strip().split(" ", 2)
    if len(parts) == 3 and parts[0][:4].isdigit():
        return parts[2][:1000]
    return output.strip()[:1000]
