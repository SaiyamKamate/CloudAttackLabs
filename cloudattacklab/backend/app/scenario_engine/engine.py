"""Loads every scenario file in the scenarios directory.

The allowed targets come from the lab configuration (set once at construction),
never from a request, so a scenario can never name a target outside the lab. A
file that fails validation is skipped and logged, and never becomes runnable.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..errors import ApiError
from ..schemas.scenario import ScenarioDefinition, ScenarioError, load_scenario

log = logging.getLogger("cloudattacklab.scenarios")


class ScenarioEngine:
    def __init__(self, scenarios_dir: Path, allowed_targets: set[str]) -> None:
        self.scenarios_dir = Path(scenarios_dir)
        self.allowed_targets = set(allowed_targets)

    def load_all(self) -> dict[str, ScenarioDefinition]:
        """Every valid scenario, keyed by id. Invalid files are skipped and logged."""
        valid: dict[str, ScenarioDefinition] = {}
        if not self.scenarios_dir.is_dir():
            log.warning("scenarios directory not found: %s", self.scenarios_dir)
            return valid
        for path in sorted(self.scenarios_dir.glob("*.yaml")):
            try:
                scenario = load_scenario(path, self.allowed_targets)
            except ScenarioError as exc:
                log.warning("skipping invalid scenario %s: %s", path.name, exc)
                continue
            valid[scenario.id] = scenario
        return valid

    def list(self) -> list[ScenarioDefinition]:
        return list(self.load_all().values())

    def get(self, scenario_id: str) -> ScenarioDefinition:
        scenario = self.load_all().get(scenario_id)
        if scenario is None:
            raise ApiError(404, "scenario_not_found", f"No valid scenario named '{scenario_id}'.")
        return scenario
