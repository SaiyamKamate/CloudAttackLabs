"""Lab Manager logic: database rows + the lab adapter + the target client.

There is one physical lab (lab/docker-compose.lab.yml, fixed ports), so only
one lab row may be starting or running at a time.
"""
from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit
from ..errors import ApiError
from ..models import Lab, LabStatus
from .adapter import ContainerStatus, LabAdapter, LabAdapterError
from .target import TargetClient, TargetError, TargetHealth

ACTIVE = (LabStatus.starting.value, LabStatus.running.value)

# One start/stop/reset at a time (single backend process).
_operation_lock = threading.Lock()


@contextmanager
def _exclusive() -> Iterator[None]:
    if not _operation_lock.acquire(blocking=False):
        raise ApiError(409, "lab_busy", "Another lab operation is in progress; try again shortly.")
    try:
        yield
    finally:
        _operation_lock.release()


class LabManager:
    def __init__(
        self,
        session: Session,
        adapter: LabAdapter,
        target_clients: dict[str, TargetClient],
        *,
        actor: str = audit.ANONYMOUS_ACTOR,
    ) -> None:
        self.session = session
        self.adapter = adapter
        self.target_clients = target_clients  # the configured lab targets
        self.actor = actor

    # --- create / read ----------------------------------------------------
    def create(self, name: str, target: str) -> Lab:
        if target not in self.target_clients:
            raise ApiError(
                422, "unknown_target", f"'{target}' is not a configured lab target {sorted(self.target_clients)}."
            )
        lab = Lab(name=name, target=target)
        self.session.add(lab)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise ApiError(409, "lab_name_taken", f"A lab named '{name}' already exists.") from None
        return lab

    def list(self) -> list[Lab]:
        return list(self.session.scalars(select(Lab).order_by(Lab.created_at)))

    def get(self, lab_id: uuid.UUID) -> Lab:
        lab = self.session.get(Lab, lab_id)
        if lab is None:
            raise ApiError(404, "lab_not_found", f"No lab with id {lab_id}.")
        return lab

    def details(self, lab_id: uuid.UUID) -> tuple[Lab, list[ContainerStatus], TargetHealth]:
        lab = self.get(lab_id)
        other = self._other_active_lab(lab)
        if other is not None:
            # The physical lab currently belongs to another lab row; do not report its containers as ours.
            return lab, [], TargetHealth(reachable=False, healthy=False, error=f"lab '{other.name}' is using the lab")
        try:
            containers = self.adapter.status()
        except LabAdapterError as exc:
            raise ApiError(502, "lab_runtime_error", str(exc)) from exc
        return lab, containers, self._client(lab).health()

    # --- start / stop / reset ---------------------------------------------
    def start(self, lab_id: uuid.UUID) -> Lab:
        lab = self.get(lab_id)
        with _exclusive():
            other = self._other_active_lab(lab)
            if other is not None:
                raise ApiError(409, "lab_conflict", f"Lab '{other.name}' is {other.status}; stop it first.")
            previous = lab.status
            lab.status = LabStatus.starting.value
            self.session.commit()
            try:
                self.adapter.start()
                self._client(lab).wait_healthy()
            except (LabAdapterError, TargetError) as exc:
                self._finish(lab, "lab.start", LabStatus.error, previous, error=str(exc))
                raise ApiError(502, "lab_start_failed", str(exc)) from exc
            self._finish(lab, "lab.start", LabStatus.running, previous)
        return lab

    def stop(self, lab_id: uuid.UUID) -> Lab:
        lab = self.get(lab_id)
        with _exclusive():
            if lab.status in (LabStatus.created.value, LabStatus.stopped.value):
                raise ApiError(409, "lab_not_running", f"Lab '{lab.name}' is {lab.status}; there is nothing to stop.")
            previous = lab.status
            try:
                self.adapter.stop()
            except LabAdapterError as exc:
                self._finish(lab, "lab.stop", LabStatus.error, previous, error=str(exc))
                raise ApiError(502, "lab_stop_failed", str(exc)) from exc
            self._finish(lab, "lab.stop", LabStatus.stopped, previous)
        return lab

    def reset(self, lab_id: uuid.UUID) -> Lab:
        lab = self.get(lab_id)
        with _exclusive():
            if lab.status != LabStatus.running.value:
                raise ApiError(409, "lab_not_running", f"Lab '{lab.name}' is {lab.status}; start it before a reset.")
            previous = lab.status
            client = self._client(lab)
            try:
                # 1. fresh containers; 2. the target restores its own data, which may
                # live outside the container (the real ShopCloud can have a database).
                self.adapter.reset()
                client.wait_healthy()
                client.reset()
            except (LabAdapterError, TargetError) as exc:
                self._finish(lab, "lab.reset", LabStatus.error, previous, error=str(exc))
                raise ApiError(502, "lab_reset_failed", str(exc)) from exc
            self._finish(lab, "lab.reset", LabStatus.running, previous)
        return lab

    # --- internals --------------------------------------------------------
    def _client(self, lab: Lab) -> TargetClient:
        return self.target_clients[lab.target]

    def _other_active_lab(self, lab: Lab) -> Optional[Lab]:
        return self.session.scalars(select(Lab).where(Lab.id != lab.id, Lab.status.in_(ACTIVE))).first()

    def _finish(
        self, lab: Lab, action: str, status: LabStatus, previous: str, *, error: Optional[str] = None
    ) -> None:
        """Save the new status and the audit row in one commit."""
        lab.status = status.value
        detail: dict[str, Any] = {
            "lab_name": lab.name,
            "outcome": "failed" if error else "success",
            "status_before": previous,
            "status_after": status.value,
        }
        if error:
            detail["error"] = error
        audit.record(
            self.session, actor=self.actor, action=action, object_type="lab", object_id=str(lab.id), detail=detail
        )
        self.session.commit()
