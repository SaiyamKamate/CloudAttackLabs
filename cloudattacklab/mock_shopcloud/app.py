"""Fake ShopCloud.

A stand-in that follows docs/shopcloud-interface.md so the platform can be
built before the real ShopCloud is ready. All state is in memory.

Run locally:  uvicorn app:app --port 8000   (from this folder)
"""
import json
import os
import secrets
import sys
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

SERVICE = "shopcloud"
LAB_CONTROL_TOKEN = os.environ.get("LAB_CONTROL_TOKEN", "lab-dev-token")

# Synthetic test accounts only (see the interface spec).
ACCOUNTS = {
    "lab_user": {"password": "lab-user-pass", "role": "user"},
    "lab_restricted": {"password": "lab-restricted-pass", "role": "restricted"},
}
ORDERS = [
    {"id": 1, "item": "Test Keyboard", "total": 49.0},
    {"id": 2, "item": "Test Mouse", "total": 19.0},
]


class State:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.tokens: dict[str, str] = {}  # token -> username
        self.rate_limit = {"enabled": False, "max_attempts": 5, "window_seconds": 60}
        self.login_attempts: dict[str, deque] = defaultdict(deque)  # source_ip -> attempt times


state = State()


def log(event: str, level: str, request: Request, status: int, message: str, username=None) -> None:
    """One JSON object per line on stdout. This is the telemetry the platform collects."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "service": SERVICE,
        "level": level,
        "event": event,
        "request_id": str(uuid.uuid4()),
        "source_ip": request.client.host if request.client else None,
        "method": request.method,
        "path": request.url.path,
        "status": status,
        "username": username,
        "message": message,
    }
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()


async def read_json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": SERVICE})


def rate_limited(source_ip: str) -> bool:
    limit = state.rate_limit
    if not limit["enabled"]:
        return False
    now = time.monotonic()
    attempts = state.login_attempts[source_ip]
    while attempts and now - attempts[0] > limit["window_seconds"]:
        attempts.popleft()
    if len(attempts) >= limit["max_attempts"]:
        return True
    attempts.append(now)
    return False


async def login(request: Request) -> JSONResponse:
    body = await read_json(request)
    username = body.get("username")
    password = body.get("password")
    username = username if isinstance(username, str) else None
    source_ip = request.client.host if request.client else "unknown"

    if rate_limited(source_ip):
        log("login_rate_limited", "warning", request, 429, "Login blocked by rate limit", username)
        return JSONResponse({"error": "too_many_attempts"}, status_code=429)

    account = ACCOUNTS.get(username or "")
    if account is None or not isinstance(password, str) or not secrets.compare_digest(password, account["password"]):
        log("login_failed", "warning", request, 401, "Invalid username or password", username)
        return JSONResponse({"error": "invalid_credentials"}, status_code=401)

    token = secrets.token_urlsafe(24)
    state.tokens[token] = username
    log("login_success", "info", request, 200, "Login succeeded", username)
    return JSONResponse({"token": token, "username": username, "role": account["role"]})


async def orders(request: Request) -> JSONResponse:
    header = request.headers.get("authorization", "")
    username = state.tokens.get(header[7:]) if header.startswith("Bearer ") else None

    if username is None:
        log("access_denied", "warning", request, 401, "Missing or invalid token")
        return JSONResponse({"error": "not_authenticated"}, status_code=401)
    if ACCOUNTS[username]["role"] != "user":
        log("access_denied", "warning", request, 403, "Role is not allowed to read orders", username)
        return JSONResponse({"error": "forbidden"}, status_code=403)

    log("access_granted", "info", request, 200, "Orders read", username)
    return JSONResponse({"orders": ORDERS})


def lab_authorized(request: Request) -> bool:
    return secrets.compare_digest(request.headers.get("x-lab-token", ""), LAB_CONTROL_TOKEN)


async def lab_rate_limit(request: Request) -> JSONResponse:
    if not lab_authorized(request):
        log("access_denied", "warning", request, 403, "Bad lab control token")
        return JSONResponse({"error": "forbidden"}, status_code=403)

    if request.method == "POST":
        body = await read_json(request)
        enabled = body.get("enabled")
        max_attempts = body.get("max_attempts", state.rate_limit["max_attempts"])
        window = body.get("window_seconds", state.rate_limit["window_seconds"])
        valid = (
            isinstance(enabled, bool)
            and type(max_attempts) is int and 1 <= max_attempts <= 1000
            and type(window) is int and 1 <= window <= 3600
        )
        if not valid:
            return JSONResponse({"error": "invalid_settings"}, status_code=422)
        state.rate_limit = {"enabled": enabled, "max_attempts": max_attempts, "window_seconds": window}
        state.login_attempts.clear()
        log("rate_limit_changed", "info", request, 200, f"Rate limit set to {state.rate_limit}")

    return JSONResponse(state.rate_limit)


async def lab_reset(request: Request) -> JSONResponse:
    if not lab_authorized(request):
        log("access_denied", "warning", request, 403, "Bad lab control token")
        return JSONResponse({"error": "forbidden"}, status_code=403)
    state.reset()
    log("lab_reset", "info", request, 200, "State restored to baseline")
    return JSONResponse({"status": "reset"})


app = Starlette(
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/api/login", login, methods=["POST"]),
        Route("/api/orders", orders, methods=["GET"]),
        Route("/lab/rate-limit", lab_rate_limit, methods=["GET", "POST"]),
        Route("/lab/reset", lab_reset, methods=["POST"]),
    ]
)
