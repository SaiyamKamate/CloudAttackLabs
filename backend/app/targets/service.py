"""Target Management: register, verify, verify telemetry, authorize, revoke.

Registration and authorization are separate steps (plan section 11):

    registered --verify--> verified --verify telemetry--> (telemetry verified) --authorize--> runs allowed

- Registering checks the URL against the policy and sends nothing.
- Verifying sends only GET /health and the signed handshake.
- Verifying telemetry asks the target for one test event and reads it back
  through the signed event feed.
- Only an active, unexpired authorization that names the scenario allows
  scenario traffic (checked by the Scenario Engine before every run).

Every step writes an audit row. Secrets never appear in audit rows or answers.
"""
from __future__ import annotations

import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit
from ..config import Settings
from ..errors import ApiError
from ..models import AuthorizationStatus, Lab, LabStatus, Target, TargetAuthorization, TargetStatus
from .integration_client import IntegrationClient, IntegrationError, Pinned, handshake_proof
from .url_policy import UrlRefused, check_pinned, check_url

SUPPORTED_KINDS = ("shopcloud",)
TELEMETRY_SCHEMA = "shopcloud.security_event/1"
REQUIRED_CAPABILITIES = ("login_endpoint", "rate_limit_control", "telemetry_feed_v1")
EXTERNAL = "external"  # labs.environment of a registered target's lab
TELEMETRY_CHECK_SECONDS = 10.0

ClientFactory = Callable[[Target, Settings], IntegrationClient]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite returns naive datetimes; they are UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def effective_status(authorization: TargetAuthorization, now: Optional[datetime] = None) -> str:
    now = now or utcnow()
    if authorization.status == AuthorizationStatus.active.value and aware(authorization.expires_at) <= now:
        return AuthorizationStatus.expired.value
    if authorization.status == AuthorizationStatus.active.value and authorization.runs_used >= authorization.max_runs:
        return AuthorizationStatus.expired.value
    return authorization.status


def acknowledgement_text(base_url: str, scenario_ids: list[str], expires_at: datetime) -> str:
    return (
        f"I confirm that {base_url} is a lab system I control, and I authorize CloudAttackLab to run "
        f"{', '.join(scenario_ids)} against it until {expires_at.isoformat(timespec='seconds')}."
    )


def pinned_of(target: Target) -> Pinned:
    return Pinned(base_url=target.base_url, ip=target.resolved_ip, port=target.port)


def make_rechecker(settings: Settings) -> Callable[[Pinned], None]:
    def recheck(pinned: Pinned) -> None:
        check_pinned(
            pinned.base_url, pinned.ip, pinned.port,
            allowed_cidrs=settings.target_allowed_cidrs, allowed_ports=settings.target_allowed_ports,
        )

    return recheck


def default_client_factory(target: Target, settings: Settings) -> IntegrationClient:
    return IntegrationClient(
        pinned_of(target),
        target.integration_key,
        recheck=make_rechecker(settings),
        timeout_seconds=settings.integration_timeout_seconds,
    )


class TargetService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        client_factory: ClientFactory = default_client_factory,
        scenario_kinds: Optional[dict[str, str]] = None,  # scenario id -> its target kind
        reserved_names: tuple[str, ...] = (),
        actor: str = audit.ANONYMOUS_ACTOR,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.session = session
        self.settings = settings
        self.client_factory = client_factory
        self.scenario_kinds = scenario_kinds or {}
        self.reserved_names = reserved_names
        self.actor = actor
        self._sleep = sleep
        self._clock = clock

    # --- read -----------------------------------------------------------------
    def list(self) -> list[Target]:
        return list(self.session.scalars(select(Target).order_by(Target.created_at)))

    def get(self, target_id: uuid.UUID) -> Target:
        target = self.session.get(Target, target_id)
        if target is None:
            raise ApiError(404, "target_not_found", f"No target with id {target_id}.")
        return target

    # --- register ---------------------------------------------------------------
    def register(self, *, name: str, kind: str, base_url: str, integration_key: str, control_token: str) -> Target:
        if kind not in SUPPORTED_KINDS:
            raise ApiError(422, "unsupported_kind", f"V1 supports only these target kinds: {', '.join(SUPPORTED_KINDS)}.")
        if name in self.reserved_names:
            raise ApiError(409, "target_name_taken", f"'{name}' is the name of a built-in lab target; choose another.")
        try:
            approved = check_url(
                base_url,
                allowed_cidrs=self.settings.target_allowed_cidrs,
                allowed_ports=self.settings.target_allowed_ports,
            )
        except UrlRefused as exc:
            # Refusals are audited too: an attempt to register a forbidden URL is worth seeing.
            audit.record(
                self.session, actor=self.actor, action="target.register_refused", object_type="target",
                detail={"name": name, "kind": kind, "base_url": base_url[:300], "reason": str(exc)},
            )
            self.session.commit()
            raise ApiError(422, "url_refused", f"{base_url} is refused: {exc}.") from None

        lab = Lab(name=f"{name} (external)", target=name, environment=EXTERNAL, status=LabStatus.created.value)
        target = Target(
            name=name,
            kind=kind,
            base_url=approved.base_url,
            resolved_ip=approved.ip,
            port=approved.port,
            integration_key=integration_key,
            control_token=control_token,
            status=TargetStatus.registered.value,
            lab=lab,
        )
        self.session.add_all([lab, target])
        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            raise ApiError(409, "target_name_taken", f"A target or lab named '{name}' already exists.") from None
        self._audit(target, "target.registered", {
            "name": name, "kind": kind, "base_url": approved.base_url, "resolved_ip": approved.ip,
            "port": approved.port, "lab_id": str(lab.id),
        })
        self.session.commit()
        return target

    # --- verify the connection -----------------------------------------------------
    def verify(self, target_id: uuid.UUID) -> Target:
        target = self._usable(target_id)
        client = self.client_factory(target, self.settings)
        try:
            health = client.health()
            if not health.healthy:
                raise IntegrationError(
                    f"GET /health did not answer 200 {{\"status\": \"ok\"}}: "
                    f"{health.error or f'HTTP {health.status_code}'}"
                )
            challenge = secrets.token_hex(32)
            answer = client.handshake(challenge, str(target.id))
            identity = _check_handshake(answer, target, challenge)
        except IntegrationError as exc:
            target.status = TargetStatus.registered.value
            target.telemetry_verified_at = None
            target.lab.status = LabStatus.error.value  # no run can start until it verifies again
            self._audit(target, "target.verify_failed", {"error": str(exc)})
            self.session.commit()
            raise ApiError(502, "handshake_failed", f"Connection check failed: {exc}") from None

        if target.instance_id and target.instance_id != identity["instance_id"]:
            # Another instance answers now: its telemetry must be verified again.
            target.telemetry_verified_at = None
        target.status = TargetStatus.verified.value
        target.instance_id = identity["instance_id"]
        target.app_version = identity["app_version"]
        target.capabilities = identity["capabilities"]
        target.verified_at = utcnow()
        # The lab row shows the target as running while it answers and proves its identity.
        target.lab.status = LabStatus.running.value
        self._audit(target, "target.verified", {
            "instance_id": target.instance_id, "app_version": target.app_version,
            "capabilities": target.capabilities, "interface_version": identity["interface_version"],
        })
        self.session.commit()
        return target

    # --- verify telemetry -------------------------------------------------------------
    def verify_telemetry(self, target_id: uuid.UUID) -> Target:
        target = self._usable(target_id)
        if target.status != TargetStatus.verified.value:
            raise ApiError(409, "not_verified", "Verify the connection first.")
        client = self.client_factory(target, self.settings)
        nonce = secrets.token_hex(16)
        since = (utcnow() - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            client.telemetry_test(nonce)
            event = self._wait_for_test_event(client, target, nonce, since)
        except IntegrationError as exc:
            target.telemetry_verified_at = None
            self._audit(target, "target.telemetry_verify_failed", {"error": str(exc)})
            self.session.commit()
            raise ApiError(502, "telemetry_check_failed", f"Telemetry check failed: {exc}") from None
        target.telemetry_verified_at = utcnow()
        self._audit(target, "target.telemetry_verified", {
            "test_event_seq": event.get("seq"), "request_id": event.get("request_id"),
            "schema": event.get("schema"), "instance_id": event.get("instance_id"),
        })
        self.session.commit()
        return target

    def _wait_for_test_event(self, client: IntegrationClient, target: Target, nonce: str, since: str) -> dict:
        deadline = self._clock() + TELEMETRY_CHECK_SECONDS
        while True:
            after_seq = 0
            for _ in range(20):  # at most 20 pages per look
                page = client.events(since=since, after_seq=after_seq, limit=1000)
                events = page.get("events")
                if not isinstance(events, list):
                    raise IntegrationError("the event feed did not return a list of events")
                for event in events:
                    if isinstance(event, dict) and event.get("test_nonce") == nonce:
                        _check_record(event, target)
                        return event
                next_seq = page.get("next_seq")
                if not events or not isinstance(next_seq, int) or next_seq <= after_seq:
                    break
                after_seq = next_seq
            if self._clock() >= deadline:
                raise IntegrationError(
                    f"the test event did not appear in the event feed within {TELEMETRY_CHECK_SECONDS:g} s"
                )
            self._sleep(1)

    # --- authorize -------------------------------------------------------------------------
    def authorize(
        self,
        target_id: uuid.UUID,
        *,
        scenario_ids: list[str],
        duration_minutes: int,
        authorized_by: str,
        acknowledgement: bool,
        max_runs: int,
    ) -> TargetAuthorization:
        target = self._usable(target_id)
        if target.status != TargetStatus.verified.value or target.telemetry_verified_at is None:
            raise ApiError(
                409, "not_verified",
                "Verify the connection and the telemetry integration before authorizing an experiment.",
            )
        if not acknowledgement:
            raise ApiError(422, "acknowledgement_required", "Confirm that this is a lab system you control.")
        scenario_ids = sorted(set(scenario_ids))
        wrong = [s for s in scenario_ids if self.scenario_kinds.get(s) != target.kind]
        if not scenario_ids or wrong:
            allowed = sorted(s for s, kind in self.scenario_kinds.items() if kind == target.kind)
            raise ApiError(
                422, "scenario_not_allowed",
                f"These scenarios cannot run against a '{target.kind}' target: {', '.join(wrong) or 'none given'}. "
                f"Allowed: {', '.join(allowed) or 'none'}.",
            )
        if not 1 <= duration_minutes <= self.settings.target_authorization_max_minutes:
            raise ApiError(
                422, "invalid_duration",
                f"duration_minutes must be from 1 to {self.settings.target_authorization_max_minutes}.",
            )
        current = self.active_authorization(target)
        if current is not None:
            raise ApiError(
                409, "authorization_active",
                f"Authorization {current.id} is active until {aware(current.expires_at).isoformat(timespec='seconds')}; "
                "revoke it first.",
            )
        expires_at = utcnow() + timedelta(minutes=duration_minutes)
        row = TargetAuthorization(
            target=target,
            scenario_ids=scenario_ids,
            status=AuthorizationStatus.active.value,
            authorized_by=authorized_by,
            acknowledgement=acknowledgement_text(target.base_url, scenario_ids, expires_at),
            max_runs=max_runs,
            runs_used=0,
            expires_at=expires_at,
        )
        self.session.add(row)
        self.session.flush()
        self._audit(target, "target.authorized", {
            "authorization_id": str(row.id), "scenario_ids": scenario_ids, "authorized_by": authorized_by,
            "expires_at": expires_at.isoformat(), "max_runs": max_runs, "acknowledgement": row.acknowledgement,
        })
        self.session.commit()
        return row

    def revoke(self, target_id: uuid.UUID, authorization_id: uuid.UUID) -> TargetAuthorization:
        target = self.get(target_id)
        row = self.session.get(TargetAuthorization, authorization_id)
        if row is None or row.target_id != target.id:
            raise ApiError(404, "authorization_not_found", f"No authorization {authorization_id} for this target.")
        if effective_status(row) != AuthorizationStatus.active.value:
            raise ApiError(409, "authorization_not_active", f"Authorization {row.id} is {effective_status(row)}.")
        row.status = AuthorizationStatus.revoked.value
        row.revoked_at = utcnow()
        self._audit(target, "target.authorization_revoked", {"authorization_id": str(row.id)})
        self.session.commit()
        return row

    def active_authorization(self, target: Target) -> Optional[TargetAuthorization]:
        now = utcnow()
        for row in target.authorizations:
            if effective_status(row, now) == AuthorizationStatus.active.value:
                return row
        return None

    # --- internals --------------------------------------------------------------------
    def _usable(self, target_id: uuid.UUID) -> Target:
        target = self.get(target_id)
        if target.status == TargetStatus.disabled.value:
            raise ApiError(409, "target_disabled", f"Target '{target.name}' is disabled.")
        return target

    def _audit(self, target: Target, action: str, detail: dict) -> None:
        audit.record(
            self.session, actor=self.actor, action=action, object_type="target", object_id=str(target.id),
            detail={"target_name": target.name, **detail},
        )


def _check_handshake(answer: dict, target: Target, challenge: str) -> dict:
    """The identity in a handshake answer, after checking it is ShopCloud and holds the key."""
    instance_id = answer.get("instance_id")
    if not isinstance(instance_id, str) or not 1 <= len(instance_id) <= 64:
        raise IntegrationError("the handshake has no valid instance_id")
    expected = handshake_proof(target.integration_key, challenge, str(target.id), instance_id)
    proof = answer.get("proof")
    if not isinstance(proof, str) or not secrets.compare_digest(proof, expected):
        raise IntegrationError("the handshake proof is wrong: the application does not hold this integration key")
    if answer.get("kind") != target.kind or answer.get("app") != target.kind:
        raise IntegrationError(f"the application says it is '{answer.get('kind')}', not '{target.kind}'")
    if answer.get("telemetry_schema") != TELEMETRY_SCHEMA:
        raise IntegrationError(f"unsupported telemetry schema '{answer.get('telemetry_schema')}'")
    capabilities = answer.get("capabilities")
    if not isinstance(capabilities, list) or not all(isinstance(c, str) for c in capabilities):
        raise IntegrationError("the handshake has no capability list")
    missing = [c for c in REQUIRED_CAPABILITIES if c not in capabilities]
    if missing:
        raise IntegrationError(f"the application lacks required capabilities: {', '.join(missing)}")
    app_version = answer.get("app_version")
    interface_version = answer.get("interface_version")
    return {
        "instance_id": instance_id,
        "app_version": app_version[:32] if isinstance(app_version, str) else None,
        "interface_version": interface_version if isinstance(interface_version, str) else None,
        "capabilities": capabilities[:20],
    }


def _check_record(event: dict, target: Target) -> None:
    if event.get("schema") != TELEMETRY_SCHEMA:
        raise IntegrationError(f"the test event has schema '{event.get('schema')}', not '{TELEMETRY_SCHEMA}'")
    if event.get("instance_id") != target.instance_id:
        raise IntegrationError("the test event comes from another instance than the one verified")
    for field in ("timestamp", "event", "request_id"):
        if not isinstance(event.get(field), str) or not event.get(field):
            raise IntegrationError(f"the test event has no '{field}'")
