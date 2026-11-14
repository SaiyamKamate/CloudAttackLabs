"""Ingest: collect raw lines, normalize, and store them as events.

Invalid-JSON lines are skipped and counted. The same request_id is never stored
twice (deduplicated on raw_event_reference). Events are kept only from the run's
start time onward.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Event
from ..schemas.event import NormalizedEvent
from .normalize import RunContext, normalize
from .source import TelemetrySource

log = logging.getLogger("cloudattacklab.telemetry")


@dataclass
class IngestResult:
    stored: int = 0
    duplicates: int = 0
    invalid_json: int = 0
    skipped: int = 0  # valid JSON but not a usable record (no request_id, bad timestamp, ...)

    def add(self, other: "IngestResult") -> None:
        self.stored += other.stored
        self.duplicates += other.duplicates
        self.invalid_json += other.invalid_json
        self.skipped += other.skipped


def _event_row(event: NormalizedEvent, raw: dict) -> Event:
    return Event(
        event_id=event.event_id,
        timestamp=event.timestamp,
        scenario_id=event.scenario_id,
        source=event.source,
        event_type=event.event_type,
        severity=event.severity,
        source_ip=event.source_ip,
        resource=event.resource,
        message=event.message,
        raw_event_reference=event.raw_event_reference,
        schema_version=event.schema_version,
        lab_id=event.lab_id,
        run_id=event.run_id,
        username=event.username,
        container_id=event.container_id,
        rule_id=event.rule_id,
        correlation_key=event.correlation_key,
        raw=raw,
    )


class TelemetryIngestor:
    def __init__(self, session: Session, source: TelemetrySource, ctx: RunContext, *, grace_seconds: float = 3) -> None:
        self.session = session
        self.source = source
        self.ctx = ctx
        self.grace = timedelta(seconds=grace_seconds)

    def ingest(self) -> IngestResult:
        result = IngestResult()
        window_start = self.ctx.started_at - self.grace
        lines = self.source.collect(since=window_start)

        # Already-stored request ids for this run, so we never store one twice.
        seen: set[str] = set(
            self.session.scalars(
                select(Event.raw_event_reference).where(Event.run_id == self.ctx.run_id)
            )
        )

        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                result.invalid_json += 1
                continue
            if not isinstance(record, dict):
                result.invalid_json += 1
                continue

            event = normalize(record, self.ctx)
            if event is None:
                result.skipped += 1
                continue
            if event.timestamp < window_start:
                continue  # before this run; not ours
            if event.raw_event_reference in seen:
                result.duplicates += 1
                continue

            self.session.add(_event_row(event, record))
            seen.add(event.raw_event_reference)
            result.stored += 1

        if result.stored:
            self.session.commit()
        return result
