"""Scenario run orchestration: pre-flight, background execution, cleanup, audit.

One active run per lab. Runs happen on a background thread; the API returns the
run id straight away. Cleanup also runs automatically if a run fails or times out.

An observation run (scenario type `observation`) runs no script and sends
nothing to the lab. For its duration it collects the same telemetry an attack
run would (the target's logs, the test container's Docker events and, with
FALCO_ENABLED, Falco's alerts) through the same ingest code, then checks it
against every detection rule. It measures what traffic sent from outside the
platform (normal use) would raise.
"""
from __future__ import annotations

import dataclasses
import logging
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .. import audit
from ..config import Settings
from ..detection.checks import OBSERVATION, PARAMETER as CHECKS_PARAMETER, as_parameter, observation_checks
from ..detection.engine import DetectionEngine
from ..errors import ApiError
from ..lab_manager.adapter import LabAdapter, LabAdapterError
from ..models import Event, Lab, LabStatus, RunStatus, ScenarioRun
from ..response_manager.revert import mark_reverted
from ..schemas.scenario import ScenarioDefinition
from ..lab_manager.target import TargetClient, TargetError
from ..lab_manager.target_guard import TargetGuard
from ..lab_manager.service import refuse_while_run_active
from ..config import FALCO_SERVICE
from ..lab_manager.container import TargetRefused
from ..telemetry.container import DockerEventSource, make_docker_event_normalizer, make_falco_normalizer
from ..telemetry.factory import make_service_source, make_source
from ..telemetry.ingest import IngestResult, TelemetryIngestor, ingest_until_settled
from ..telemetry.normalize import RunContext
from ..telemetry.source import TelemetrySource, TelemetrySourceError
from . import runner
from .engine import ScenarioEngine
from .preflight import run_preflight
from .verification import run_verification

log = logging.getLogger("cloudattacklab.scenarios")

TERMINAL = (
    RunStatus.completed.value,
    RunStatus.incomplete.value,
    RunStatus.failed.value,
    RunStatus.cleaned_up.value,
)
ACTIVE_RUN = (RunStatus.pending.value, RunStatus.running.value)

# One active run per lab, across the single backend process.
_active_labs: set[uuid.UUID] = set()
_active_lock = threading.Lock()


@dataclass
class Feed:
    """One telemetry source collected during a run, and how its records are normalized."""

    source: TelemetrySource
    what: str                                   # for messages, e.g. "shopcloud log lines"
    normalizer: Optional[Callable[..., Any]] = None  # None: ShopCloud's mapping
    required: bool = True     # a settling source that returns nothing at all fails the run
    settle: bool = False      # after the run, poll until the record count stops growing
    hint: str = (             # added to the error when a required source returned nothing
        "Check that the monitoring stack's Alloy is running and shipping the lab container's logs."
    )
    container_id: Optional[str] = None  # the container this feed is about, if not the run's own


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
        # Checks, before every run, that the target URL points into the lab.
        self.guard = TargetGuard(settings.lab_targets, settings.lab_compose_file, adapter)
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
        if lab.environment == "external":
            # Fail closed until runs against registered targets (target resolution,
            # authorization check, integration telemetry) are wired in.
            raise ApiError(
                409,
                "external_runs_not_enabled",
                f"Lab '{lab.name}' belongs to a registered target; runs against registered targets are not enabled yet.",
            )

        # Refuse (409) if the lab is not running or the target is not one of the lab's targets.
        is_container = scenario.type == "container"
        is_observation = scenario.type == OBSERVATION
        if is_container:
            # Every Docker lab has the configured container targets (the test container).
            if scenario.target not in self.settings.container_targets:
                raise ApiError(409, "unknown_target", f"'{scenario.target}' is not a configured container target.")
        else:
            if scenario.target != lab.target:
                raise ApiError(
                    409,
                    "target_mismatch",
                    f"scenario targets '{scenario.target}' but lab '{lab.name}' targets '{lab.target}'.",
                )
            if lab.target not in self.target_clients:
                raise ApiError(409, "unknown_target", f"'{lab.target}' is not a configured lab target.")
            # Nothing is sent unless the configured URL is inside the lab (SHOPCLOUD_URL).
            url_check = self.guard.check(lab.target)
            if not url_check.allowed:
                raise ApiError(409, "target_url_refused", url_check.error)
        if lab.status != LabStatus.running.value:
            raise ApiError(409, "lab_not_running", f"Lab '{lab.name}' is {lab.status}; start it before a run.")

        target = None if is_container else self.target_clients[lab.target]
        base_url = None if is_container else self.settings.lab_targets[lab.target]
        # Confirm the script exists before accepting the run (an observation has none).
        script_path = (
            None
            if is_observation
            else runner.resolve_script(self.settings.scenario_scripts_dir, scenario.execution.script)
        )
        parameters = dict(scenario.execution.parameters)
        if is_observation:
            # Fix, now, which rules the run is checked against and with what threshold.
            parameters[CHECKS_PARAMETER] = as_parameter(
                observation_checks(self.detection.rules(), self.engine.list())
            )

        self._acquire(lab_id)  # one active run per lab (409 if busy)
        try:
            run_preflight(  # 409 on failure
                scenario,
                target,
                self.settings.lab_test_accounts,
                adapter=self.adapter,
                container_targets=self.settings.container_targets,
            )
            if is_container and self.settings.falco_enabled:
                self._check_falco_running()

            run = ScenarioRun(
                lab_id=lab.id,
                scenario_id=scenario.id,
                scenario_version=scenario.version,
                status=RunStatus.pending.value,
                parameters=parameters,
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
            args=(run_id, lab_id, scenario, base_url, str(script_path) if script_path else None, scenario.target),
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
        # Cleanup resets the shared target (or recreates the test container): never
        # while another run in the same lab is still using it.
        refuse_while_run_active(
            self.session, run.lab_id, f"Cleanup of run {run_id}",
            "cleanup resets the lab's target and would break that run",
        )
        scenario = self.engine.get(run.scenario_id)
        target = self.target_clients.get(scenario.target)
        if target is None and scenario.target not in self.settings.container_targets:
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
        base_url: Optional[str],
        script_path: Optional[str],
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

            # Collect the run's telemetry while it is active (and once more at the end).
            feeds = self._telemetry_feeds(scenario)
            for feed in feeds:
                try:
                    feed.source.check()
                except TelemetrySourceError as exc:
                    # A telemetry source cannot be read: do not run the scenario at
                    # all, and never fall back to another source.
                    self._fail_before_script(
                        run_id, scenario, f"Telemetry source '{feed.source.name}' is not available: {exc}"
                    )
                    return
            is_container = scenario.type == "container"
            service = self.settings.container_targets.get(target_name) if is_container else self.settings.telemetry_service
            ctx = RunContext(
                run_id=run_id,
                lab_id=lab_id,
                scenario_id=scenario.id,
                started_at=started_at,
                container_id=self._container_id(service),
            )
            stop = threading.Event()
            telemetry_errors: list[tuple[str, str]] = []
            collector = threading.Thread(
                target=self._collect_loop,
                args=(feeds, ctx, stop, telemetry_errors),
                name=f"collect-{run_id}",
                daemon=True,
            )
            collector.start()

            if scenario.type == OBSERVATION:
                # No script: only watch the lab for the scenario's duration.
                duration = int(scenario.execution.parameters["duration_seconds"])
                time.sleep(duration)  # collection runs meanwhile in its own thread
                result = runner.RunResult(
                    outcome="completed",
                    summary={"scenario": scenario.id, "observed_seconds": duration, "script": None},
                    error=None,
                )
            elif is_container:
                # In-process, with a handle that can only run the fixed marker command.
                handle = runner.ContainerHandle(self.adapter, target_name, service)
                result = runner.execute_container_script(Path(script_path), handle, scenario.execution.timeout_seconds)
            else:
                result = runner.execute_script(
                    Path(script_path), base_url, dict(run.parameters), scenario.execution.timeout_seconds
                )

            stop.set()
            # The loop does a final ingest after stop is set (and, for a late
            # source such as Loki, polls until the count stops growing).
            collector.join(timeout=30 + self.settings.loki_settle_timeout_seconds)
            if telemetry_errors:
                # The run's evidence is incomplete: record it as failed and do
                # not raise alerts from part of the telemetry.
                failed_source, message = telemetry_errors[0]
                result = runner.RunResult(
                    outcome="failed",
                    summary=result.summary,
                    error=f"Telemetry source '{failed_source}' failed during the run: {message}",
                )

            # Detection runs over the events collected for this run.
            with Session(self._bind, expire_on_commit=False) as session:
                run = session.get(ScenarioRun, run_id)
                try:
                    if not telemetry_errors:
                        alerts = self.detection.run_for_run(session, run, scenario)
                        if alerts:
                            log.info("run %s raised %d alert(s)", run_id, len(alerts))
                except Exception:
                    log.exception("detection failed for run %s", run_id)

                # Repeat the scenario's check once more, if it names one, and
                # keep the result with the run. Done after collection stops, so
                # this request is not counted as part of the run's evidence.
                summary = result.summary
                verification = run_verification(
                    scenario, self.target_clients.get(target_name), self.settings.lab_test_accounts
                )
                if verification is not None:
                    summary = {**(summary or {}), "verification": verification}

                run.finished_at = _utcnow()
                if result.outcome == "completed":
                    # The script finished, but the run is only `completed` when its
                    # evidence is whole: otherwise `incomplete`, with the reasons.
                    shortfalls = run_shortfalls(session, run, scenario, result.summary)
                    outcome = RunStatus.incomplete.value if shortfalls else RunStatus.completed.value
                    run.status = outcome
                    run.summary = summary
                    run.error = ("Run incomplete: " + "; ".join(shortfalls) + ".") if shortfalls else None
                    detail = {"scenario_id": scenario.id, "outcome": outcome, "summary": summary}
                    if shortfalls:
                        detail["error"] = run.error
                    audit.record(
                        session,
                        actor=self.actor,
                        action="run.finish",
                        object_type="scenario_run",
                        object_id=str(run.id),
                        detail=detail,
                    )
                    session.commit()
                else:
                    run.status = RunStatus.failed.value
                    run.error = result.error
                    run.summary = summary
                    audit.record(
                        session,
                        actor=self.actor,
                        action="run.failure",
                        object_type="scenario_run",
                        object_id=str(run.id),
                        detail={
                            "scenario_id": scenario.id,
                            "outcome": "failed",
                            "error": result.error,
                            "summary": summary,
                        },
                    )
                    session.commit()
                    # Cleanup also runs if a run fails or times out.
                    target = self.target_clients.get(target_name)
                    if target is not None or target_name in self.settings.container_targets:
                        self._run_cleanup(session, scenario, target, run, trigger="auto")
                        session.commit()
        except Exception:  # never let a background thread die silently
            log.exception("run %s crashed", run_id)
            self._record_crash(run_id, scenario)
        finally:
            self._release(lab_id)

    # --- telemetry collection --------------------------------------------
    def _telemetry_feeds(self, scenario: ScenarioDefinition) -> list[Feed]:
        """What to collect for this scenario.

        Application scenarios: the target's logs from TELEMETRY_SOURCE.
        Container scenarios: the test container's Docker events (base telemetry)
        and, with FALCO_ENABLED, Falco's alerts read from TELEMETRY_SOURCE (extra
        evidence: Falco having nothing to report is not an error).
        """
        if scenario.type == OBSERVATION:
            # Everything an attack run of any type would collect.
            source = make_source(self.settings, self.adapter)
            feeds = [
                Feed(
                    source,
                    what=f"{self.settings.telemetry_service} log lines",
                    settle=bool(getattr(source, "late_arriving", False)),
                )
            ]
            for target, service in sorted(self.settings.container_targets.items()):
                container_id = self._container_id(service)
                feeds.append(
                    Feed(
                        DockerEventSource(self.adapter),
                        what="docker events",
                        normalizer=make_docker_event_normalizer(target),
                        required=False,  # normal use may start no process at all
                        settle=True,
                        hint="Check that the backend can reach the Docker daemon.",
                        container_id=container_id,
                    )
                )
                if self.settings.falco_enabled:
                    feeds.append(
                        Feed(
                            make_service_source(self.settings, self.adapter, FALCO_SERVICE),
                            what="falco alerts",
                            normalizer=make_falco_normalizer(target),
                            required=False,
                            settle=True,
                            container_id=container_id,
                        )
                    )
            return feeds
        if scenario.type != "container":
            source = make_source(self.settings, self.adapter)
            return [
                Feed(
                    source,
                    what=f"{self.settings.telemetry_service} log lines",
                    settle=bool(getattr(source, "late_arriving", False)),
                )
            ]
        feeds = [
            Feed(
                DockerEventSource(self.adapter),
                what="docker events",
                normalizer=make_docker_event_normalizer(scenario.target),
                # An event can reach the daemon's log a moment after `docker exec`
                # returns, so poll until the count stops growing.
                settle=True,
                hint="Check that the backend can reach the Docker daemon.",
            )
        ]
        if self.settings.falco_enabled:
            feeds.append(
                Feed(
                    make_service_source(self.settings, self.adapter, FALCO_SERVICE),
                    what="falco alerts",
                    normalizer=make_falco_normalizer(scenario.target),
                    required=False,
                    settle=True,  # Falco reports a moment after the process started
                )
            )
        return feeds

    def _collect_loop(
        self, feeds: list[Feed], ctx: RunContext, stop: threading.Event, errors: list[tuple[str, str]]
    ) -> None:
        """Ingest telemetry periodically while the run is active, then once more at the end.

        All ingestion is in this one thread, so stored events are never raced. If
        a source cannot be read, (source name, error) is put in `errors` and
        collection stops.
        """
        feed = feeds[0]
        try:
            while not stop.is_set():
                for feed in feeds:
                    self._ingest_once(feed, ctx)
                stop.wait(self.settings.telemetry_collect_interval_seconds)
            # The script has finished: from now on only events up to this moment
            # (plus the grace) belong to the run, however long the settling takes.
            ctx = dataclasses.replace(ctx, until=_utcnow())
            for feed in feeds:
                if not feed.settle:
                    self._ingest_once(feed, ctx)  # final pass after the run stops
                    continue
                # Records reach Loki (or Falco's output) a little late: poll until
                # the count stops growing.
                settled = ingest_until_settled(
                    lambda: self._ingest_once(feed, ctx),
                    interval_seconds=self.settings.loki_settle_interval_seconds,
                    timeout_seconds=self.settings.loki_settle_timeout_seconds,
                    empty_is_settled=not feed.required,
                )
                if feed.required and settled.collected == 0:
                    raise TelemetrySourceError(
                        f"{feed.source.name} returned no {feed.what} for this run "
                        f"after waiting {self.settings.loki_settle_timeout_seconds:g} s. {feed.hint}"
                    )
        except TelemetrySourceError as exc:
            log.warning("telemetry source %s failed for run %s: %s", feed.source.name, ctx.run_id, exc)
            errors.append((feed.source.name, str(exc)))
        except Exception as exc:
            log.exception("telemetry collection failed for run %s", ctx.run_id)
            errors.append((feed.source.name, f"{type(exc).__name__}: {exc}"))

    def _ingest_once(self, feed: Feed, ctx: RunContext) -> IngestResult:
        if feed.container_id:
            ctx = dataclasses.replace(ctx, container_id=feed.container_id)
        with Session(self._bind, expire_on_commit=False) as session:
            return TelemetryIngestor(
                session,
                feed.source,
                ctx,
                grace_seconds=self.settings.telemetry_since_grace_seconds,
                normalizer=feed.normalizer,
            ).ingest()

    def _check_falco_running(self) -> None:
        """With FALCO_ENABLED, a container run needs Falco up: refuse (409) otherwise."""
        try:
            containers = self.adapter.status()
        except LabAdapterError as exc:
            raise ApiError(409, "preflight_failed", f"pre-flight check 'falco' failed: {exc}") from exc
        falco = next((c for c in containers if c.service == FALCO_SERVICE), None)
        if falco is None or falco.state != "running":
            state = "missing" if falco is None else falco.state
            raise ApiError(
                409,
                "preflight_failed",
                f"pre-flight check 'falco' failed: FALCO_ENABLED is true but the Falco container is {state}. "
                "Start (or reset) the lab, or set FALCO_ENABLED=false.",
            )

    def _fail_before_script(self, run_id: uuid.UUID, scenario: ScenarioDefinition, error: str) -> None:
        """Mark a run failed before its script ran (nothing was sent to the target)."""
        with Session(self._bind, expire_on_commit=False) as session:
            run = session.get(ScenarioRun, run_id)
            run.status = RunStatus.failed.value
            run.error = error
            run.finished_at = _utcnow()
            audit.record(
                session,
                actor=self.actor,
                action="run.failure",
                object_type="scenario_run",
                object_id=str(run_id),
                detail={"scenario_id": scenario.id, "outcome": "failed", "error": error, "script_ran": False},
            )
            session.commit()

    def _container_id(self, service: str) -> Optional[str]:
        try:
            for container in self.adapter.status():
                if container.service == service:
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
        self,
        session: Session,
        scenario: ScenarioDefinition,
        target: Optional[TargetClient],
        run: ScenarioRun,
        *,
        trigger: str,
    ) -> None:
        done: list[str] = []
        errors: dict[str, str] = {}
        for step in scenario.cleanup.steps:
            try:
                self._cleanup_step(step, target, scenario)
                done.append(step)
            except (TargetError, LabAdapterError, TargetRefused) as exc:
                errors[step] = str(exc)
                log.warning("cleanup step '%s' failed for run %s: %s", step, run.id, exc)
        # Either step turns the target's rate limit off, so an applied rate limit is now reverted;
        # a recreated test container undoes stop_container.
        reverted = []
        if "disable_rate_limit" in done or "reset_target_state" in done:
            reverted += mark_reverted(
                session, lab_id=run.lab_id, trigger=f"cleanup:{trigger}", actor=self.actor,
                actions=("temporary_rate_limit",),
            )
        if "recreate_test_container" in done:
            reverted += mark_reverted(
                session, lab_id=run.lab_id, trigger=f"cleanup:{trigger}", actor=self.actor,
                actions=("stop_container",),
            )
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
                "responses_reverted": [str(row.id) for row in reverted],
                "outcome": "success" if not errors else "partial",
            },
        )

    def _cleanup_step(self, step: str, target: Optional[TargetClient], scenario: ScenarioDefinition) -> None:
        if step in ("disable_rate_limit", "reset_target_state") and target is None:
            raise TargetError(f"'{scenario.target}' has no HTTP control endpoints for '{step}'")
        if step == "disable_rate_limit":
            target.set_rate_limit(enabled=False)
        elif step == "reset_target_state":
            target.reset()
        elif step == "recreate_test_container":
            service = self.settings.container_targets.get(scenario.target)
            if service is None:
                raise TargetError(f"'{scenario.target}' is not a container target")
            # A fresh test container: back to the baseline (undoes a stop_container).
            self.adapter.recreate_service(service)
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


def run_shortfalls(
    session: Session, run: ScenarioRun, scenario: ScenarioDefinition, summary: Optional[dict[str, Any]]
) -> list[str]:
    """Why a run whose script finished still cannot count as `completed`. Empty if it can.

    - an expected_telemetry item of the scenario was not met (fewer events of that
      source and type were collected for the run than its min_count);
    - the script reported request errors (requests that got no answer at all).
    """
    reasons: list[str] = []
    counts = Counter(
        (source, event_type)
        for source, event_type in session.execute(
            select(Event.source, Event.event_type).where(Event.run_id == run.id)
        )
    )
    for item in scenario.expected_telemetry:
        got = counts.get((item.source, item.event_type), 0)
        if got < item.min_count:
            reasons.append(
                f"expected at least {item.min_count} {item.source} {item.event_type} event(s), collected {got}"
            )
    errors = (summary or {}).get("errors")
    if isinstance(errors, int) and not isinstance(errors, bool) and errors > 0:
        reasons.append(f"the script reported {errors} request error(s) (requests the target never answered)")
    return reasons


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
