"""HTTP client for a registered target's integration API (ShopCloud /integration/v1).

Signing matches ShopCloud's shop/integration.py:

    request:  HMAC-SHA256(key, METHOD \\n PATH[?QUERY] \\n timestamp \\n nonce \\n sha256_hex(body))
    response: HMAC-SHA256(key, "response" \\n nonce \\n sha256_hex(body)), header X-CAL-Response-Signature

Every call re-checks the target's pinned URL (url_policy.check_pinned) before
anything is sent, never follows redirects, has short timeouts and refuses
response bodies over MAX_RESPONSE_BYTES. Every answer must carry a valid
response signature, so an application without the key cannot feed telemetry.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import httpx

from ..lab_manager.target import TargetHealth
from .url_policy import UrlRefused

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class IntegrationError(Exception):
    """The target did not answer the integration API correctly. Message is safe to show."""


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sign(key: str, message: str) -> str:
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def request_message(method: str, path_query: str, timestamp: str, nonce: str, body: bytes) -> str:
    return "\n".join([method.upper(), path_query, timestamp, nonce, _sha256_hex(body)])


def response_message(nonce: str, body: bytes) -> str:
    return "\n".join(["response", nonce, _sha256_hex(body)])


def handshake_proof(key: str, challenge: str, target_id: str, instance_id: str) -> str:
    return sign(key, f"handshake|{challenge}|{target_id}|{instance_id}")


@dataclass(frozen=True)
class Pinned:
    """What a request may go to: the URL approved at registration."""

    base_url: str
    ip: str
    port: int


class IntegrationClient:
    def __init__(
        self,
        pinned: Pinned,
        key: str,
        *,
        recheck: Callable[[Pinned], None],
        timeout_seconds: float = 5,
        http: Optional[httpx.Client] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.pinned = pinned
        self._key = key
        self._recheck = recheck  # raises UrlRefused if the pinned URL is no longer allowed
        self._timeout = timeout_seconds
        self._http = http
        self._clock = clock

    @property
    def base_url(self) -> str:
        return self.pinned.base_url

    # --- the calls ------------------------------------------------------------
    def health(self) -> TargetHealth:
        """GET /health, unsigned (the application's public health check)."""
        try:
            self._recheck(self.pinned)
            response, body = self._send("GET", "/health", b"", {})
        except UrlRefused as exc:
            return TargetHealth(reachable=False, healthy=False, error=f"URL refused: {exc}")
        except IntegrationError as exc:
            return TargetHealth(reachable=False, healthy=False, error=str(exc))
        try:
            data = json.loads(body)
        except ValueError:
            data = None
        data = data if isinstance(data, dict) else None
        healthy = response.status_code == 200 and (data or {}).get("status") == "ok"
        return TargetHealth(reachable=True, healthy=healthy, status_code=response.status_code, body=data)

    def handshake(self, challenge: str, target_id: str) -> dict[str, Any]:
        return self._signed("POST", "/integration/v1/handshake", {"challenge": challenge, "target_id": target_id})

    def telemetry_test(self, nonce: str) -> dict[str, Any]:
        return self._signed("POST", "/integration/v1/telemetry/test", {"nonce": nonce})

    def events(self, *, since: Optional[str] = None, after_seq: int = 0, limit: int = 500) -> dict[str, Any]:
        params: dict[str, Any] = {"after_seq": after_seq, "limit": limit}
        if since is not None:
            params["since"] = since
        return self._signed("GET", "/integration/v1/events", None, params)

    # --- internals ------------------------------------------------------------
    def _signed(self, method: str, path: str, payload: Optional[dict], params: Optional[dict] = None) -> dict[str, Any]:
        try:
            self._recheck(self.pinned)
        except UrlRefused as exc:
            raise IntegrationError(f"URL refused: {exc}") from exc
        body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else b""
        path_query = path + ("?" + urlencode(params) if params else "")
        timestamp = str(int(self._clock()))
        nonce = secrets.token_hex(16)
        headers = {
            "X-CAL-Timestamp": timestamp,
            "X-CAL-Nonce": nonce,
            "X-CAL-Signature": sign(self._key, request_message(method, path_query, timestamp, nonce, body)),
        }
        if body:
            headers["Content-Type"] = "application/json"
        response, raw = self._send(method, path_query, body, headers)
        if response.status_code == 401:
            raise IntegrationError("the target refused the integration key (HTTP 401)")
        if response.status_code == 404:
            raise IntegrationError("the target has no integration API (HTTP 404); is INTEGRATION_KEY set on it?")
        expected = sign(self._key, response_message(nonce, raw))
        if not hmac.compare_digest(expected, response.headers.get("x-cal-response-signature", "")):
            raise IntegrationError("the target's answer is not signed with the integration key")
        if response.status_code != 200:
            raise IntegrationError(f"{method} {path} returned HTTP {response.status_code}")
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise IntegrationError(f"{method} {path} did not return JSON") from exc
        if not isinstance(data, dict):
            raise IntegrationError(f"{method} {path} did not return a JSON object")
        return data

    def _send(self, method: str, path_query: str, body: bytes, headers: dict) -> tuple[httpx.Response, bytes]:
        url = self.pinned.base_url + path_query
        client = self._http or httpx.Client(follow_redirects=False)
        try:
            with client.stream(
                method, url, content=body or None, headers=headers, timeout=self._timeout, follow_redirects=False
            ) as response:
                if 300 <= response.status_code < 400:
                    raise IntegrationError(f"the target answered with a redirect (HTTP {response.status_code}); redirects are never followed")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise IntegrationError("the target's answer is larger than 2 MB")
                    chunks.append(chunk)
                return response, b"".join(chunks)
        except httpx.HTTPError as exc:
            raise IntegrationError(f"{type(exc).__name__}: {exc}") from exc
        finally:
            if self._http is None:
                client.close()
