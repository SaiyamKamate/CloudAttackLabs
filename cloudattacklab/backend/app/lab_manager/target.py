"""HTTP client for the lab target (ShopCloud), per docs/shopcloud-interface.md.

Only the two calls the Lab Manager needs: GET /health and POST /lab/reset.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol

import httpx


class TargetError(Exception):
    """The target did not answer as the interface says it should."""


@dataclass(frozen=True)
class TargetHealth:
    reachable: bool                         # the target answered at all
    healthy: bool                           # ... with 200 {"status": "ok"}
    status_code: Optional[int] = None
    body: Optional[dict[str, Any]] = None   # what GET /health returned
    error: Optional[str] = None


class TargetClient(Protocol):
    def health(self) -> TargetHealth: ...
    def wait_healthy(self, timeout_seconds: float = 30) -> TargetHealth: ...
    def reset(self) -> None: ...


class HttpTargetClient:
    def __init__(self, base_url: str, lab_control_token: str, *, timeout_seconds: float = 5) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = lab_control_token
        self._timeout = timeout_seconds

    def health(self) -> TargetHealth:
        try:
            response = httpx.get(f"{self.base_url}/health", timeout=self._timeout)
        except httpx.HTTPError as exc:
            return TargetHealth(reachable=False, healthy=False, error=f"{type(exc).__name__}: {exc}")
        try:
            body = response.json()
        except ValueError:
            body = None
        body = body if isinstance(body, dict) else None
        healthy = response.status_code == 200 and (body or {}).get("status") == "ok"
        return TargetHealth(reachable=True, healthy=healthy, status_code=response.status_code, body=body)

    def wait_healthy(self, timeout_seconds: float = 30) -> TargetHealth:
        deadline = time.monotonic() + timeout_seconds
        while True:
            result = self.health()
            if result.healthy:
                return result
            if time.monotonic() >= deadline:
                raise TargetError(
                    f"target at {self.base_url} is not healthy: "
                    f"{result.error or f'HTTP {result.status_code} {result.body}'}"
                )
            time.sleep(1)

    def reset(self) -> None:
        try:
            response = httpx.post(
                f"{self.base_url}/lab/reset", headers={"X-Lab-Token": self._token}, timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            raise TargetError(f"POST /lab/reset failed: {type(exc).__name__}: {exc}") from exc
        if response.status_code != 200:
            raise TargetError(f"POST /lab/reset returned HTTP {response.status_code}")
