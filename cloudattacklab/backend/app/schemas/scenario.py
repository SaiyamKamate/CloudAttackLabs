"""Scenario definition format (guide section 6) and the loader that validates it.

A scenario = one YAML file in /scenarios + one execution script.
Nothing runs unless the YAML passes load_scenario().
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .event import EventSource, EventType

# Hard safety bounds. A scenario file cannot go beyond these.
MAX_ATTEMPTS = 200
MIN_INTERVAL_SECONDS = 0.1
MAX_TIMEOUT_SECONDS = 600

ID_PATTERN = r"^[a-z][a-z0-9_]*$"


class ScenarioError(Exception):
    """The scenario file is missing, malformed, or breaks a safety rule."""


class ScenarioType(str, Enum):
    application = "application"
    container = "container"
    # Runs no script and sends nothing: it only collects the lab's telemetry for
    # `execution.parameters.duration_seconds` and checks it against every
    # detection rule. Used to measure what normal traffic, sent from outside the
    # platform, would raise (false positives).
    observation = "observation"


class Capability(str, Enum):
    """What a target must offer for a scenario to be compatible with it."""

    login_endpoint = "login_endpoint"
    protected_resource = "protected_resource"
    rate_limit_control = "rate_limit_control"
    container_target = "container_target"
    falco = "falco"


class PrerequisiteCheck(str, Enum):
    http_health = "http_health"              # target health endpoint answers 200
    container_running = "container_running"  # named container is up
    test_account = "test_account"            # named test account can log in


class ResponseActionType(str, Enum):
    temporary_rate_limit = "temporary_rate_limit"
    stop_container = "stop_container"
    isolate_container = "isolate_container"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class Prerequisite(_Strict):
    check: PrerequisiteCheck
    detail: Optional[str] = Field(default=None, description="Account or container name, if the check needs one.")


class Execution(_Strict):
    script: Optional[str] = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]*\.py$",
        description="File name only, inside the scenarios scripts folder. Required, except for an observation scenario, which has none.",
    )
    timeout_seconds: int = Field(default=60, ge=1, le=MAX_TIMEOUT_SECONDS)
    parameters: dict[str, Union[bool, int, float, str]] = Field(default_factory=dict)

    @field_validator("parameters")
    @classmethod
    def _bounded(cls, params: dict) -> dict:
        attempts = params.get("attempts")
        if attempts is not None and not (isinstance(attempts, int) and 1 <= attempts <= MAX_ATTEMPTS):
            raise ValueError(f"attempts must be a whole number from 1 to {MAX_ATTEMPTS}")
        interval = params.get("interval_seconds")
        if interval is not None and not (isinstance(interval, (int, float)) and interval >= MIN_INTERVAL_SECONDS):
            raise ValueError(f"interval_seconds must be at least {MIN_INTERVAL_SECONDS}")
        return params


class ExpectedEvent(_Strict):
    source: EventSource
    event_type: EventType
    min_count: int = Field(default=1, ge=1)


class Detection(_Strict):
    rule: str = Field(pattern=ID_PATTERN)
    threshold: int = Field(ge=1)
    window_seconds: int = Field(default=60, ge=1, le=3600)


class Verification(_Strict):
    check: str = Field(pattern=ID_PATTERN, description="Name of the follow-up check, e.g. login_returns_429.")
    description: str


class Response(_Strict):
    action: ResponseActionType
    requires_approval: bool = True
    parameters: dict[str, Union[bool, int, float, str]] = Field(default_factory=dict)
    verification: Verification

    @field_validator("requires_approval")
    @classmethod
    def _approval_is_mandatory(cls, value: bool) -> bool:
        if not value:
            raise ValueError("response actions must require approval (guide section 13)")
        return value


class Cleanup(_Strict):
    enabled: bool = True
    steps: list[str] = Field(min_length=1, description="Named cleanup steps, run in order.")

    @field_validator("enabled")
    @classmethod
    def _cleanup_is_mandatory(cls, value: bool) -> bool:
        if not value:
            raise ValueError("every scenario must have cleanup enabled (guide section 13)")
        return value


class ScenarioDefinition(_Strict):
    schema_version: str = "1.0"
    id: str = Field(pattern=ID_PATTERN)
    name: str = Field(min_length=3)
    category: str = Field(min_length=3)
    description: str = Field(min_length=10)
    version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    target: str = Field(pattern=ID_PATTERN, description="Must be one of the lab's configured targets.")
    type: ScenarioType
    requires: list[Capability] = Field(default_factory=list)
    prerequisites: list[Prerequisite] = Field(min_length=1)
    execution: Execution
    expected_telemetry: list[ExpectedEvent] = Field(min_length=1)
    # Required, except for an observation scenario: that one is checked against
    # every rule, with the threshold of the scenario that uses each rule.
    detection: Optional[Detection] = None
    response: Optional[Response] = None
    cleanup: Cleanup

    @model_validator(mode="after")
    def _type_rules(self) -> "ScenarioDefinition":
        if self.type != ScenarioType.observation.value:
            if self.execution.script is None:
                raise ValueError("execution.script is required (only an observation scenario has no script)")
            if self.detection is None:
                raise ValueError("detection is required (only an observation scenario has none)")
            return self
        if self.execution.script is not None:
            raise ValueError("an observation scenario runs no script: remove execution.script")
        if self.detection is not None:
            raise ValueError("an observation scenario is checked against every rule: remove detection")
        if self.response is not None:
            raise ValueError("an observation scenario has no response action")
        duration = self.execution.parameters.get("duration_seconds")
        if not (isinstance(duration, int) and not isinstance(duration, bool)
                and 1 <= duration <= self.execution.timeout_seconds):
            raise ValueError(
                "an observation scenario needs execution.parameters.duration_seconds, "
                "a whole number from 1 to execution.timeout_seconds"
            )
        return self


def load_scenario(path: Union[str, Path], allowed_targets: set[str]) -> ScenarioDefinition:
    """Read and validate one scenario file.

    allowed_targets comes from the platform's lab configuration. A scenario
    naming any other target is rejected, so it can never point outside the lab.
    """
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScenarioError(f"scenario file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{path.name}: not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ScenarioError(f"{path.name}: top level must be a mapping of fields")

    try:
        scenario = ScenarioDefinition.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'file'}: {err['msg']}" for err in exc.errors()
        )
        raise ScenarioError(f"{path.name}: {problems}") from exc

    if scenario.id != path.stem:
        raise ScenarioError(f"{path.name}: id '{scenario.id}' must match the file name '{path.stem}'")
    if scenario.target not in allowed_targets:
        raise ScenarioError(
            f"{path.name}: target '{scenario.target}' is not a configured lab target {sorted(allowed_targets)}"
        )
    return scenario
