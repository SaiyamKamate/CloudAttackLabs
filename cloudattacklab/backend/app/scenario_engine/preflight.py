"""Pre-flight checks run before any scenario run, from the scenario's `prerequisites`.

A failed check refuses the run with 409 and says which check failed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..errors import ApiError
from ..lab_manager.adapter import LabAdapter, LabAdapterError
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
    target: Optional[TargetClient],
    test_accounts: dict[str, str],
    *,
    adapter: Optional[LabAdapter] = None,
    container_targets: Optional[dict[str, str]] = None,
) -> list[CheckResult]:
    """Run every prerequisite. Raises ApiError(409) on the first failure.

    For a container scenario `target` is None and `container_running` asks the
    lab adapter whether the named container target's service is running.
    """
    results: list[CheckResult] = []
    for prerequisite in scenario.prerequisites:
        if scenario.type == "container":
            result = _run_container_check(
                prerequisite.check, prerequisite.detail or scenario.target, adapter, container_targets or {}
            )
        else:
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


def _run_container_check(
    check: str, detail: str, adapter: Optional[LabAdapter], container_targets: dict[str, str]
) -> CheckResult:
    if check != PrerequisiteCheck.container_running.value:
        return CheckResult(check, detail, False, f"'{check}' is not a check a container scenario can use")
    service = container_targets.get(detail)
    if service is None:
        return CheckResult(check, detail, False, f"'{detail}' is not a configured container target")
    if adapter is None:
        return CheckResult(check, detail, False, "no lab adapter to ask")
    try:
        containers = adapter.status()
    except LabAdapterError as exc:
        return CheckResult(check, detail, False, f"cannot read the lab's containers: {exc}")
    found = next((c for c in containers if c.service == service), None)
    if found is None:
        return CheckResult(check, detail, False, f"the lab has no '{service}' container; start or reset the lab")
    if found.state != "running":
        return CheckResult(check, detail, False, f"the '{service}' container is {found.state}; run cleanup or reset the lab")
    return CheckResult(check, detail, True, f"the '{service}' container is running")


def _health_message(health) -> str:
    if health.healthy:
        return "target health is ok"
    if not health.reachable:
        return f"target is not reachable: {health.error}"
    return f"target health returned HTTP {health.status_code}"
