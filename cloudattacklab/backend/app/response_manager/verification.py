"""Verification checks: follow-up requests that show whether a response worked.

A check returns a JSON-safe dict that is stored as-is in
response_actions.verification_result: what was sent, what came back, and
`passed`.
"""
from __future__ import annotations

from typing import Any, Callable

from ..lab_manager.target import TargetClient

# Deliberately invalid, the same value the scenario script uses. Verification
# never uses a real account password.
WRONG_PASSWORD = "wrong-password-not-real-cloudattacklab"
EXTRA_ATTEMPTS = 2  # logins sent beyond the limit; these are the ones that must be blocked


def login_returns_429(target: TargetClient, *, username: str, max_attempts: int) -> dict[str, Any]:
    """Send max_attempts + 2 wrong logins; the ones beyond max_attempts must all get 429."""
    total = max_attempts + EXTRA_ATTEMPTS
    statuses: list[Any] = []
    errors: list[str] = []
    for _ in range(total):
        result = target.login(username, WRONG_PASSWORD)
        statuses.append(result.status_code)  # None when the request itself failed
        if result.error:
            errors.append(result.error)
    beyond_limit = statuses[max_attempts:]
    outcome: dict[str, Any] = {
        "check": "login_returns_429",
        "passed": all(status == 429 for status in beyond_limit),
        "sent": {
            "request": "POST /api/login",
            "username": username,
            "password": "deliberately wrong (never a real one)",
            "attempts": total,
            "max_attempts": max_attempts,
        },
        "received": {
            "statuses": statuses,
            "blocked_429": sum(1 for status in statuses if status == 429),
            "expected_blocked": EXTRA_ATTEMPTS,
            "blocked_beyond_limit": sum(1 for status in beyond_limit if status == 429),
        },
    }
    if errors:
        outcome["received"]["errors"] = errors
    return outcome


CHECKS: dict[str, Callable[..., dict[str, Any]]] = {"login_returns_429": login_returns_429}
