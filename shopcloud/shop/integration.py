"""CloudAttackLab integration API v1 (docs/architecture/PRESENTATION_INTEGRATION_PLAN.md, sections 9 and 10).

Three endpoints under /integration/v1, all HMAC-signed with INTEGRATION_KEY:

    POST /integration/v1/handshake        proves this is ShopCloud and that it holds the key
    POST /integration/v1/telemetry/test   emits one `integration_test` security event
    GET  /integration/v1/events           the buffered security events (the JSON log records)

The integration is off unless an integration key is configured: then every
/integration path answers 404, exactly as before. /integration/* requests are
never written to the security log (main.LOGGED_PREFIXES), so CloudAttackLab
reading the feed does not create telemetry about itself.

Request signature (headers X-CAL-Timestamp, X-CAL-Nonce, X-CAL-Signature):
    HMAC-SHA256(key, METHOD \\n PATH[?QUERY] \\n timestamp \\n nonce \\n sha256_hex(body))
Response signature (header X-CAL-Response-Signature), bound to the request's nonce:
    HMAC-SHA256(key, "response" \\n nonce \\n sha256_hex(response body))
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

APP_VERSION = "1.1.0"
INTERFACE_VERSION = "1.0"
TELEMETRY_SCHEMA = "shopcloud.security_event/1"
CAPABILITIES = ["login_endpoint", "rate_limit_control", "telemetry_feed_v1"]

MAX_CLOCK_SKEW_SECONDS = 60
NONCE_TTL_SECONDS = 120
BUFFER_SIZE = 10_000
MAX_PAGE = 1000
MAX_BODY_BYTES = 4096


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sign(key: str, message: str) -> str:
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def request_message(method: str, path_query: str, timestamp: str, nonce: str, body: bytes) -> str:
    return "\n".join([method.upper(), path_query, timestamp, nonce, sha256_hex(body)])


def response_message(nonce: str, body: bytes) -> str:
    return "\n".join(["response", nonce, sha256_hex(body)])


def handshake_proof(key: str, challenge: str, target_id: str, instance_id: str) -> str:
    return sign(key, f"handshake|{challenge}|{target_id}|{instance_id}")


class EventBuffer:
    """The latest security events, each numbered with an increasing `seq`."""

    def __init__(self, instance_id: str, maxlen: int = BUFFER_SIZE) -> None:
        self.instance_id = instance_id
        self._events: deque[dict] = deque(maxlen=maxlen)
        self._seq = 0
        self._dropped_through = 0  # highest seq that fell out of the buffer
        self._lock = threading.Lock()

    def append(self, record: dict) -> dict:
        with self._lock:
            self._seq += 1
            if len(self._events) == self._events.maxlen:
                self._dropped_through = self._events[0]["seq"]
            event = {"schema": TELEMETRY_SCHEMA, "seq": self._seq, "instance_id": self.instance_id, **record}
            self._events.append(event)
            return event

    def query(self, since: Optional[datetime], after_seq: int, limit: int) -> dict:
        with self._lock:
            truncated = after_seq < self._dropped_through
            selected = []
            for event in self._events:
                if event["seq"] <= after_seq:
                    continue
                if since is not None and _parse_time(event.get("timestamp")) is not None:
                    if _parse_time(event["timestamp"]) < since:
                        continue
                selected.append(event)
                if len(selected) >= limit:
                    break
            next_seq = selected[-1]["seq"] if selected else max(after_seq, 0)
            return {"events": selected, "next_seq": next_seq, "truncated": truncated, "latest_seq": self._seq}


def _parse_time(value) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class NonceCache:
    """Nonces seen in the last NONCE_TTL_SECONDS, so a signed request cannot be replayed."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def use(self, nonce: str) -> bool:
        """True the first time a nonce is seen; False for a replay."""
        now = self._clock()
        with self._lock:
            for old in [n for n, t in self._seen.items() if now - t > NONCE_TTL_SECONDS]:
                del self._seen[old]
            if nonce in self._seen:
                return False
            self._seen[nonce] = now
            return True


def build_router(
    *,
    key: Optional[str],
    buffer: EventBuffer,
    write_log: Callable[[dict], None],
    now: Callable[[], str],
    wall_clock: Callable[[], float] = time.time,
) -> APIRouter:
    router = APIRouter(prefix="/integration/v1")
    nonces = NonceCache(wall_clock)

    def unauthorized() -> JSONResponse:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    async def authenticate(request: Request) -> Optional[tuple[str, bytes]]:
        """(nonce, body) if the request is correctly signed and fresh, else None."""
        if not key:
            return None
        timestamp = request.headers.get("x-cal-timestamp", "")
        nonce = request.headers.get("x-cal-nonce", "")
        signature = request.headers.get("x-cal-signature", "")
        if not (timestamp.isdigit() and 16 <= len(nonce) <= 64 and nonce.isalnum() and signature):
            return None
        if abs(wall_clock() - int(timestamp)) > MAX_CLOCK_SKEW_SECONDS:
            return None
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            return None
        path_query = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        expected = sign(key, request_message(request.method, path_query, timestamp, nonce, body))
        if not hmac.compare_digest(expected, signature):
            return None
        if not nonces.use(nonce):
            return None
        return nonce, body

    def signed(payload: dict, nonce: str, status_code: int = 200) -> Response:
        body = json.dumps(payload, separators=(",", ":")).encode()
        return Response(
            content=body,
            status_code=status_code,
            media_type="application/json",
            headers={"X-CAL-Response-Signature": sign(key, response_message(nonce, body))},
        )

    def json_body(body: bytes) -> dict:
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def short_text(value, low: int, high: int) -> Optional[str]:
        if isinstance(value, str) and low <= len(value) <= high and value.isprintable():
            return value
        return None

    @router.post("/handshake")
    async def handshake(request: Request):
        auth = await authenticate(request)
        if auth is None:
            return unauthorized()
        nonce, body = auth
        data = json_body(body)
        challenge = short_text(data.get("challenge"), 16, 128)
        target_id = short_text(data.get("target_id"), 1, 64)
        if challenge is None or target_id is None:
            return signed({"error": "invalid_request"}, nonce, 422)
        return signed(
            {
                "app": "shopcloud",
                "kind": "shopcloud",
                "instance_id": buffer.instance_id,
                "app_version": APP_VERSION,
                "interface_version": INTERFACE_VERSION,
                "telemetry_schema": TELEMETRY_SCHEMA,
                "capabilities": CAPABILITIES,
                "proof": handshake_proof(key, challenge, target_id, buffer.instance_id),
            },
            nonce,
        )

    @router.post("/telemetry/test")
    async def telemetry_test(request: Request):
        auth = await authenticate(request)
        if auth is None:
            return unauthorized()
        nonce, body = auth
        test_nonce = short_text(json_body(body).get("nonce"), 16, 64)
        if test_nonce is None:
            return signed({"error": "invalid_request"}, nonce, 422)
        record = {
            "timestamp": now(),
            "service": "shopcloud",
            "level": "info",
            "event": "integration_test",
            "request_id": str(uuid.uuid4()),
            "source_ip": request.client.host if request.client else None,
            "method": request.method,
            "path": request.url.path,
            "status": 200,
            "username": None,
            "message": "CloudAttackLab integration telemetry test",
            "test_nonce": test_nonce,
        }
        write_log(record)
        event = buffer.append(record)
        return signed({"status": "emitted", "seq": event["seq"], "request_id": record["request_id"]}, nonce)

    @router.get("/events")
    async def events(request: Request):
        auth = await authenticate(request)
        if auth is None:
            return unauthorized()
        nonce, _ = auth
        params = request.query_params
        since = None
        if "since" in params:
            since = _parse_time(params["since"])
            if since is None:
                return signed({"error": "invalid_since"}, nonce, 422)
        after_seq = params.get("after_seq", "0")
        limit = params.get("limit", "500")
        if not (after_seq.isdigit() and limit.isdigit() and 1 <= int(limit) <= MAX_PAGE):
            return signed({"error": "invalid_paging"}, nonce, 422)
        return signed(buffer.query(since, int(after_seq), int(limit)), nonce)

    return router
