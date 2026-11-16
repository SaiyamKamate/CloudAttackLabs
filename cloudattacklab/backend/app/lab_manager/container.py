"""The lab's test container: its fixed marker command and the safety checks.

The container scenario runs exactly one command in the test container, and
that command is the constant below. It is fixed in code: it is not read from
scenario parameters, the API request or anywhere else, and the adapter offers
no way to run any other command.

Only a container that carries TEST_TARGET_LABEL and belongs to the lab's own
Compose project may be exec'd into or stopped. ShopCloud, Falco and every
platform container (backend, dashboard, PostgreSQL) fail that check.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Appears in the command line of the marker process; the lab's Falco rule
# (lab/falco/cloudattacklab_rules.yaml) matches on it.
MARKER = "cloudattacklab-scenario-marker"
# A shell that prints the user id and exits. `:` is the shell's no-op; the
# marker after it changes nothing but makes the process easy to recognise.
MARKER_COMMAND: tuple[str, ...] = ("sh", "-c", f"id; : {MARKER}")

TEST_TARGET_LABEL = ("cloudattacklab.role", "test-target")
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"


class TargetRefused(Exception):
    """The container is not the lab's test container; nothing was done to it."""


@dataclass(frozen=True)
class ContainerInfo:
    id: str                      # full container id
    name: str
    running: bool
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def short_id(self) -> str:
        return self.id[:12]

    @property
    def project(self) -> Optional[str]:
        return self.labels.get(COMPOSE_PROJECT_LABEL)

    @property
    def service(self) -> Optional[str]:
        return self.labels.get(COMPOSE_SERVICE_LABEL)

    @property
    def is_test_target(self) -> bool:
        key, value = TEST_TARGET_LABEL
        return self.labels.get(key) == value


@dataclass(frozen=True)
class ExecResult:
    command: tuple[str, ...]
    exit_code: int
    output: str


def check_test_container(info: ContainerInfo, lab_project: str) -> None:
    """Raise TargetRefused unless `info` is a test container of this lab's project."""
    key, value = TEST_TARGET_LABEL
    if not info.is_test_target:
        raise TargetRefused(
            f"container {info.name or info.short_id} does not have the label {key}={value}; "
            "only the lab's test container may be targeted"
        )
    if info.project != lab_project:
        raise TargetRefused(
            f"container {info.name or info.short_id} belongs to Compose project '{info.project}', "
            f"not to the lab project '{lab_project}'"
        )
