"""ShopCloud: a small reference shop, the lab target for CloudAttackLab.

It follows docs/shopcloud-interface.md (the contract) and adds a product
catalogue and a web page. It is separate from the platform: its own SQLite
database, no platform code.

Run locally (from shopcloud/):
    SHOP_DB_PATH=./data/shop.db uvicorn --factory shop.main:create_app --port 8000
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .db import Database
from .integration import EventBuffer, build_router
from .security import (
    DUMMY_HASH,
    RateLimit,
    new_token,
    token_hash,
    valid_rate_limit,
    verify_password,
)

SERVICE = "shopcloud"
SESSION_SECONDS = 8 * 3600
STATIC = Path(__file__).parent / "static"

# Requests under these prefixes (and the page at /) get one JSON log line each.
LOGGED_PREFIXES = ("/api/", "/lab/")

# A request that no handler described (unknown path, wrong method) still gets a line.
_FALLBACK_EVENTS = {404: "not_found", 405: "method_not_allowed"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_log(record: dict) -> None:
    """One JSON object per line on stdout (interface section 5). Never passwords or tokens."""
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()


def note(request: Request, event: str, level: str, message: str, username: Optional[str] = None) -> None:
    """Describe this request for its log line; the middleware writes it with the real status."""
    request.state.log = {"event": event, "level": level, "message": message, "username": username}


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def create_app(
    db_path: Optional[str] = None,
    lab_token: Optional[str] = None,
    clock=time.monotonic,
    *,
    integration_key: Optional[str] = None,
    instance_id: Optional[str] = None,
    reset_on_start: Optional[bool] = None,
    wall_clock=time.time,
) -> FastAPI:
    if reset_on_start is None:
        # The bundled lab restores the baseline at every start (the default); the
        # standalone deployment keeps its data in a volume (SHOP_RESET_ON_START=false).
        reset_on_start = _env_flag("SHOP_RESET_ON_START", True)
    db = Database(db_path or os.environ.get("SHOP_DB_PATH", "/app/data/shop.db"), reset_on_start=reset_on_start)
    lab_control_token = lab_token or os.environ.get("LAB_CONTROL_TOKEN", "lab-dev-token")
    rate_limit = RateLimit(clock=clock)
    # CloudAttackLab integration (shop/integration.py): off unless a key is set.
    integration_key = integration_key if integration_key is not None else os.environ.get("INTEGRATION_KEY", "")
    instance_id = instance_id or os.environ.get("INTEGRATION_INSTANCE_ID") or f"shopcloud-{secrets.token_hex(4)}"
    events = EventBuffer(instance_id)

    app = FastAPI(title="ShopCloud", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db = db
    app.state.rate_limit = rate_limit
    app.state.events = events

    # --- logging and security headers --------------------------------------

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        request.state.log = None
        response = await call_next(request)
        path = request.url.path
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if path == "/" or path.startswith(LOGGED_PREFIXES):
            described = request.state.log or {
                "event": _FALLBACK_EVENTS.get(response.status_code, "request_failed"),
                "level": "warning",
                "message": f"No such endpoint: {request.method} {path}"
                if response.status_code in _FALLBACK_EVENTS
                else "Request failed",
                "username": None,
            }
            record = {
                "timestamp": _now(),
                "service": SERVICE,
                "level": described["level"],
                "event": described["event"],
                "request_id": str(uuid.uuid4()),
                "source_ip": request.client.host if request.client else None,
                "method": request.method,
                "path": path,
                "status": response.status_code,
                "username": described["username"],
                "message": described["message"],
            }
            write_log(record)
            events.append(record)  # the same record, for the integration feed
        return response

    async def read_json(request: Request) -> object:
        try:
            return await request.json()
        except Exception:
            return None

    def source_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def current_user(request: Request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or not header[7:]:
            return None
        with db.connect() as conn:
            return conn.execute(
                "SELECT users.id, users.username, users.role FROM sessions JOIN users ON users.id = sessions.user_id"
                " WHERE sessions.token_hash = ? AND sessions.expires_at > ?",
                (token_hash(header[7:]), time.time()),
            ).fetchone()

    def lab_authorized(request: Request) -> bool:
        return secrets.compare_digest(request.headers.get("x-lab-token", ""), lab_control_token)

    # --- interface endpoints (docs/shopcloud-interface.md) -----------------

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/login")
    async def login(request: Request):
        body = await read_json(request)
        body = body if isinstance(body, dict) else {}
        username = body.get("username") if isinstance(body.get("username"), str) else None
        password = body.get("password")

        if rate_limit.blocked(source_ip(request)):
            note(request, "login_rate_limited", "warning", "Login blocked by rate limit", username)
            return JSONResponse({"error": "too_many_attempts"}, status_code=429)

        with db.connect() as conn:
            user = conn.execute(
                "SELECT id, username, password_hash, role FROM users WHERE username = ?", (username or "",)
            ).fetchone()
        password_ok = isinstance(password, str) and verify_password(
            password, user["password_hash"] if user else DUMMY_HASH
        )
        if user is None or not password_ok:
            note(request, "login_failed", "warning", "Invalid username or password", username)
            return JSONResponse({"error": "invalid_credentials"}, status_code=401)

        token = new_token()
        with db.transaction() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (time.time(),))
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (token_hash(token), user["id"], time.time() + SESSION_SECONDS),
            )
        note(request, "login_success", "info", "Login succeeded", user["username"])
        return {"token": token, "username": user["username"], "role": user["role"]}

    @app.get("/api/orders")
    def orders(request: Request):
        user = current_user(request)
        if user is None:
            note(request, "access_denied", "warning", "Missing or invalid token")
            return JSONResponse({"error": "not_authenticated"}, status_code=401)
        if user["role"] != "user":
            note(request, "access_denied", "warning", "Role is not allowed to read orders", user["username"])
            return JSONResponse({"error": "forbidden"}, status_code=403)
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT orders.id, products.name AS item, orders.quantity, orders.total_cents, orders.status,"
                " orders.created_at FROM orders JOIN products ON products.id = orders.product_id"
                " WHERE orders.user_id = ? ORDER BY orders.id",
                (user["id"],),
            ).fetchall()
        result = [
            {
                "id": r["id"],
                "item": r["item"],
                "quantity": r["quantity"],
                "total": r["total_cents"] / 100,
                "status": r["status"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
        note(request, "access_granted", "info", f"Orders read ({len(result)})", user["username"])
        return {"orders": result}

    # --- lab control (X-Lab-Token) -----------------------------------------

    def lab_denied(request: Request) -> JSONResponse:
        note(request, "access_denied", "warning", "Bad lab control token")
        return JSONResponse({"error": "forbidden"}, status_code=403)

    @app.get("/lab/rate-limit")
    def get_rate_limit(request: Request):
        if not lab_authorized(request):
            return lab_denied(request)
        settings = rate_limit.current()
        note(request, "rate_limit_read", "info", f"Rate limit is {settings}")
        return settings

    @app.post("/lab/rate-limit")
    async def set_rate_limit(request: Request):
        if not lab_authorized(request):
            return lab_denied(request)
        settings = valid_rate_limit(await read_json(request), rate_limit.current())
        if settings is None:
            note(request, "rate_limit_rejected", "warning", "Invalid rate limit settings")
            return JSONResponse({"error": "invalid_settings"}, status_code=422)
        saved = rate_limit.configure(settings)
        note(request, "rate_limit_changed", "info", f"Rate limit set to {saved}")
        return saved

    @app.post("/lab/reset")
    def lab_reset(request: Request):
        if not lab_authorized(request):
            return lab_denied(request)
        db.reset()
        rate_limit.reset()
        note(request, "lab_reset", "info", "Data, sessions and rate limit restored to baseline")
        return {"status": "reset"}

    # --- the shop itself: products and the web page ------------------------

    def product_json(row) -> dict:
        return {
            "id": row["id"],
            "sku": row["sku"],
            "name": row["name"],
            "description": row["description"],
            "price": row["price_cents"] / 100,
            "in_stock": row["stock"] > 0,
        }

    @app.get("/api/products")
    def list_products(request: Request):
        with db.connect() as conn:
            rows = conn.execute("SELECT * FROM products ORDER BY id").fetchall()
        note(request, "products_listed", "info", f"Products listed ({len(rows)})")
        return {"products": [product_json(r) for r in rows]}

    @app.get("/api/products/{product_id}")
    def get_product(request: Request, product_id: str):
        row = None
        if product_id.isdecimal() and len(product_id) < 10:
            with db.connect() as conn:
                row = conn.execute("SELECT * FROM products WHERE id = ?", (int(product_id),)).fetchone()
        if row is None:
            note(request, "product_not_found", "info", "No such product")
            return JSONResponse({"error": "not_found"}, status_code=404)
        note(request, "product_viewed", "info", f"Product {row['id']} viewed")
        return product_json(row)

    @app.get("/")
    def page(request: Request):
        note(request, "page_viewed", "info", "Shop page viewed")
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    if integration_key:
        if len(integration_key) < 32:
            raise ValueError("INTEGRATION_KEY must be at least 32 characters")
        app.include_router(
            build_router(key=integration_key, buffer=events, write_log=write_log, now=_now, wall_clock=wall_clock)
        )

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
