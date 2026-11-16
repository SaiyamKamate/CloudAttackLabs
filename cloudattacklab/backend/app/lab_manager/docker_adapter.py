"""Docker adapter. This is the only module in the backend that talks to Docker.

Lab control runs `docker compose -f <lab compose file> ...`, so it can touch
the lab project and nothing else on the machine. The container scenario adds
three plain `docker` commands, each scoped to the lab's test container:
`docker events` filtered on the lab project and the test-target label,
`docker inspect`, and `docker stop`, which only runs after the container passed
check_test_container (container.py).

Falco is an optional service in the `falco` Compose profile. It is started only
with FALCO_ENABLED=true; stop, status and logs always include it, so a Falco
left running from earlier is still stopped and shown.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from .adapter import ContainerStatus, LabAdapter, LabAdapterError
from .container import MARKER_COMMAND, ContainerInfo, ExecResult, TEST_TARGET_LABEL, check_test_container

FALCO_PROFILE = "falco"


class DockerComposeAdapter(LabAdapter):
    def __init__(
        self,
        compose_file: Path,
        *,
        docker_bin: str = "docker",
        lab_control_token: str,
        start_timeout_seconds: int = 180,
        falco_enabled: bool = False,
    ) -> None:
        self.compose_file = Path(compose_file)
        self.docker_bin = docker_bin
        self.lab_control_token = lab_control_token
        self.start_timeout_seconds = start_timeout_seconds
        self.falco_enabled = falco_enabled

    # --- LabAdapter -------------------------------------------------------
    def start(self) -> None:
        # --wait returns once the health checks in the compose file pass.
        # Falco is started only when it is enabled.
        self._compose(
            "up", "--detach", "--build", "--wait", "--wait-timeout", str(self.start_timeout_seconds),
            falco=self.falco_enabled,
        )

    def stop(self) -> None:
        # `stop`, not `down`: the stopped container keeps its logs for later inspection.
        self._compose("stop", falco=True)

    def reset(self) -> None:
        # Recreates every container, the test container included: back to baseline.
        self._compose(
            "up", "--detach", "--build", "--force-recreate", "--wait", "--wait-timeout",
            str(self.start_timeout_seconds),
            falco=self.falco_enabled,
        )

    def status(self) -> list[ContainerStatus]:
        output = self._compose("ps", "--all", "--format", "json", timeout=30, falco=True)
        return [
            ContainerStatus(
                service=row.get("Service", ""),
                name=row.get("Name", ""),
                container_id=row.get("ID", ""),
                state=row.get("State", "unknown"),
                health=row.get("Health") or None,
            )
            for row in _parse_ps(output)
        ]

    def logs(self, service: str, since: Optional[datetime] = None) -> str:
        args = ["logs", "--no-color", "--no-log-prefix"]
        if since is not None:
            # RFC3339 UTC, which `docker compose logs --since` accepts.
            args += ["--since", since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")]
        args.append(service)
        return self._compose(*args, timeout=60, falco=True)

    # --- the test container ----------------------------------------------
    @property
    def project(self) -> str:
        """The lab's Compose project name (the compose file's `name:`)."""
        try:
            data = yaml.safe_load(self.compose_file.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise LabAdapterError(f"cannot read the lab compose file {self.compose_file}: {exc}") from exc
        name = data.get("name") if isinstance(data, dict) else None
        if not isinstance(name, str) or not name:
            raise LabAdapterError(f"the lab compose file {self.compose_file} has no project `name:`")
        return name

    def exec_marker(self, service: str) -> ExecResult:
        info = self._service_container(service)
        check_test_container(info, self.project)  # TargetRefused for anything else
        if not info.running:
            raise LabAdapterError(f"the test container {info.name} is not running")
        # `exec -T`: no terminal. The command is the fixed constant; nothing else can be passed.
        result = self._run(
            [self.docker_bin, "compose", "-f", str(self.compose_file), "exec", "-T", service, *MARKER_COMMAND],
            what="docker compose exec",
            timeout=30,
            check=False,
        )
        return ExecResult(command=MARKER_COMMAND, exit_code=result.returncode, output=(result.stdout + result.stderr).strip())

    def container_events(self, since: datetime, until: datetime) -> list[str]:
        key, value = TEST_TARGET_LABEL
        result = self._run(
            [
                self.docker_bin, "events",
                "--since", _unix(since), "--until", _unix(until),
                "--filter", "type=container",
                "--filter", f"label=com.docker.compose.project={self.project}",
                "--filter", f"label={key}={value}",
                "--format", "{{json .}}",
            ],
            what="docker events",
            timeout=30,
        )
        return [line for line in result.stdout.splitlines() if line.strip()]

    def inspect(self, container: str) -> ContainerInfo:
        result = self._run([self.docker_bin, "inspect", "--type", "container", container], what="docker inspect", timeout=30)
        try:
            data = json.loads(result.stdout)[0]
        except (ValueError, IndexError, TypeError) as exc:
            raise LabAdapterError(f"docker inspect returned no container for '{container}'") from exc
        return _container_info(data)

    def stop_test_container(self, container: str) -> ContainerInfo:
        info = self.inspect(container)
        check_test_container(info, self.project)  # refuse before touching anything
        self._run([self.docker_bin, "stop", "--time", "5", info.id], what="docker stop", timeout=60)
        return info

    def recreate_service(self, service: str) -> None:
        self._compose(
            "up", "--detach", "--force-recreate", "--no-deps", "--wait", "--wait-timeout",
            str(self.start_timeout_seconds), service,
        )

    def _service_container(self, service: str) -> ContainerInfo:
        for row in self.status():
            if row.service == service and row.container_id:
                return self.inspect(row.container_id)
        raise LabAdapterError(f"the lab has no '{service}' container; start the lab first")

    # --- internals --------------------------------------------------------
    def _compose(self, *args: str, timeout: int | None = None, falco: bool = False) -> str:
        if not self.compose_file.is_file():
            raise LabAdapterError(f"lab compose file not found: {self.compose_file}")
        command = [self.docker_bin, "compose", "-f", str(self.compose_file), *args]
        profiles = FALCO_PROFILE if falco else ""
        result = self._run(command, what=f"docker compose {args[0]}", timeout=timeout, profiles=profiles)
        return result.stdout

    def _run(
        self,
        command: list[str],
        *,
        what: str,
        timeout: int | None = None,
        check: bool = True,
        profiles: str = "",
    ) -> subprocess.CompletedProcess:
        env = {**os.environ, "LAB_CONTROL_TOKEN": self.lab_control_token, "COMPOSE_PROFILES": profiles}
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout or self.start_timeout_seconds + 60,
            )
        except FileNotFoundError as exc:
            raise LabAdapterError(f"'{self.docker_bin}' was not found; is Docker installed and on PATH?") from exc
        except subprocess.TimeoutExpired as exc:
            raise LabAdapterError(f"{what} timed out") from exc
        if check and result.returncode != 0:
            tail = " | ".join((result.stderr or result.stdout).strip().splitlines()[-5:])
            raise LabAdapterError(f"{what} failed (exit {result.returncode}): {tail}")
        return result


def _unix(value: datetime) -> str:
    """Seconds since the epoch with a fraction, which `docker events` accepts."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return f"{value.timestamp():.6f}"


def _container_info(data: dict) -> ContainerInfo:
    config = data.get("Config") or {}
    state = data.get("State") or {}
    return ContainerInfo(
        id=str(data.get("Id") or ""),
        name=str(data.get("Name") or "").lstrip("/"),
        running=bool(state.get("Running")),
        labels={str(k): str(v) for k, v in (config.get("Labels") or {}).items()},
    )


def _parse_ps(output: str) -> list[dict]:
    """`compose ps --format json` prints one object per line (or one array in old versions)."""
    output = output.strip()
    if not output:
        return []
    try:
        parsed = json.loads(output)
    except ValueError:
        parsed = [json.loads(line) for line in output.splitlines() if line.strip()]
    return parsed if isinstance(parsed, list) else [parsed]
