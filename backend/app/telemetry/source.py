"""Telemetry source interface and the Docker-logs implementation.

A source just returns raw log lines for a time window. It does not parse or
interpret them. `name` is the source's short name; it is the first part of each
event's raw_event_reference (`docker:shopcloud:<request_id>`,
`loki:shopcloud:<request_id>`). The Loki implementation is in `loki.py`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol

from ..lab_manager.adapter import LabAdapter


class TelemetrySourceError(Exception):
    """The source could not be read. The message is shown on the run as it is."""


class TelemetrySource(Protocol):
    name: str
    # True when records can reach the source some time after they were written,
    # so collection keeps polling after the run until the count stops growing.
    late_arriving: bool

    def collect(self, since: Optional[datetime] = None) -> list[str]:
        """Raw log lines (one record each), oldest first, optionally since a time."""
        ...

    def check(self) -> None:
        """Raise TelemetrySourceError if the source cannot be read right now."""
        ...


class DockerLogSource:
    """Collects a lab service's logs through the Docker adapter.

    The adapter is the only place that talks to Docker; this source only asks it
    for log text and splits it into lines.
    """

    name = "docker"
    late_arriving = False

    def __init__(self, adapter: LabAdapter, service: str) -> None:
        self.adapter = adapter
        self.service = service

    def collect(self, since: Optional[datetime] = None) -> list[str]:
        text = self.adapter.logs(self.service, since)
        return [line for line in text.splitlines() if line.strip()]

    def check(self) -> None:
        # The lab's containers are checked by pre-flight; nothing extra here.
        return None
