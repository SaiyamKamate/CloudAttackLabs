"""HTTP client for the lab target (ShopCloud), per docs/shopcloud-interface.md.

The calls the platform needs: health, login, reading the protected resource,
the rate limit and reset.
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


@dataclass(frozen=True)
class LoginResult:
    ok: bool                                # the login returned 200
    status_code: Optional[int] = None
    error: Optional[str] = None             # set when the request itself failed


@dataclass(frozen=True)
class AccessResult:
    """What happened when an account logged in and asked for the protected resource."""

    login_status: Optional[int] = None      # HTTP status of POST /api/login
    status_code: Optional[int] = None       # HTTP status of GET /api/orders; None if not sent
    error: Optional[str] = None             # set when a request itself failed


class TargetClient(Protocol):
    def health(self) -> TargetHealth: ...
    def wait_healthy(self, timeout_seconds: float = 30) -> TargetHealth: ...
    def reset(self) -> None: ...
    def login(self, username: str, password: str) -> LoginResult: ...
    def read_orders(self, username: str, password: str) -> AccessResult: ...
    def set_rate_limit(
        self, enabled: bool, max_attempts: Optional[int] = None, window_seconds: Optional[int] = None
    ) -> Optional[dict[str, Any]]: ...


class HttpTargetClient:
    """Redirects are never followed (httpx's default, passed explicitly): a 3xx
    answer is just a non-200 status, so no request can leave the target."""

    def __init__(
        self, base_url: str, lab_control_token: str, *, timeout_seconds: float = 5, http: Optional[httpx.Client] = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = lab_control_token
        self._timeout = timeout_seconds
        self._http = http  # an httpx.Client (tests); None: one-off requests

    def _get(self, url: str, **kwargs):
        return (self._http or httpx).get(url, follow_redirects=False, **kwargs)

    def _post(self, url: str, **kwargs):
        return (self._http or httpx).post(url, follow_redirects=False, **kwargs)

    def health(self) -> TargetHealth:
        try:
            response = self._get(f"{self.base_url}/health", timeout=self._timeout)
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
            response = self._post(
                f"{self.base_url}/lab/reset", headers={"X-Lab-Token": self._token}, timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            raise TargetError(f"POST /lab/reset failed: {type(exc).__name__}: {exc}") from exc
        if response.status_code != 200:
            raise TargetError(f"POST /lab/reset returned HTTP {response.status_code}")

    def login(self, username: str, password: str) -> LoginResult:
        """POST /api/login. Used by the test_account pre-flight check."""
        try:
            response = self._post(
                f"{self.base_url}/api/login",
                json={"username": username, "password": password},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            return LoginResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        return LoginResult(ok=response.status_code == 200, status_code=response.status_code)

    def read_orders(self, username: str, password: str) -> AccessResult:
        """Log in, then GET /api/orders with that token. The token is not kept.

        Used by the access_remains_denied verification.
        """
        try:
            login = self._post(
                f"{self.base_url}/api/login",
                json={"username": username, "password": password},
                timeout=self._timeout,
            )
            if login.status_code != 200:
                return AccessResult(login_status=login.status_code, error="login did not return 200")
            token = login.json().get("token")
            if not isinstance(token, str) or not token:
                return AccessResult(login_status=200, error="login returned no token")
            response = self._get(
                f"{self.base_url}/api/orders",
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._timeout,
            )
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            return AccessResult(error=f"{type(exc).__name__}: {exc}")
        return AccessResult(login_status=200, status_code=response.status_code)

    def set_rate_limit(
        self, enabled: bool, max_attempts: Optional[int] = None, window_seconds: Optional[int] = None
    ) -> Optional[dict[str, Any]]:
        """POST /lab/rate-limit. Returns the settings the target says it saved.

        Used by the temporary_rate_limit response and the disable_rate_limit cleanup step.
        """
        body: dict[str, Any] = {"enabled": enabled}
        if max_attempts is not None:
            body["max_attempts"] = max_attempts
        if window_seconds is not None:
            body["window_seconds"] = window_seconds
        try:
            response = self._post(
                f"{self.base_url}/lab/rate-limit",
                json=body,
                headers={"X-Lab-Token": self._token},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise TargetError(f"POST /lab/rate-limit failed: {type(exc).__name__}: {exc}") from exc
        if response.status_code != 200:
            raise TargetError(f"POST /lab/rate-limit returned HTTP {response.status_code}")
        try:
            saved = response.json()
        except ValueError:
            saved = None
        return saved if isinstance(saved, dict) else None
