"""Detection rule format and loader. A rule file is validated when loaded; an
invalid file is skipped and logged, and can never fire."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..schemas.event import EventType, Severity

log = logging.getLogger("cloudattacklab.detection")

ID_PATTERN = r"^[a-z][a-z0-9_]*$"


class RuleError(Exception):
    """The rule file is missing, malformed, or breaks a rule."""


class RuleDefinition(BaseModel):
    """A count-over-time rule.

    It fires when one value of `group_by` produces `threshold` events of
    `event_type` within `window_seconds`. threshold/window usually come from the
    scenario; the values here are fallback defaults.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    id: str = Field(pattern=ID_PATTERN)
    event_type: EventType
    group_by: str = Field(default="source_ip", pattern=r"^(source_ip|username)$")
    severity: Severity = Severity.medium
    title: str = Field(min_length=3)
    description: str = Field(min_length=3)
    default_threshold: int = Field(default=10, ge=1)
    default_window_seconds: int = Field(default=60, ge=1, le=3600)


def load_rule(path: Union[str, Path]) -> RuleDefinition:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuleError(f"rule file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise RuleError(f"{path.name}: not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RuleError(f"{path.name}: top level must be a mapping of fields")
    try:
        rule = RuleDefinition.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'file'}: {err['msg']}" for err in exc.errors()
        )
        raise RuleError(f"{path.name}: {problems}") from exc
    if rule.id != path.stem:
        raise RuleError(f"{path.name}: id '{rule.id}' must match the file name '{path.stem}'")
    return rule


def load_rules(rules_dir: Union[str, Path]) -> dict[str, RuleDefinition]:
    """Every valid rule, keyed by id. Invalid files are skipped and logged."""
    rules_dir = Path(rules_dir)
    loaded: dict[str, RuleDefinition] = {}
    if not rules_dir.is_dir():
        log.warning("rules directory not found: %s", rules_dir)
        return loaded
    for path in sorted(rules_dir.glob("*.yaml")):
        try:
            rule = load_rule(path)
        except RuleError as exc:
            log.warning("skipping invalid rule %s: %s", path.name, exc)
            continue
        loaded[rule.id] = rule
    return loaded
