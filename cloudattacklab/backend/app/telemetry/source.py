"""Telemetry source interface and the Docker-logs implementation.

A source just returns raw log lines for a time window. It does not parse or
interpret them. A future LokiSource would implement the same `collect`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol

from ..lab_manager.adapter import LabAdapter


class TelemetrySource(Protocol):
    def collect(self, since: Optional[datetime] = None) -> list[str]:
        """Raw log lines (one record each), oldest first, optionally since a time."""
        ...


class DockerLogSource:
    """Collects a lab service's logs through the Docker adapter.

    The adapter is the only place that talks to Docker; this source only asks it
    for log text and splits it into lines.
    """

    def __init__(self, adapter: LabAdapter, service: str) -> None:
        self.adapter = adapter
        self.service = service

    def collect(self, since: Optional[datetime] = None) -> list[str]:
        text = self.adapter.logs(self.service, since)
        return [line for line in text.splitlines() if line.strip()]
