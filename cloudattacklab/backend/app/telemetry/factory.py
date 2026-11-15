"""Pick the telemetry source from the TELEMETRY_SOURCE setting."""
from __future__ import annotations

from ..config import Settings
from ..lab_manager.adapter import LabAdapter
from .loki import LokiSource
from .source import DockerLogSource, TelemetrySource


def make_source(settings: Settings, adapter: LabAdapter) -> TelemetrySource:
    if settings.telemetry_source == "loki":
        return LokiSource(settings.loki_url, settings.telemetry_service)
    return DockerLogSource(adapter, settings.telemetry_service)
