"""One place that answers "what does this lab's traffic go to?" (plan section 12.2).

- A Docker lab (environment "docker"): the configured target client and URL
  (SHOPCLOUD_URL), exactly as before.
- A registered target's lab (environment "external"): the target's pinned URL,
  its own control token, and its integration client. Every request re-checks the
  pinned URL against the URL policy first (GuardedTargetClient), so a policy
  change takes effect immediately and nothing follows a redirect.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..errors import ApiError
from ..lab_manager.target import HttpTargetClient, TargetClient
from ..lab_manager.target_guard import GuardedTargetClient, TargetUrlCheck
from ..models import Lab, Target, TargetAuthorization
from .integration_client import IntegrationClient
from .service import (
    EXTERNAL,
    ClientFactory,
    default_client_factory,
    effective_status,
    utcnow,
)
from .url_policy import UrlRefused, check_pinned


def get_client_factory() -> ClientFactory:
    """FastAPI dependency: how the backend talks to a target's integration API (a fake in tests)."""
    return default_client_factory


class PinnedGuard:
    """TargetGuard's check() for one registered target: its pinned URL against the URL policy."""

    def __init__(self, target: Target, settings: Settings) -> None:
        self.target = target
        self.settings = settings

    def check(self, name: str) -> TargetUrlCheck:
        try:
            check_pinned(
                self.target.base_url, self.target.resolved_ip, self.target.port,
                allowed_cidrs=self.settings.target_allowed_cidrs,
                allowed_ports=self.settings.target_allowed_ports,
            )
        except UrlRefused as exc:
            return TargetUrlCheck(name, self.target.base_url, False, f"Target '{name}' URL {self.target.base_url} is refused: {exc}.")
        return TargetUrlCheck(name, self.target.base_url, True)


@dataclass
class ResolvedTarget:
    kind: str                   # what scenarios name as their target ("shopcloud")
    base_url: str
    client: TargetClient        # health, login, rate limit, reset (guarded)
    url_check: TargetUrlCheck
    target: Optional[Target] = None                 # set for a registered target
    integration: Optional[IntegrationClient] = None  # set for a registered target

    @property
    def external(self) -> bool:
        return self.target is not None


def is_external(lab: Lab) -> bool:
    return lab.environment == EXTERNAL


def target_of_lab(session: Session, lab: Lab) -> Target:
    target = session.scalars(select(Target).where(Target.lab_id == lab.id)).first()
    if target is None:
        raise ApiError(409, "no_target", f"Lab '{lab.name}' has no registered target.")
    return target


def resolve_external(
    session: Session, lab: Lab, settings: Settings, client_factory: ClientFactory = default_client_factory
) -> ResolvedTarget:
    target = target_of_lab(session, lab)
    guard = PinnedGuard(target, settings)
    integration = client_factory(target, settings)
    inner = HttpTargetClient(
        target.base_url, target.control_token,
        timeout_seconds=settings.integration_timeout_seconds, http=integration.http,
    )
    return ResolvedTarget(
        kind=target.kind,
        base_url=target.base_url,
        client=GuardedTargetClient(inner, target.name, guard),
        url_check=guard.check(target.name),
        target=target,
        integration=integration,
    )


def resolve_lab_target(
    session: Session,
    lab: Lab,
    settings: Settings,
    target_clients: dict[str, TargetClient],
    guard=None,
    client_factory: ClientFactory = default_client_factory,
) -> Optional[ResolvedTarget]:
    """The lab's HTTP target, or None if the lab has none (unknown target name)."""
    if is_external(lab):
        return resolve_external(session, lab, settings, client_factory)
    client = target_clients.get(lab.target)
    if client is None:
        return None
    check = guard.check(lab.target) if guard is not None else TargetUrlCheck(lab.target, "", True)
    return ResolvedTarget(
        kind=lab.target, base_url=settings.lab_targets.get(lab.target, ""), client=client, url_check=check
    )


def authorization_for(target: Target, scenario_id: str) -> Optional[TargetAuthorization]:
    """The active, unexpired authorization with run budget left that names this scenario."""
    now = utcnow()
    for row in target.authorizations:
        if effective_status(row, now) == "active" and scenario_id in (row.scenario_ids or []):
            return row
    return None


def require_authorization(target: Target, scenario_id: str) -> TargetAuthorization:
    row = authorization_for(target, scenario_id)
    if row is None:
        raise ApiError(
            409,
            "not_authorized",
            f"Target '{target.name}' has no active authorization for '{scenario_id}'. "
            "Authorize a controlled experiment in Target Management first.",
        )
    return row
