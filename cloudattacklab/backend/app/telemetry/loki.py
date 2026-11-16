"""Telemetry source that reads a lab service's logs from Loki.

The monitoring stack (monitoring/) ships every container's logs to Loki with a
`service` label, so ShopCloud's logs are the stream `{service="shopcloud"}`.
This source asks Loki's HTTP API (`/loki/api/v1/query_range`) for that stream
from a start time to now and returns the log lines exactly as ShopCloud wrote
them, oldest first: the same records the Docker source returns, so
normalization and detection are unchanged.

If Loki cannot be reached or answers with an error, it raises
TelemetrySourceError with a readable message. It never falls back to Docker and
never returns made-up lines.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from .source import TelemetrySourceError

QUERY_RANGE = "/loki/api/v1/query_range"
# Loki's default `max_entries_limit_per_query`. Longer windows are read in pages.
PAGE_LIMIT = 5000
MAX_PAGES = 20


def _ns(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1_000_000_000)


def parse_query_range(body: Any) -> list[tuple[int, str]]:
    """(timestamp in ns, line) pairs from a query_range response, oldest first.

    Lines from all returned streams are merged. Raises TelemetrySourceError if the
    body is not a successful `streams` result.
    """
    if not isinstance(body, dict) or body.get("status") != "success":
        raise TelemetrySourceError(f"Loki returned an unexpected answer: {str(body)[:200]}")
    data = body.get("data") or {}
    if data.get("resultType") != "streams":
        raise TelemetrySourceError(f"Loki returned '{data.get('resultType')}' instead of log streams.")
    entries: list[tuple[int, str]] = []
    for stream in data.get("result") or []:
        for value in stream.get("values") or []:
            if not isinstance(value, (list, tuple)) or len(value) < 2:
                continue
            try:
                timestamp = int(value[0])
            except (TypeError, ValueError):
                continue
            line = value[1]
            if isinstance(line, str) and line.strip():
                # Alloy keeps Docker's trailing newline on each line.
                entries.append((timestamp, line.rstrip("\r\n")))
    entries.sort(key=lambda entry: entry[0])
    return entries


class LokiSource:
    name = "loki"
    late_arriving = True

    def __init__(
        self,
        base_url: str,
        service: str,
        *,
        timeout_seconds: float = 5.0,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.service = service
        self.query = '{service="%s"}' % service
        self.timeout = timeout_seconds
        self._client = client

    # --- interface --------------------------------------------------------
    def collect(self, since: Optional[datetime] = None) -> list[str]:
        start = _ns(since) if since is not None else None
        end = _ns(datetime.now(timezone.utc)) + 1
        lines: list[str] = []
        for _ in range(MAX_PAGES):
            entries = self._query(start, end, PAGE_LIMIT)
            lines.extend(line for _, line in entries)
            if len(entries) < PAGE_LIMIT:
                break
            # Full page: continue just after the newest entry read so far.
            start = entries[-1][0] + 1
        return lines

    def check(self) -> None:
        """A tiny query over the last minute; raises if Loki cannot answer it."""
        now = _ns(datetime.now(timezone.utc))
        self._query(now - 60 * 1_000_000_000, now + 1, 1)

    # --- internals --------------------------------------------------------
    def _query(self, start: Optional[int], end: int, limit: int) -> list[tuple[int, str]]:
        params: dict[str, Any] = {"query": self.query, "end": end, "limit": limit, "direction": "forward"}
        if start is not None:
            params["start"] = start
        url = self.base_url + QUERY_RANGE
        try:
            if self._client is not None:
                response = self._client.get(url, params=params, timeout=self.timeout)
            else:
                response = httpx.get(url, params=params, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise TelemetrySourceError(
                f"Loki at {self.base_url} could not be reached ({type(exc).__name__}: {exc}). "
                "Start the monitoring stack or set TELEMETRY_SOURCE=docker."
            ) from exc
        if response.status_code != 200:
            raise TelemetrySourceError(
                f"Loki at {self.base_url} answered HTTP {response.status_code}: {response.text.strip()[:200]}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise TelemetrySourceError(f"Loki at {self.base_url} did not return JSON.") from exc
        return parse_query_range(body)
