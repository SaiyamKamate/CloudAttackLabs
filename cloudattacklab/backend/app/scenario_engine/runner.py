"""Runs a scenario's script as a separate process, bounded by timeout_seconds.

Only a script named in a valid scenario file is ever run, and the target URL is
passed in from the lab configuration, so a script cannot be pointed outside the
lab. The script prints a single JSON object (its summary) on stdout. A non-zero
exit code means the run failed; the summary is kept if the script printed one.
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
    summary: Optional[dict[str, Any]]   # the script's parsed JSON, if it printed one
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
            # A script that fails on purpose (e.g. the target granted access it
            # should have refused) still reports what it saw.
            summary=_parse_summary(completed.stdout),
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


# --- container scenarios -----------------------------------------------------
class TestContainerHandle:
    """What a container scenario's script is given: one fixed action, nothing else.

    `run_marker()` runs MARKER_COMMAND (lab_manager/container.py) in the target's
    test container through the lab adapter. There is no way to pass a command,
    choose another container, or reach the adapter itself.
    """

    __slots__ = ("_run", "target", "name")

    def __init__(self, adapter, target: str, service: str) -> None:
        self.target = target
        self.name = service
        self._run = lambda: adapter.exec_marker(service)

    def run_marker(self):
        return self._run()


def execute_container_script(script_path: Path, handle: TestContainerHandle, timeout_seconds: int) -> RunResult:
    """Load a container scenario's script and call its `run(handle)`, bounded by the timeout.

    The script returns its summary as a dict. A non-zero `exit_code` in it, an
    exception, or running past the timeout makes the run fail.
    """
    import importlib.util
    import threading

    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            spec = importlib.util.spec_from_file_location(f"scenario_script_{script_path.stem}", script_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            run = getattr(module, "run", None)
            if not callable(run):
                raise RuntimeError(f"{script_path.name} has no run(container) function")
            outcome["summary"] = run(handle)
        except Exception as exc:  # reported on the run, never raised into the engine
            outcome["error"] = f"{type(exc).__name__}: {exc}"

    worker = threading.Thread(target=target, name=f"script-{script_path.stem}", daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        return RunResult(outcome="failed", summary=None, error=f"script timed out after {timeout_seconds}s")
    if "error" in outcome:
        return RunResult(outcome="failed", summary=None, error=f"script failed: {outcome['error']}")
    summary = outcome.get("summary")
    if not isinstance(summary, dict):
        return RunResult(outcome="failed", summary=None, error="script did not return a summary dict")
    exit_code = summary.get("exit_code")
    if exit_code != 0:
        return RunResult(
            outcome="failed", summary=summary, error=f"the command in the test container exited with {exit_code}"
        )
    return RunResult(outcome="completed", summary=summary, error=None, exit_code=0)
