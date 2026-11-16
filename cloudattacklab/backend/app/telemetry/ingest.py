"""Ingest: collect raw lines, normalize, and store them as events.

Invalid-JSON lines are skipped and counted. The same request_id is never stored
twice, whichever source it came from: raw_event_reference is
`<source>:shopcloud:<request_id>`, and a record is a duplicate when any stored
event at or after the window start has the same request_id. Events are kept only
from the run's start time onward.

Sources whose records arrive late (Loki) are read once more after the run with
`ingest_until_settled`, which polls until the number of records stops growing.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Event
from ..schemas.event import NormalizedEvent
from .normalize import RunContext, normalize
from .source import TelemetrySource

log = logging.getLogger("cloudattacklab.telemetry")


@dataclass
class IngestResult:
    collected: int = 0  # lines the source returned for the window
    stored: int = 0
    duplicates: int = 0
    invalid_json: int = 0
    skipped: int = 0  # valid JSON but not a usable record (no request_id, bad timestamp, ...)

    def add(self, other: "IngestResult") -> None:
        self.collected = other.collected  # the latest read, not a sum
        self.stored += other.stored
        self.duplicates += other.duplicates
        self.invalid_json += other.invalid_json
        self.skipped += other.skipped


def request_id_of(reference: str) -> str:
    """`docker:shopcloud:abc` -> `abc`."""
    return reference.split(":", 2)[-1]


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
    def __init__(
        self,
        session: Session,
        source: TelemetrySource,
        ctx: RunContext,
        *,
        grace_seconds: float = 3,
        normalizer: Optional[Callable[..., Optional[NormalizedEvent]]] = None,
    ) -> None:
        # How one record becomes an event; ShopCloud's mapping unless the source needs another.
        self.normalizer = normalizer or normalize
        self.session = session
        self.source = source
        self.ctx = ctx
        self.grace = timedelta(seconds=grace_seconds)

    def ingest(self) -> IngestResult:
        result = IngestResult()
        window_start = self.ctx.started_at - self.grace
        lines = self.source.collect(since=window_start)
        result.collected = len(lines)
        origin = getattr(self.source, "name", "docker")

        # Request ids already stored for this run, or for any event in this time
        # window (a neighbouring run, or another source), so none is stored twice.
        seen: set[str] = {
            request_id_of(reference)
            for reference in self.session.scalars(
                select(Event.raw_event_reference).where(
                    (Event.run_id == self.ctx.run_id) | (Event.timestamp >= window_start)
                )
            )
        }

        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                result.invalid_json += 1
                continue
            if not isinstance(record, dict):
                result.invalid_json += 1
                continue

            event = self.normalizer(record, self.ctx, origin)
            if event is None:
                result.skipped += 1
                continue
            if event.timestamp < window_start:
                continue  # before this run; not ours
            request_id = request_id_of(event.raw_event_reference)
            if request_id in seen:
                result.duplicates += 1
                continue

            self.session.add(_event_row(event, record))
            seen.add(request_id)
            result.stored += 1

        if result.stored:
            self.session.commit()
        return result


def ingest_until_settled(
    ingest_once: Callable[[], IngestResult],
    *,
    interval_seconds: float,
    timeout_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    empty_is_settled: bool = False,
) -> IngestResult:
    """Ingest repeatedly until the source returns the same number of records twice
    in a row, or the timeout passes. Returns the combined result.

    Used after a run for sources whose records arrive late, so the last requests
    of a run are not missed. An empty answer does not count as settled (a run
    always makes requests, so no records means they have not arrived yet),
    unless `empty_is_settled` is set for an optional source such as Falco,
    which may rightly have nothing to report.
    """
    total = IngestResult()
    deadline = clock() + timeout_seconds
    previous = None
    while True:
        result = ingest_once()
        total.add(result)
        if previous is not None and result.collected == previous and (result.collected > 0 or empty_is_settled):
            break
        if clock() + interval_seconds > deadline:
            log.info("telemetry did not settle within %ss; stopping with %d records", timeout_seconds, result.collected)
            break
        previous = result.collected
        sleep(interval_seconds)
    return total
