"""The interface between the Lab Manager and whatever runs the lab.

DockerComposeAdapter (docker_adapter.py) is the only implementation today.
A Kubernetes adapter would implement the same four methods.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .container import ContainerInfo, ExecResult  # noqa: F401  (used in type hints)


class LabAdapterError(Exception):
    """The lab runtime could not do what was asked."""


@dataclass(frozen=True)
class ContainerStatus:
    service: str            # e.g. "shopcloud"
    name: str               # runtime name of the container / pod
    container_id: str
    state: str              # e.g. "running", "exited"
    health: Optional[str]   # "healthy", "unhealthy", "starting", or None if there is no health check


class LabAdapter(ABC):
    @abstractmethod
    def start(self) -> None:
        """Start the lab and return once its workloads are up and healthy."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the lab's workloads."""

    @abstractmethod
    def reset(self) -> None:
        """Throw away the lab's workloads and create fresh ones, healthy on return."""

    @abstractmethod
    def status(self) -> list[ContainerStatus]:
        """Current workloads, including stopped ones. Empty if nothing exists."""

    @abstractmethod
    def logs(self, service: str, since: Optional[datetime] = None) -> str:
        """Raw stdout logs of one service, oldest first, optionally only since a time.

        One line per record. The caller parses them; the adapter never interprets them.
        """

    # --- the test container (container scenario) -------------------------
    # Not abstract: a runtime without a test container simply does not offer them.
    def exec_marker(self, service: str) -> "ExecResult":
        """Run the fixed MARKER_COMMAND (container.py) in the service's test container.

        There is deliberately no way to pass a command. Raises TargetRefused if
        the service's container is not the lab's test container.
        """
        raise LabAdapterError("this lab runtime has no test container")

    def container_events(self, since: datetime, until: datetime) -> list[str]:
        """Runtime events (start, exec_start, die, ...) of the lab's test containers
        between two times, one JSON object per line, oldest first."""
        raise LabAdapterError("this lab runtime has no container events")

    def inspect(self, container: str) -> "ContainerInfo":
        """Id, name, running state and labels of one container (id or name)."""
        raise LabAdapterError("this lab runtime cannot inspect containers")

    def stop_test_container(self, container: str) -> "ContainerInfo":
        """Stop one container, only if it is a test container of this lab.

        Raises TargetRefused, before touching anything, for any other container.
        Returns the container as it was before stopping.
        """
        raise LabAdapterError("this lab runtime cannot stop containers")

    def recreate_service(self, service: str) -> None:
        """Throw one service's container away and create a fresh one."""
        raise LabAdapterError("this lab runtime cannot recreate a service")
