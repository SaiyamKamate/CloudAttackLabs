"""Checks that a lab target URL (SHOPCLOUD_URL) points into the lab and nowhere else.

A URL is accepted only when it is one of:

1. a loopback address (127.0.0.0/8, ::1 or `localhost`) on a host port that the
   lab compose file (lab/docker-compose.lab.yml) publishes for the target's
   service, for example `http://127.0.0.1:8081`; or
2. the name of a container that belongs to the lab's Compose project
   (`cloudattacklab-lab`) and runs the target's service, for example
   `http://cloudattacklab-lab-shopcloud-1:8000` when the backend shares the lab
   network.

Anything else (another port, another host, a container of another project) is
refused. The backend checks every lab target at startup and again before every
run, lab start, lab reset, response and cleanup, so a scenario's traffic and the
lab control token never leave the lab.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import yaml

from .adapter import LabAdapter, LabAdapterError
from .target import AccessResult, LoginResult, TargetError, TargetHealth


class TargetUrlRefused(Exception):
    """The configured URL is not inside the lab; nothing was sent to it."""


@dataclass(frozen=True)
class TargetUrlCheck:
    target: str
    url: str
    allowed: bool
    error: Optional[str] = None


def published_ports(compose_file: Path, service: str) -> tuple[str, set[int]]:
    """(project name, host ports) that the lab compose file publishes for `service`."""
    try:
        data = yaml.safe_load(Path(compose_file).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise TargetUrlRefused(f"cannot read the lab compose file {compose_file}: {exc}") from exc
    project = data.get("name") if isinstance(data, dict) else None
    services = (data.get("services") or {}) if isinstance(data, dict) else {}
    spec = services.get(service)
    if not isinstance(project, str) or not project:
        raise TargetUrlRefused(f"the lab compose file {compose_file} has no project `name:`")
    if not isinstance(spec, dict):
        raise TargetUrlRefused(f"the lab compose file has no '{service}' service")
    ports: set[int] = set()
    for entry in spec.get("ports") or []:
        port = _host_port(entry)
        if port is not None:
            ports.add(port)
    return project, ports


def _host_port(entry) -> Optional[int]:
    """The published host port of one `ports:` entry ("127.0.0.1:8081:8000", "8081:8000" or long form)."""
    if isinstance(entry, dict):
        value = entry.get("published")
    elif isinstance(entry, str):
        parts = entry.split("/")[0].rsplit(":", 2)
        if len(parts) < 2:
            return None  # only a container port: nothing fixed on the host
        value = parts[-2]
    else:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def check_target_url(
    target: str,
    url: str,
    *,
    compose_file: Path,
    service: Optional[str] = None,
    adapter: Optional[LabAdapter] = None,
) -> TargetUrlCheck:
    """Decide whether `url` is inside the lab. Never sends anything to the URL."""
    service = service or target
    try:
        _check(url, compose_file=compose_file, service=service, adapter=adapter)
    except TargetUrlRefused as exc:
        return TargetUrlCheck(target, url, False, f"Lab target '{target}' URL {url} is refused: {exc}.")
    return TargetUrlCheck(target, url, True)


def _check(url: str, *, compose_file: Path, service: str, adapter: Optional[LabAdapter]) -> None:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise TargetUrlRefused(f"it is not a valid URL ({exc})") from exc
    if parts.scheme not in ("http", "https"):
        raise TargetUrlRefused("only http:// and https:// are allowed")
    if parts.username or parts.password:
        raise TargetUrlRefused("it must not contain a user name or password")
    host = (parts.hostname or "").lower()
    if not host:
        raise TargetUrlRefused("it has no host")
    if port is None:
        port = 443 if parts.scheme == "https" else 80

    project, ports = published_ports(compose_file, service)

    if _is_loopback(host):
        if port in ports:
            return
        published = ", ".join(str(p) for p in sorted(ports)) or "none"
        raise TargetUrlRefused(
            f"port {port} is not the port {compose_file.name} publishes for '{service}' ({published}). "
            "Something else may be listening there, outside the lab"
        )

    if _is_ip(host):
        raise TargetUrlRefused(
            "it is not a loopback address. Use the loopback port the lab publishes "
            f"(for example http://127.0.0.1:{min(ports) if ports else 8081})"
        )

    # A host name: only a container of the lab's own Compose project, running the target service.
    if adapter is None:
        raise TargetUrlRefused(f"'{host}' is not a loopback address and the lab's containers cannot be checked")
    try:
        info = adapter.inspect(host)
    except LabAdapterError as exc:
        raise TargetUrlRefused(
            f"'{host}' is not a loopback address and not a container of the '{project}' project ({exc})"
        ) from exc
    if info.project != project:
        raise TargetUrlRefused(
            f"container '{host}' belongs to Compose project '{info.project}', not to the lab project '{project}'"
        )
    if info.service != service:
        raise TargetUrlRefused(f"container '{host}' runs service '{info.service}', not '{service}'")


class TargetGuard:
    """Checks the configured lab target URLs. Used at startup and before anything is sent."""

    def __init__(self, lab_targets: dict[str, str], compose_file: Path, adapter: Optional[LabAdapter] = None) -> None:
        self.lab_targets = dict(lab_targets)
        self.compose_file = Path(compose_file)
        self.adapter = adapter

    def check(self, target: str) -> TargetUrlCheck:
        url = self.lab_targets.get(target)
        if url is None:
            return TargetUrlCheck(target, "", False, f"'{target}' is not a configured lab target.")
        return check_target_url(target, url, compose_file=self.compose_file, adapter=self.adapter)

    def check_all(self) -> list[TargetUrlCheck]:
        return [self.check(name) for name in sorted(self.lab_targets)]

    def require(self, target: str) -> None:
        """Raise TargetUrlRefused (with the full reason) unless the target's URL is inside the lab."""
        result = self.check(target)
        if not result.allowed:
            raise TargetUrlRefused(result.error)


class GuardedTargetClient:
    """A TargetClient that checks the target's URL before every request.

    Defence in depth behind the explicit checks in the services: whatever calls
    the target (pre-flight, verification, responses, cleanup, lab reset), nothing
    is sent, and the lab control token never leaves the backend, unless the URL
    is inside the lab.
    """

    def __init__(self, inner, target: str, guard: TargetGuard) -> None:
        self._inner = inner
        self._target = target
        self._guard = guard

    @property
    def base_url(self) -> str:
        return getattr(self._inner, "base_url", "")

    def _refusal(self) -> Optional[str]:
        result = self._guard.check(self._target)
        return None if result.allowed else result.error

    def _require(self) -> None:
        error = self._refusal()
        if error:
            raise TargetError(error)

    def health(self) -> TargetHealth:
        error = self._refusal()
        if error:
            return TargetHealth(reachable=False, healthy=False, error=error)
        return self._inner.health()

    def wait_healthy(self, timeout_seconds: float = 30) -> TargetHealth:
        self._require()
        return self._inner.wait_healthy(timeout_seconds)

    def reset(self) -> None:
        self._require()
        self._inner.reset()

    def login(self, username: str, password: str) -> LoginResult:
        error = self._refusal()
        if error:
            return LoginResult(ok=False, error=error)
        return self._inner.login(username, password)

    def read_orders(self, username: str, password: str) -> AccessResult:
        error = self._refusal()
        if error:
            return AccessResult(error=error)
        return self._inner.read_orders(username, password)

    def set_rate_limit(self, enabled: bool, max_attempts=None, window_seconds=None):
        self._require()
        return self._inner.set_rate_limit(enabled, max_attempts, window_seconds)
