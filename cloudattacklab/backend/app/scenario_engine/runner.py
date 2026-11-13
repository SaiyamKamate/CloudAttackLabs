"""Runs a scenario's script as a separate process, bounded by timeout_seconds.

Only a script named in a valid scenario file is ever run, and the target URL is
passed in from the lab configuration, so a script cannot be pointed outside the
lab. The script prints a single JSON object (its summary) on stdout.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..errors import ApiError


@dataclass(frozen=True)
class RunResult:
    outcome: str                        # "completed" or "failed"
    summary: Optional[dict[str, Any]]   # the script's parsed JSON, when it completed
    error: Optional[str]                # why it failed
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""


def resolve_script(scripts_dir: Path, script_name: str) -> Path:
    """The script path for a validated scenario, confirmed to sit inside scripts_dir.

    scenario.py already restricts the name to `^[a-z][a-z0-9_]*\\.py$` (no path
    separators), so this is defence in depth, not the only guard.
    """
    scripts_dir = Path(scripts_dir).resolve()
    path = (scripts_dir / script_name).resolve()
    if path.parent != scripts_dir:
        raise ApiError(500, "script_outside_scripts_dir", f"script '{script_name}' resolves outside {scripts_dir}")
    if not path.is_file():
        raise ApiError(500, "script_not_found", f"scenario script not found: {path}")
    return path


def execute_script(
    script_path: Path, base_url: str, parameters: dict[str, Any], timeout_seconds: int
) -> RunResult:
    command = [sys.executable, "-I", str(script_path), base_url, json.dumps(parameters)]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=str(script_path.parent),
        )
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        return RunResult(
            outcome="failed",
            summary=None,
            error=f"script timed out after {timeout_seconds}s",
            stdout=partial,
        )

    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout).strip().splitlines()[-5:]
        return RunResult(
            outcome="failed",
            summary=None,
            error=f"script exited with code {completed.returncode}: {' | '.join(tail)}",
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    summary = _parse_summary(completed.stdout)
    if summary is None:
        return RunResult(
            outcome="failed",
            summary=None,
            error="script did not print a JSON summary on stdout",
            exit_code=0,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
    return RunResult(
        outcome="completed",
        summary=summary,
        error=None,
        exit_code=0,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _parse_summary(stdout: str) -> Optional[dict[str, Any]]:
    lines = [line for line in stdout.strip().splitlines() if line.strip()]
    if not lines:
        return None
    try:
        parsed = json.loads(lines[-1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
