"""Which rules a run is checked against, and with what threshold and window.

An attack scenario (application or container) names one rule in its
`detection` section, with the threshold and window for it.

An observation scenario names none: its run is checked against every rule.
Each rule gets the threshold and window of the scenario that uses it (the
lowest threshold if several do), or the rule's own defaults if no scenario uses
it. That way an observation measures exactly what the attack scenarios'
detection would raise on the same traffic. The list is fixed when the run
starts and kept in the run's parameters (`detection_rules`), so the run's
report and risk scores read the values that were actually used.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Optional

from ..schemas.scenario import ScenarioDefinition
from .rules import RuleDefinition

OBSERVATION = "observation"
PARAMETER = "detection_rules"


@dataclass(frozen=True)
class RuleCheck:
    rule: str
    threshold: int
    window_seconds: int
    taken_from: str  # "scenario <id>" or "rule default"


def observation_checks(rules: dict[str, RuleDefinition], scenarios: Iterable[ScenarioDefinition]) -> list[RuleCheck]:
    """One check per loaded rule, for an observation run (see the module docstring)."""
    used: dict[str, RuleCheck] = {}
    for scenario in sorted(scenarios, key=lambda s: s.id):
        detection = scenario.detection
        if detection is None or detection.rule not in rules:
            continue
        current = used.get(detection.rule)
        if current is None or detection.threshold < current.threshold:
            used[detection.rule] = RuleCheck(
                detection.rule, detection.threshold, detection.window_seconds, f"scenario {scenario.id}"
            )
    return [
        used.get(rule_id)
        or RuleCheck(rule_id, rule.default_threshold, rule.default_window_seconds, "rule default")
        for rule_id, rule in sorted(rules.items())
    ]


def as_parameter(checks: list[RuleCheck]) -> list[dict[str, Any]]:
    return [asdict(check) for check in checks]


def run_checks(scenario: ScenarioDefinition, parameters: Optional[dict[str, Any]]) -> list[RuleCheck]:
    """The rules a run of `scenario` is checked against (parameters: the run's stored parameters)."""
    if scenario.type == OBSERVATION:
        rows = (parameters or {}).get(PARAMETER) or []
        return [
            RuleCheck(str(row["rule"]), int(row["threshold"]), int(row["window_seconds"]), str(row.get("taken_from", "")))
            for row in rows
            if isinstance(row, dict) and {"rule", "threshold", "window_seconds"} <= row.keys()
        ]
    detection = scenario.detection
    if detection is None:
        return []
    return [RuleCheck(detection.rule, detection.threshold, detection.window_seconds, f"scenario {scenario.id}")]


def threshold_for(scenario: ScenarioDefinition, parameters: Optional[dict[str, Any]], rule_id: Optional[str]) -> Optional[int]:
    """The threshold a run used for `rule_id` (an attack scenario has only its own rule)."""
    checks = run_checks(scenario, parameters)
    if scenario.type != OBSERVATION:
        return checks[0].threshold if checks else None
    return next((check.threshold for check in checks if check.rule == rule_id), None)
