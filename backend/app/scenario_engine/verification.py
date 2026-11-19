"""Post-run verification: repeat a scenario's check once and report what happened.

A scenario asks for it by naming a check in `execution.parameters`:

    verification_check: access_remains_denied

This is for scenarios with no response action (a response action has its own
verification, run by the Response Manager). The target is always the lab's
configured target; nothing here comes from a request.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from ..lab_manager.target import TargetClient
from ..schemas.scenario import ScenarioDefinition

log = logging.getLogger("cloudattacklab.scenarios")

PARAMETER = "verification_check"


def access_remains_denied(target: TargetClient, *, username: str, password: str) -> dict[str, Any]:
    """One more GET /api/orders as `username`. Passes only if it is refused with 403."""
    result = target.read_orders(username, password)
    still_denied = result.status_code == 403
    return {
        "check": "access_remains_denied",
        "passed": still_denied,
        "still_denied": still_denied,
        "sent": {"request": "GET /api/orders", "username": username},
        "received": {"login_status": result.login_status, "status": result.status_code},
        "error": result.error,
    }


CHECKS: dict[str, Callable[..., dict[str, Any]]] = {"access_remains_denied": access_remains_denied}


def run_verification(
    scenario: ScenarioDefinition, target: Optional[TargetClient], test_accounts: dict[str, str]
) -> Optional[dict[str, Any]]:
    """The verification result for this scenario, or None if it names no check.

    Never raises: a check that cannot be run is reported as not passed, with why.
    """
    name = scenario.execution.parameters.get(PARAMETER)
    if not name:
        return None
    username = str(scenario.execution.parameters.get("username", ""))
    failed = {"check": str(name), "passed": False, "still_denied": None}

    check = CHECKS.get(str(name))
    if check is None:
        return {**failed, "error": f"unknown verification check '{name}'"}
    if target is None:
        return {**failed, "error": f"'{scenario.target}' is not a configured lab target"}
    password = test_accounts.get(username)
    if password is None:
        return {**failed, "error": f"no credentials configured for account '{username}'"}
    try:
        return check(target, username=username, password=password)
    except Exception as exc:  # the run record must still be written
        log.exception("verification '%s' failed to run", name)
        return {**failed, "error": f"{type(exc).__name__}: {exc}"}
