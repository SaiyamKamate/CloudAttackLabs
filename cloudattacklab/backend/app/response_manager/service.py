"""Response Manager: request, approve or reject, execute, verify.

Safety rules, all enforced here:
- The action and its parameters come from the scenario's `response` section.
  Nothing from the request body is used.
- Nothing touches the target until a response is approved.
- The target is the lab's configured target client, never an address from a request.
- stop_container stops only the container named in the incident's own evidence
  (the container_id on its events), and only if that container carries the
  test-target label and belongs to the lab's Compose project. This is checked
  when the response is requested and again by the adapter right before
  `docker stop`; ShopCloud, Falco and platform containers are refused.

Implemented: `temporary_rate_limit` and `stop_container`. Reverting happens in
cleanup and lab reset (see revert.py).
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..errors import ApiError
from ..incidents.risk import refresh_risk
from ..lab_manager.adapter import LabAdapter, LabAdapterError
from ..lab_manager.container import TargetRefused, check_test_container
from ..lab_manager.target import TargetClient, TargetError
from ..models import Incident, Lab, LabStatus, ResponseAction, ResponseStatus, ScenarioRun
from ..scenario_engine.engine import ScenarioEngine
from ..schemas.scenario import MAX_ATTEMPTS, ScenarioDefinition
from .verification import CHECKS, EXTRA_ATTEMPTS, container_not_running

log = logging.getLogger("cloudattacklab.responses")

SUPPORTED_ACTIONS = ("temporary_rate_limit", "stop_container")
# A response that is still waiting or is applied; an incident may have only one of these.
OPEN = (
    ResponseStatus.pending_approval.value,
    ResponseStatus.approved.value,
    ResponseStatus.executed.value,
    ResponseStatus.verified.value,
)

# One decision at a time (single backend process), so a response cannot be approved twice.
_decision_lock = threading.Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResponseManager:
    def __init__(
        self,
        session: Session,
        engine: ScenarioEngine,
        target_clients: dict[str, TargetClient],
        *,
        adapter: Optional[LabAdapter] = None,
        container_targets: Optional[dict[str, str]] = None,
        actor: str = audit.ANONYMOUS_ACTOR,
    ) -> None:
        self.session = session
        self.engine = engine
        self.target_clients = target_clients  # the configured lab targets
        self.adapter = adapter  # for stop_container
        self.container_targets = container_targets or {}
        self.actor = actor

    # --- read -------------------------------------------------------------
    def get(self, response_id: uuid.UUID) -> ResponseAction:
        row = self.session.get(ResponseAction, response_id)
        if row is None:
            raise ApiError(404, "response_not_found", f"No response with id {response_id}.")
        return row

    # --- request ----------------------------------------------------------
    def request(self, incident_id: uuid.UUID) -> ResponseAction:
        incident = self.session.get(Incident, incident_id)
        if incident is None:
            raise ApiError(404, "incident_not_found", f"No incident with id {incident_id}.")
        scenario = self._scenario_for(incident)
        action, parameters = self._planned_action(scenario, incident)
        if action == "stop_container":
            self._check_stoppable(parameters["container_id"])  # 409 for anything but the test container

        with _decision_lock:
            existing = self.session.scalars(
                select(ResponseAction).where(
                    ResponseAction.incident_id == incident.id, ResponseAction.status.in_(OPEN)
                )
            ).first()
            if existing is not None:
                raise ApiError(
                    409,
                    "response_already_open",
                    f"Incident {incident.id} already has a response that is {existing.status}.",
                )
            row = ResponseAction(incident_id=incident.id, action=action, parameters=parameters)
            self.session.add(row)
            self.session.flush()
            self._audit(row, "response.requested", {"scenario_id": scenario.id, "parameters": parameters})
            self.session.commit()
        return row

    # --- decide -----------------------------------------------------------
    def reject(self, response_id: uuid.UUID) -> ResponseAction:
        with _decision_lock:
            row = self._pending(response_id)
            row.status = ResponseStatus.rejected.value
            row.decided_at = _utcnow()
            row.decided_by = self.actor
            self._audit(row, "response.rejected", {"decided_by": self.actor})
            self.session.commit()
        return row

    def approve(self, response_id: uuid.UUID) -> ResponseAction:
        """Approve, execute, then verify. Each stage is committed with its audit row."""
        with _decision_lock:
            row = self._pending(response_id)
            incident = self.session.get(Incident, row.incident_id)
            scenario = self._scenario_for(incident)
            # Re-read the action from the scenario: the stored row must still match it.
            action, parameters = self._planned_action(scenario, incident)
            if (action, parameters) != (row.action, dict(row.parameters)):
                raise ApiError(
                    409,
                    "response_out_of_date",
                    "The scenario's response section changed after this response was requested; request it again.",
                )
            if action == "stop_container":
                self._lab_for(incident)  # the lab must exist and be running
                return self._approve_stop_container(row, incident, scenario, parameters)
            target = self._target_for(incident, scenario)

            row.status = ResponseStatus.approved.value
            row.decided_at = _utcnow()
            row.decided_by = self.actor
            self._audit(row, "response.approved", {"decided_by": self.actor})
            self.session.commit()

            try:
                saved = target.set_rate_limit(
                    enabled=True,
                    max_attempts=parameters["max_attempts"],
                    window_seconds=parameters["window_seconds"],
                )
            except Exception as exc:  # TargetError, or anything unexpected: never leave it "approved"
                self._fail(row, {"stage": "execute", "passed": False, "error": str(exc)})
                return row
            row.status = ResponseStatus.executed.value
            row.executed_at = _utcnow()
            self._audit(
                row,
                "response.executed",
                {"request": "POST /lab/rate-limit", "sent": {"enabled": True, **parameters}, "target_saved": saved},
            )
            self.session.commit()

            check = scenario.response.verification.check
            try:
                run_check = CHECKS.get(check)
                if run_check is None:
                    raise TargetError(f"verification check '{check}' is not implemented")
                result = run_check(
                    target, username=self._username(scenario), max_attempts=parameters["max_attempts"]
                )
            except Exception as exc:
                self._fail(row, {"stage": "verify", "check": check, "passed": False, "error": str(exc)})
                return row

            row.verified_at = _utcnow()
            if result["passed"]:
                row.status = ResponseStatus.verified.value
                row.verification_result = result
                self._audit(row, "response.verified", result)
                # A verified response lowers the incident's risk score.
                refresh_risk(self.session, incident, scenario.detection.threshold)
                self.session.commit()
            else:
                self._fail(row, {"stage": "verify", **result})
        return row

    # --- stop_container ---------------------------------------------------
    def _approve_stop_container(
        self, row: ResponseAction, incident: Incident, scenario: ScenarioDefinition, parameters: dict[str, Any]
    ) -> ResponseAction:
        """Approved, then `docker stop` (the adapter refuses any non-test container), then verify."""
        container_id = parameters["container_id"]
        row.status = ResponseStatus.approved.value
        row.decided_at = _utcnow()
        row.decided_by = self.actor
        self._audit(row, "response.approved", {"decided_by": self.actor})
        self.session.commit()

        try:
            stopped = self.adapter.stop_test_container(container_id)
        except TargetRefused as exc:
            self._fail(row, {"stage": "execute", "passed": False, "refused": True, "error": str(exc)})
            return row
        except Exception as exc:  # LabAdapterError, or anything unexpected: never leave it "approved"
            self._fail(row, {"stage": "execute", "passed": False, "error": str(exc)})
            return row
        row.status = ResponseStatus.executed.value
        row.executed_at = _utcnow()
        self._audit(
            row,
            "response.executed",
            {"request": "docker stop", "container": stopped.name, "container_id": stopped.short_id},
        )
        self.session.commit()

        check = scenario.response.verification.check
        try:
            if check != "container_not_running":
                raise TargetError(f"verification check '{check}' is not implemented for stop_container")
            result = container_not_running(self.adapter, container_id)
        except Exception as exc:
            self._fail(row, {"stage": "verify", "check": check, "passed": False, "error": str(exc)})
            return row

        row.verified_at = _utcnow()
        if result["passed"]:
            row.status = ResponseStatus.verified.value
            row.verification_result = result
            self._audit(row, "response.verified", result)
            refresh_risk(self.session, incident, scenario.detection.threshold)
            self.session.commit()
        else:
            self._fail(row, {"stage": "verify", **result})
        return row

    def _incident_container(self, incident: Incident) -> str:
        """The one container the incident's events are about (from evidence, never a request)."""
        ids = sorted({event.container_id for alert in incident.alerts for event in alert.events if event.container_id})
        if not ids:
            raise ApiError(409, "no_container", f"Incident {incident.id} has no container in its evidence.")
        if len(ids) > 1:
            raise ApiError(
                409, "several_containers", f"Incident {incident.id} names several containers ({', '.join(ids)})."
            )
        return ids[0]

    def _check_stoppable(self, container_id: str) -> None:
        if self.adapter is None:
            raise ApiError(409, "no_lab_adapter", "stop_container needs the lab adapter.")
        try:
            info = self.adapter.inspect(container_id)
            check_test_container(info, self.adapter.project)
        except TargetRefused as exc:
            raise ApiError(409, "target_refused", f"stop_container refused: {exc}.") from exc
        except LabAdapterError as exc:
            raise ApiError(
                409,
                "container_not_found",
                f"Container {container_id} from this incident cannot be inspected ({exc}); "
                "it may have been recreated by cleanup or a lab reset.",
            ) from exc
        if not info.running:
            raise ApiError(409, "container_not_running", f"Container {info.name} is already stopped.")

    def _lab_for(self, incident: Incident) -> Lab:
        lab: Optional[Lab] = self.session.get(Lab, incident.lab_id) if incident.lab_id else None
        if lab is None:
            raise ApiError(409, "no_lab", f"Incident {incident.id} is not linked to a lab.")
        if lab.status != LabStatus.running.value:
            raise ApiError(409, "lab_not_running", f"Lab '{lab.name}' is {lab.status}; start it before approving.")
        return lab

    # --- internals --------------------------------------------------------
    def _pending(self, response_id: uuid.UUID) -> ResponseAction:
        row = self.get(response_id)
        self.session.refresh(row)  # the status as it is now, under the lock
        if row.status != ResponseStatus.pending_approval.value:
            raise ApiError(
                409,
                "response_not_pending",
                f"Response {row.id} is {row.status}; only a pending response can be decided.",
            )
        return row

    def _scenario_for(self, incident: Incident) -> ScenarioDefinition:
        run = self.session.get(ScenarioRun, incident.run_id) if incident.run_id else None
        if run is None:
            raise ApiError(409, "no_scenario_run", f"Incident {incident.id} is not linked to a scenario run.")
        return self.engine.get(run.scenario_id)  # 404 if it is no longer a valid scenario

    def _planned_action(self, scenario: ScenarioDefinition, incident: Incident) -> tuple[str, dict[str, Any]]:
        """The action and parameters from the scenario's `response` section, checked."""
        response = scenario.response
        if response is None:
            raise ApiError(409, "no_response_defined", f"Scenario '{scenario.id}' defines no response action.")
        if response.action not in SUPPORTED_ACTIONS:
            raise ApiError(409, "action_not_supported", f"Response action '{response.action}' is not implemented.")
        if response.action == "stop_container":
            if scenario.target not in self.container_targets:
                raise ApiError(409, "unknown_target", f"'{scenario.target}' is not a container target.")
            # The container comes from the incident's evidence; the scenario's
            # parameters cannot name one.
            return response.action, {"target": scenario.target, "container_id": self._incident_container(incident)}
        max_attempts = response.parameters.get("max_attempts")
        window_seconds = response.parameters.get("window_seconds")
        # Bounded like scenario attempts: verification sends max_attempts + 2 logins.
        highest = MAX_ATTEMPTS - EXTRA_ATTEMPTS
        valid = (
            type(max_attempts) is int
            and 1 <= max_attempts <= highest
            and type(window_seconds) is int
            and 1 <= window_seconds <= 3600
        )
        if not valid:
            raise ApiError(
                409,
                "invalid_response_parameters",
                f"Scenario '{scenario.id}' needs whole-number max_attempts (1-{highest}) "
                "and window_seconds (1-3600) for temporary_rate_limit.",
            )
        return response.action, {"max_attempts": max_attempts, "window_seconds": window_seconds}

    def _target_for(self, incident: Incident, scenario: ScenarioDefinition) -> TargetClient:
        lab: Optional[Lab] = self.session.get(Lab, incident.lab_id) if incident.lab_id else None
        if lab is None:
            raise ApiError(409, "no_lab", f"Incident {incident.id} is not linked to a lab.")
        if scenario.target != lab.target or lab.target not in self.target_clients:
            raise ApiError(
                409, "unknown_target", f"'{scenario.target}' is not the configured target of lab '{lab.name}'."
            )
        if lab.status != LabStatus.running.value:
            raise ApiError(409, "lab_not_running", f"Lab '{lab.name}' is {lab.status}; start it before approving.")
        return self.target_clients[lab.target]

    @staticmethod
    def _username(scenario: ScenarioDefinition) -> str:
        username = scenario.execution.parameters.get("username")
        return username if isinstance(username, str) and username else "lab_user"

    def _fail(self, row: ResponseAction, result: dict[str, Any]) -> None:
        log.warning("response %s failed: %s", row.id, result.get("error") or "verification did not pass")
        row.status = ResponseStatus.failed.value
        row.verification_result = result
        self._audit(row, "response.failed", result)
        self.session.commit()

    def _audit(self, row: ResponseAction, action: str, detail: dict[str, Any]) -> None:
        audit.record(
            self.session,
            actor=self.actor,
            action=action,
            object_type="response_action",
            object_id=str(row.id),
            detail={"incident_id": str(row.incident_id), "action": row.action, **detail},
        )
