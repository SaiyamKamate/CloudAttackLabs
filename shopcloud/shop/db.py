"""ShopCloud's own SQLite database: schema, synthetic seed data, and the reset.

Separate from the platform: a file inside the container, stdlib sqlite3 only.
Everything here is synthetic test data.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .security import hash_password

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    stock INTEGER NOT NULL CHECK (stock >= 0)
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'restricted'))
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    total_cents INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
-- Login sessions. Only a SHA-256 of each token is stored, never the token.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    expires_at REAL NOT NULL
);
"""

# --- Synthetic seed data (the baseline /lab/reset restores) -----------------

PRODUCTS = [
    (1, "KB-100", "Mechanical Keyboard", "Tenkeyless keyboard with brown switches.", 4900, 25),
    (2, "MS-200", "Wireless Mouse", "Six-button mouse with a USB receiver.", 1900, 60),
    (3, "MN-270", "27-inch Monitor", "1440p IPS panel, height-adjustable stand.", 27900, 8),
    (4, "HS-300", "Headset", "Closed-back headset with a boom microphone.", 5900, 30),
    (5, "CB-010", "USB-C Cable", "1 m braided USB-C to USB-C cable.", 900, 200),
]

# The two accounts from docs/shopcloud-interface.md, section 2.
ACCOUNTS = [
    (1, "lab_user", "lab-user-pass", "user"),
    (2, "lab_restricted", "lab-restricted-pass", "restricted"),
]

# A few orders, all belonging to lab_user (user id 1).
ORDERS = [
    (1, 1, 1, 1, 4900, "delivered", "2026-10-02T09:15:00Z"),
    (2, 1, 2, 2, 3800, "delivered", "2026-10-19T17:40:00Z"),
    (3, 1, 5, 3, 2700, "shipped", "2026-11-10T11:05:00Z"),
]


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()  # one writer at a time; the shop is small
        # Hash the seed passwords once per process; reset reuses the hashes.
        self._seed_accounts = [(i, u, hash_password(p), r) for i, u, p, r in ACCOUNTS]
        with self.connect() as conn:
            conn.executescript(SCHEMA)
        self.reset()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def reset(self) -> None:
        """Restore the seeded baseline: every table emptied and refilled, all sessions gone."""
        with self.transaction() as conn:
            for table in ("sessions", "orders", "users", "products"):
                conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed table names
            conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?)", PRODUCTS)
            conn.executemany("INSERT INTO users VALUES (?, ?, ?, ?)", self._seed_accounts)
            conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?)", ORDERS)
