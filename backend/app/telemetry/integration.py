"""Telemetry from a registered target's signed event feed (GET /integration/v1/events).

The third TelemetrySource next to DockerLogSource and LokiSource: it returns raw
JSON lines, so TelemetryIngestor and normalize() store them exactly like
ShopCloud's Docker or Loki log lines. Only the reference changes:
`integration:shopcloud:<request_id>`.

Fail-closed rules (a run's evidence must be whole and from the verified app):
- the feed cannot be read, or its answer is not signed with the integration key -> error;
- the feed says it dropped events the run needed (`truncated`)                     -> error;
- a record comes from another instance than the one verified                       -> error;
- a record with an unknown schema is skipped (counted in `skipped_records`).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from ..targets.integration_client import IntegrationClient, IntegrationError
from .source import TelemetrySourceError

TELEMETRY_SCHEMA = "shopcloud.security_event/1"
PAGE_LIMIT = 1000
MAX_PAGES = 20


def _since_param(value: datetime) -> str:
    value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class IntegrationFeedSource:
    name = "integration"
    late_arriving = True  # the feed is polled; keep reading until the count settles

    def __init__(self, client: IntegrationClient, instance_id: str, *, page_limit: int = PAGE_LIMIT) -> None:
        self.client = client
        self.instance_id = instance_id
        self.page_limit = page_limit
        self.skipped_records = 0

    def collect(self, since: Optional[datetime] = None) -> list[str]:
        lines: list[str] = []
        after_seq = 0
        since_param = _since_param(since) if since is not None else None
        for _ in range(MAX_PAGES):
            try:
                page = self.client.events(since=since_param, after_seq=after_seq, limit=self.page_limit)
            except IntegrationError as exc:
                raise TelemetrySourceError(f"the target's event feed cannot be read: {exc}") from exc
            if page.get("truncated") is True:
                raise TelemetrySourceError(
                    "the target's event buffer wrapped and dropped events of this run; the evidence is incomplete"
                )
            events = page.get("events")
            next_seq = page.get("next_seq")
            if not isinstance(events, list) or not isinstance(next_seq, int):
                raise TelemetrySourceError("the target's event feed answered in an unexpected format")
            for event in events:
                if not isinstance(event, dict) or event.get("schema") != TELEMETRY_SCHEMA:
                    self.skipped_records += 1
                    continue
                if event.get("instance_id") != self.instance_id:
                    raise TelemetrySourceError(
                        f"a telemetry record comes from instance '{event.get('instance_id')}', not the verified "
                        f"'{self.instance_id}'; verify the target again"
                    )
                lines.append(json.dumps(event))
            if len(events) < self.page_limit or next_seq <= after_seq:
                return lines
            after_seq = next_seq
        raise TelemetrySourceError(f"the target's event feed returned more than {MAX_PAGES} pages")

    def check(self) -> None:
        try:
            page = self.client.events(limit=1)
        except IntegrationError as exc:
            raise TelemetrySourceError(f"the target's event feed cannot be read: {exc}") from exc
        if not isinstance(page.get("events"), list):
            raise TelemetrySourceError("the target's event feed answered in an unexpected format")
