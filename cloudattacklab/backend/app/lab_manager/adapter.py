"""The interface between the Lab Manager and whatever runs the lab.

DockerComposeAdapter (docker_adapter.py) is the only implementation today.
A Kubernetes adapter would implement the same four methods.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


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
