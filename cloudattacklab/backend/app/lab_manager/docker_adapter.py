"""Docker adapter. This is the only module in the backend that talks to Docker.

It only ever runs `docker compose -f <lab compose file> ...`, so it can touch
the lab project and nothing else on the machine.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .adapter import ContainerStatus, LabAdapter, LabAdapterError


class DockerComposeAdapter(LabAdapter):
    def __init__(
        self,
        compose_file: Path,
        *,
        docker_bin: str = "docker",
        lab_control_token: str,
        start_timeout_seconds: int = 180,
    ) -> None:
        self.compose_file = Path(compose_file)
        self.docker_bin = docker_bin
        self.lab_control_token = lab_control_token
        self.start_timeout_seconds = start_timeout_seconds

    # --- LabAdapter -------------------------------------------------------
    def start(self) -> None:
        # --wait returns once the health checks in the compose file pass.
        self._compose("up", "--detach", "--build", "--wait", "--wait-timeout", str(self.start_timeout_seconds))

    def stop(self) -> None:
        # `stop`, not `down`: the stopped container keeps its logs for later inspection.
        self._compose("stop")

    def reset(self) -> None:
        self._compose(
            "up", "--detach", "--force-recreate", "--wait", "--wait-timeout", str(self.start_timeout_seconds)
        )

    def status(self) -> list[ContainerStatus]:
        output = self._compose("ps", "--all", "--format", "json", timeout=30)
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
        return self._compose(*args, timeout=60)

    # --- internals --------------------------------------------------------
    def _compose(self, *args: str, timeout: int | None = None) -> str:
        if not self.compose_file.is_file():
            raise LabAdapterError(f"lab compose file not found: {self.compose_file}")
        command = [self.docker_bin, "compose", "-f", str(self.compose_file), *args]
        env = {**os.environ, "LAB_CONTROL_TOKEN": self.lab_control_token}
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
            raise LabAdapterError(f"docker compose {args[0]} timed out") from exc
        if result.returncode != 0:
            tail = " | ".join((result.stderr or result.stdout).strip().splitlines()[-5:])
            raise LabAdapterError(f"docker compose {args[0]} failed (exit {result.returncode}): {tail}")
        return result.stdout


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
