"""Risk score: a simple, explainable 0-100 heuristic for one incident.

No machine learning and no hidden weights. The score is the sum of the lines in
its explanation, limited to 0-100:

- base points for the highest alert severity,
- plus points for events over the detection threshold,
- plus points for each additional correlated alert,
- minus points when a response action was verified as working and is still
  applied. Once cleanup or a lab reset reverted it, it no longer lowers the
  score, and the explanation says so.

It only compares incidents inside this lab. It does not predict financial loss
and is not a universal measure of risk; the explanation says so.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from sqlalchemy import select

from ..models import AuditLog, Incident, ResponseAction, ResponseStatus, ScenarioRun

SEVERITY_POINTS = {"info": 10, "low": 25, "medium": 40, "high": 60, "critical": 80}
SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]
POINTS_PER_EVENT_OVER_THRESHOLD = 2
MAX_VOLUME_POINTS = 20
POINTS_PER_EXTRA_ALERT = 5
MAX_EXTRA_ALERT_POINTS = 15
VERIFIED_RESPONSE_POINTS = 20

DISCLAIMER = (
    "This score is a simple rule of thumb for comparing incidents inside this lab. "
    "It does not predict financial loss and is not a universal measure of risk."
)


@dataclass(frozen=True)
class RiskFactor:
    points: int
    reason: str


@dataclass(frozen=True)
class Risk:
    score: int
    factors: tuple[RiskFactor, ...]
    explanation: str


def compute_risk(
    *,
    severities: list[str],
    event_counts: list[int],
    threshold: Optional[int],
    verified_response: Optional[str] = None,
    reverted_response: Optional[tuple[str, str]] = None,
) -> Risk:
    """Score one incident. Pure: the same input always gives the same result.

    severities / event_counts: one entry per alert of the incident.
    threshold: the scenario's detection threshold, if known.
    verified_response: the action name of a verified response that is still applied, if any.
    reverted_response: (action, what reverted it) of a response that was verified as
        working but has been reverted since; it is listed with 0 points.
    """
    factors: list[RiskFactor] = []

    known = [s for s in severities if s in SEVERITY_POINTS]
    if known:
        highest = max(known, key=SEVERITY_ORDER.index)
        factors.append(RiskFactor(SEVERITY_POINTS[highest], f"the highest alert severity is {highest}"))
    else:
        factors.append(RiskFactor(0, "the incident has no alert with a known severity"))

    if threshold is None:
        factors.append(RiskFactor(0, "the detection threshold is not known, so event volume adds nothing"))
    else:
        over = sum(max(0, count - threshold) for count in event_counts)
        points = min(MAX_VOLUME_POINTS, over * POINTS_PER_EVENT_OVER_THRESHOLD)
        factors.append(
            RiskFactor(
                points,
                f"{over} event(s) over the detection threshold of {threshold} "
                f"({POINTS_PER_EVENT_OVER_THRESHOLD} points each, at most {MAX_VOLUME_POINTS})",
            )
        )

    extra = max(0, len(severities) - 1)
    factors.append(
        RiskFactor(
            min(MAX_EXTRA_ALERT_POINTS, extra * POINTS_PER_EXTRA_ALERT),
            f"{extra} additional correlated alert(s) "
            f"({POINTS_PER_EXTRA_ALERT} points each, at most {MAX_EXTRA_ALERT_POINTS})",
        )
    )

    if verified_response:
        factors.append(
            RiskFactor(-VERIFIED_RESPONSE_POINTS, f"a response action ({verified_response}) was verified as working")
        )
    elif reverted_response:
        action, trigger = reverted_response
        factors.append(
            RiskFactor(
                0,
                f"a response action ({action}) was verified as working, but it was reverted by {trigger}, "
                f"so it no longer lowers the score (it gave -{VERIFIED_RESPONSE_POINTS} while it was applied)",
            )
        )

    total = sum(factor.points for factor in factors)
    score = max(0, min(100, total))

    lines = [f"Risk score: {score} out of 100."]
    lines += [f"{factor.points:+d}: {factor.reason}." for factor in factors]
    if score != total:
        lines.append(f"The sum is {total}; the score is limited to the range 0-100.")
    lines.append(DISCLAIMER)
    return Risk(score=score, factors=tuple(factors), explanation="\n".join(lines))


def refresh_risk(session: Session, incident: Incident, threshold: Optional[int]) -> Risk:
    """Recompute and store the incident's score and explanation. The caller commits."""
    alerts = sorted(incident.alerts, key=lambda alert: str(alert.id))
    worked = [
        response
        for response in sorted(incident.responses, key=lambda r: r.requested_at)
        if response.verified_at is not None and (response.verification_result or {}).get("passed") is True
    ]
    applied = next((r.action for r in worked if r.status == ResponseStatus.verified.value), None)
    reverted = next((r for r in reversed(worked) if r.status == ResponseStatus.reverted.value), None)
    risk = compute_risk(
        severities=[alert.severity for alert in alerts],
        event_counts=[alert.event_count for alert in alerts],
        threshold=threshold,
        verified_response=applied,
        reverted_response=(reverted.action, _reverted_by(session, reverted)) if reverted and not applied else None,
    )
    incident.risk_score = risk.score
    incident.risk_explanation = risk.explanation
    return risk


def _reverted_by(session: Session, response: ResponseAction) -> str:
    """What reverted a response, from its response.reverted audit row (e.g. "cleanup:manual", "lab.reset")."""
    row = session.scalars(
        select(AuditLog)
        .where(
            AuditLog.object_type == "response_action",
            AuditLog.object_id == str(response.id),
            AuditLog.action == "response.reverted",
        )
        .order_by(AuditLog.id.desc())
    ).first()
    trigger = (row.detail or {}).get("trigger") if row is not None else None
    return trigger or "cleanup"


def scenario_threshold(session: Session, incident: Incident) -> Optional[int]:
    """The detection threshold of the scenario behind the incident's run, if it can be read."""
    run = session.get(ScenarioRun, incident.run_id) if incident.run_id else None
    if run is None:
        return None
    from ..errors import ApiError
    from ..scenario_engine.router import get_scenario_engine  # late: avoids an import cycle

    try:
        return get_scenario_engine().get(run.scenario_id).detection.threshold
    except ApiError:
        return None

