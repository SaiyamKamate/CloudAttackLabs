"""Scenario run orchestration: pre-flight, background execution, cleanup, audit.

One active run per lab. Runs happen on a background thread; the API returns the
run id straight away. Cleanup also runs automatically if a run fails or times out.
"""
from __future__ import annotations

import logging
import threading
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .. import audit
from ..config import Settings
from ..detection.engine import DetectionEngine
from ..errors import ApiError
from ..lab_manager.adapter import LabAdapter, LabAdapterError
from ..models import Lab, LabStatus, RunStatus, ScenarioRun
from ..schemas.scenario import ScenarioDefinition
from ..lab_manager.target import TargetClient, TargetError
from ..telemetry.ingest import TelemetryIngestor
from ..telemetry.normalize import RunContext
from ..telemetry.source import DockerLogSource
from . import runner
from .engine import ScenarioEngine
from .preflight import run_preflight

log = logging.getLogger("cloudattacklab.scenarios")

TERMINAL = (RunStatus.completed.value, RunStatus.failed.value, RunStatus.cleaned_up.value)
ACTIVE_RUN = (RunStatus.pending.value, RunStatus.running.value)

# One active run per lab, across the single backend process.
_active_labs: set[uuid.UUID] = set()
_active_lock = threading.Lock()


class ScenarioService:
    def __init__(
        self,
        session: Session,
        engine: ScenarioEngine,
        target_clients: dict[str, TargetClient],
        settings: Settings,
        adapter: LabAdapter,
        detection: DetectionEngine,
        *,
        actor: str = audit.ANONYMOUS_ACTOR,
    ) -> None:
        self.session = session
        self.engine = engine
        self.target_clients = target_clients
        self.settings = settings
        self.adapter = adapter
        self.detection = detection
        self.actor = actor
        # Background threads open their own session on the same database.
        self._bind: Engine = session.get_bind()

    # --- reads ------------------------------------------------------------
    def list_scenarios(self) -> list[ScenarioDefinition]:
        return self.engine.list()

    def get_scenario(self, scenario_id: str) -> ScenarioDefinition:
        return self.engine.get(scenario_id)

    def get_run(self, run_id: uuid.UUID) -> ScenarioRun:
        run = self.session.get(ScenarioRun, run_id)
        if run is None:
            raise ApiError(404, "run_not_found", f"No run with id {run_id}.")
        return run

    # --- start a run ------------------------------------------------------
    def start_run(self, lab_id: uuid.UUID, scenario_id: str) -> ScenarioRun:
        lab = self.session.get(Lab, lab_id)
        if lab is None:
            raise ApiError(404, "lab_not_found", f"No lab with id {lab_id}.")
        scenario = self.engine.get(scenario_id)  # 404 if not a valid scenario

        # Refuse (409) if the lab is not running or the target is not the lab's target.
        if scenario.target != lab.target:
            raise ApiError(
                409,
                "target_mismatch",
                f"scenario targets '{scenario.target}' but lab '{lab.name}' targets '{lab.target}'.",
            )
        if lab.target not in self.target_clients:
            raise ApiError(409, "unknown_target", f"'{lab.target}' is not a configured lab target.")
        if lab.status != LabStatus.running.value:
            raise ApiError(409, "lab_not_running", f"Lab '{lab.name}' is {lab.status}; start it before a run.")

        target = self.target_clients[lab.target]
        base_url = self.settings.lab_targets[lab.target]
        # Confirm the script exists before accepting the run.
        script_path = runner.resolve_script(self.settings.scenario_scripts_dir, scenario.execution.script)

        self._acquire(lab_id)  # one active run per lab (409 if busy)
        try:
            run_preflight(scenario, target, self.settings.lab_test_accounts)  # 409 on failure

            run = ScenarioRun(
                lab_id=lab.id,
                scenario_id=scenario.id,
                scenario_version=scenario.version,
                status=RunStatus.pending.value,
                parameters=dict(scenario.execution.parameters),
            )
            self.session.add(run)
            self.session.flush()
            audit.record(
                self.session,
                actor=self.actor,
                action="run.start",
                object_type="scenario_run",
                object_id=str(run.id),
                detail={"scenario_id": scenario.id, "lab_id": str(lab.id), "parameters": dict(run.parameters)},
            )
            self.session.commit()
            run_id = run.id
        except BaseException:
            self._release(lab_id)
            raise

        thread = threading.Thread(
            target=self._run_in_background,
            args=(run_id, lab_id, scenario, base_url, str(script_path), scenario.target),
            name=f"run-{run_id}",
            daemon=True,
        )
        thread.start()
        return run

    # --- cleanup endpoint -------------------------------------------------
    def cleanup_run(self, run_id: uuid.UUID) -> ScenarioRun:
        run = self.get_run(run_id)
        if run.status in ACTIVE_RUN:
            raise ApiError(409, "run_in_progress", f"Run {run_id} is {run.status}; wait for it to finish.")
        scenario = self.engine.get(run.scenario_id)
        target = self.target_clients.get(scenario.target)
        if target is None:
            raise ApiError(409, "unknown_target", f"'{scenario.target}' is not a configured lab target.")
        self._run_cleanup(self.session, scenario, target, run, trigger="manual")
        run.status = RunStatus.cleaned_up.value
        self.session.commit()
        return run

    # --- background -------------------------------------------------------
    def _run_in_background(
        self,
        run_id: uuid.UUID,
        lab_id: uuid.UUID,
        scenario: ScenarioDefinition,
        base_url: str,
        script_path: str,
        target_name: str,
    ) -> None:
        from pathlib import Path

        try:
            with Session(self._bind, expire_on_commit=False) as session:
                run = session.get(ScenarioRun, run_id)
                run.status = RunStatus.running.value
                run.started_at = _utcnow()
                session.commit()
                started_at = run.started_at

            # Collect ShopCloud's telemetry while the run is active (and once more at the end).
            ctx = RunContext(
                run_id=run_id,
                lab_id=lab_id,
                scenario_id=scenario.id,
                started_at=started_at,
                container_id=self._container_id(),
            )
            stop = threading.Event()
            collector = threading.Thread(
                target=self._collect_loop, args=(ctx, stop), name=f"collect-{run_id}", daemon=True
            )
            collector.start()

            result = runner.execute_script(
                Path(script_path), base_url, dict(run.parameters), scenario.execution.timeout_seconds
            )

            stop.set()
            collector.join(timeout=30)  # the loop does a final ingest after stop is set

            # Detection runs over the events collected for this run.
            with Session(self._bind, expire_on_commit=False) as session:
                run = session.get(ScenarioRun, run_id)
                try:
                    alerts = self.detection.run_for_run(session, run, scenario)
                    if alerts:
                        log.info("run %s raised %d alert(s)", run_id, len(alerts))
                except Exception:
                    log.exception("detection failed for run %s", run_id)

                run.finished_at = _utcnow()
                if result.outcome == "completed":
                    run.status = RunStatus.completed.value
                    run.summary = result.summary
                    audit.record(
                        session,
                        actor=self.actor,
                        action="run.finish",
                        object_type="scenario_run",
                        object_id=str(run.id),
                        detail={"scenario_id": scenario.id, "outcome": "completed", "summary": result.summary},
                    )
                    session.commit()
                else:
                    run.status = RunStatus.failed.value
                    run.error = result.error
                    run.summary = result.summary
                    audit.record(
                        session,
                        actor=self.actor,
                        action="run.failure",
                        object_type="scenario_run",
                        object_id=str(run.id),
                        detail={"scenario_id": scenario.id, "outcome": "failed", "error": result.error},
                    )
                    session.commit()
                    # Cleanup also runs if a run fails or times out.
                    target = self.target_clients.get(target_name)
                    if target is not None:
                        self._run_cleanup(session, scenario, target, run, trigger="auto")
                        session.commit()
        except Exception:  # never let a background thread die silently
            log.exception("run %s crashed", run_id)
            self._record_crash(run_id, scenario)
        finally:
            self._release(lab_id)

    # --- telemetry collection --------------------------------------------
    def _collect_loop(self, ctx: RunContext, stop: threading.Event) -> None:
        """Ingest telemetry periodically while the run is active, then once more at the end.

        All ingestion is in this one thread, so stored events are never raced.
        """
        try:
            while not stop.is_set():
                self._ingest_once(ctx)
                stop.wait(self.settings.telemetry_collect_interval_seconds)
            self._ingest_once(ctx)  # final pass after the run stops
        except Exception:
            log.exception("telemetry collection failed for run %s", ctx.run_id)

    def _ingest_once(self, ctx: RunContext) -> None:
        source = DockerLogSource(self.adapter, self.settings.telemetry_service)
        with Session(self._bind, expire_on_commit=False) as session:
            TelemetryIngestor(
                session, source, ctx, grace_seconds=self.settings.telemetry_since_grace_seconds
            ).ingest()

    def _container_id(self) -> Optional[str]:
        try:
            for container in self.adapter.status():
                if container.service == self.settings.telemetry_service:
                    return container.container_id or None
        except LabAdapterError:
            return None
        return None

    def _record_crash(self, run_id: uuid.UUID, scenario: ScenarioDefinition) -> None:
        try:
            with Session(self._bind, expire_on_commit=False) as session:
                run = session.get(ScenarioRun, run_id)
                if run is not None and run.status not in TERMINAL:
                    run.status = RunStatus.failed.value
                    run.error = "the run crashed; see backend logs"
                    run.finished_at = _utcnow()
                    audit.record(
                        session,
                        actor=self.actor,
                        action="run.failure",
                        object_type="scenario_run",
                        object_id=str(run_id),
                        detail={"scenario_id": scenario.id, "outcome": "crashed"},
                    )
                    session.commit()
        except Exception:
            log.exception("could not record crash for run %s", run_id)

    # --- cleanup steps ----------------------------------------------------
    def _run_cleanup(
        self, session: Session, scenario: ScenarioDefinition, target: TargetClient, run: ScenarioRun, *, trigger: str
    ) -> None:
        done: list[str] = []
        errors: dict[str, str] = {}
        for step in scenario.cleanup.steps:
            try:
                self._cleanup_step(step, target)
                done.append(step)
            except TargetError as exc:
                errors[step] = str(exc)
                log.warning("cleanup step '%s' failed for run %s: %s", step, run.id, exc)
        audit.record(
            session,
            actor=self.actor,
            action="cleanup",
            object_type="scenario_run",
            object_id=str(run.id),
            detail={
                "scenario_id": scenario.id,
                "trigger": trigger,
                "steps_done": done,
                "steps_failed": errors,
                "outcome": "success" if not errors else "partial",
            },
        )

    def _cleanup_step(self, step: str, target: TargetClient) -> None:
        if step == "disable_rate_limit":
            target.set_rate_limit(enabled=False)
        elif step == "reset_target_state":
            target.reset()
        else:
            log.warning("unknown cleanup step '%s'; skipping", step)

    # --- one active run per lab ------------------------------------------
    def _acquire(self, lab_id: uuid.UUID) -> None:
        with _active_lock:
            if lab_id in _active_labs:
                raise ApiError(409, "run_in_progress", f"Lab {lab_id} already has a run in progress.")
            # Also guard against a leftover active run row (e.g. after a restart).
            existing = self.session.scalars(
                select(ScenarioRun).where(
                    ScenarioRun.lab_id == lab_id, ScenarioRun.status.in_(ACTIVE_RUN)
                )
            ).first()
            if existing is not None:
                raise ApiError(409, "run_in_progress", f"Lab {lab_id} already has a run in progress.")
            _active_labs.add(lab_id)

    def _release(self, lab_id: uuid.UUID) -> None:
        with _active_lock:
            _active_labs.discard(lab_id)


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
