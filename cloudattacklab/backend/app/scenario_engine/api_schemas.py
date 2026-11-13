"""Request and response bodies for the Scenarios and runs endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

from ..schemas.scenario import ScenarioDefinition

# scenario_id / rule_id are names like "authentication_abuse" (api-contract.md).
ScenarioId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9_]*$")]


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: ScenarioId


class ScenarioSummary(BaseModel):
    id: str
    name: str
    category: str
    description: str
    version: str
    target: str
    type: str

    @classmethod
    def from_definition(cls, scenario: ScenarioDefinition) -> "ScenarioSummary":
        return cls(
            id=scenario.id,
            name=scenario.name,
            category=scenario.category,
            description=scenario.description,
            version=scenario.version,
            target=scenario.target,
            type=scenario.type,
        )


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    lab_id: UUID
    scenario_id: str
    scenario_version: str
    status: str
    parameters: dict[str, Any]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    error: Optional[str]
    summary: Optional[dict[str, Any]]
