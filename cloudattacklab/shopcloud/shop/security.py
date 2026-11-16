"""Password hashing, login tokens and the login rate limit."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

# scrypt (stdlib). n=2**14, r=8: about 16 MB and tens of milliseconds per hash.
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(digest) // 2
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual.hex(), digest)


# Checked when the username does not exist, so a missing account takes as long as a wrong password.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RateLimit:
    """The lab's login rate limit: off at baseline; when on, `max_attempts` per source IP per window."""

    BASELINE = {"enabled": False, "max_attempts": 5, "window_seconds": 60}

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.settings = dict(self.BASELINE)
            self._attempts: dict[str, deque] = defaultdict(deque)

    def configure(self, settings: dict) -> dict:
        with self._lock:
            self.settings = dict(settings)
            self._attempts.clear()
            return dict(self.settings)

    def current(self) -> dict:
        with self._lock:
            return dict(self.settings)

    def blocked(self, source_ip: str) -> bool:
        """Record a login attempt from `source_ip`; True if it must be refused (and then it is not counted)."""
        with self._lock:
            if not self.settings["enabled"]:
                return False
            now = self._clock()
            attempts = self._attempts[source_ip]
            while attempts and now - attempts[0] >= self.settings["window_seconds"]:
                attempts.popleft()
            if len(attempts) >= self.settings["max_attempts"]:
                return True
            attempts.append(now)
            return False


def valid_rate_limit(body: object, current: dict) -> dict | None:
    """The new settings, or None if invalid (the endpoint then answers 422)."""
    if not isinstance(body, dict):
        return None
    enabled = body.get("enabled")
    max_attempts = body.get("max_attempts", current["max_attempts"])
    window = body.get("window_seconds", current["window_seconds"])
    if not isinstance(enabled, bool):
        return None
    if type(max_attempts) is not int or not 1 <= max_attempts <= 1000:
        return None
    if type(window) is not int or not 1 <= window <= 3600:
        return None
    return {"enabled": enabled, "max_attempts": max_attempts, "window_seconds": window}
