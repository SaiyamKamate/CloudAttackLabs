"""Pre-flight checks run before any scenario run, from the scenario's `prerequisites`.

A failed check refuses the run with 409 and says which check failed.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..errors import ApiError
from ..schemas.scenario import PrerequisiteCheck, ScenarioDefinition
from ..lab_manager.target import TargetClient


@dataclass(frozen=True)
class CheckResult:
    check: str
    detail: str | None
    passed: bool
    message: str


def run_preflight(
    scenario: ScenarioDefinition,
    target: TargetClient,
    test_accounts: dict[str, str],
) -> list[CheckResult]:
    """Run every prerequisite. Raises ApiError(409) on the first failure."""
    results: list[CheckResult] = []
    for prerequisite in scenario.prerequisites:
        result = _run_one(prerequisite.check, prerequisite.detail, target, test_accounts)
        results.append(result)
        if not result.passed:
            raise ApiError(
                409,
                "preflight_failed",
                f"pre-flight check '{result.check}' failed: {result.message}",
            )
    return results


def _run_one(
    check: str, detail: str | None, target: TargetClient, test_accounts: dict[str, str]
) -> CheckResult:
    # scenario.py stores enum values as strings (use_enum_values=True).
    if check in (PrerequisiteCheck.http_health.value, PrerequisiteCheck.container_running.value):
        health = target.health()
        return CheckResult(check, detail, health.healthy, _health_message(health))

    if check == PrerequisiteCheck.test_account.value:
        username = detail or ""
        password = test_accounts.get(username)
        if password is None:
            return CheckResult(check, detail, False, f"no credentials configured for account '{username}'")
        result = target.login(username, password)
        if result.ok:
            return CheckResult(check, detail, True, f"account '{username}' can log in")
        reason = result.error or f"login returned HTTP {result.status_code}"
        return CheckResult(check, detail, False, f"account '{username}' cannot log in: {reason}")

    return CheckResult(check, detail, False, f"unsupported prerequisite check '{check}'")


def _health_message(health) -> str:
    if health.healthy:
        return "target health is ok"
    if not health.reachable:
        return f"target is not reachable: {health.error}"
    return f"target health returned HTTP {health.status_code}"
