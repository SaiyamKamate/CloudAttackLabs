"""Risk score: a simple, explainable 0-100 heuristic for one incident.

No machine learning and no hidden weights. The score is the sum of the lines in
its explanation, limited to 0-100:

- base points for the highest alert severity,
- plus points for events over the detection threshold,
- plus points for each additional correlated alert,
- minus points when a response action was verified as working.

It only compares incidents inside this lab. It does not predict financial loss
and is not a universal measure of risk; the explanation says so.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from ..models import Incident

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
) -> Risk:
    """Score one incident. Pure: the same input always gives the same result.

    severities / event_counts: one entry per alert of the incident.
    threshold: the scenario's detection threshold, if known.
    verified_response: the action name of a response that was verified, if any.
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
    verified = next(
        (
            response.action
            for response in sorted(incident.responses, key=lambda r: r.requested_at)
            if response.verified_at is not None and (response.verification_result or {}).get("passed") is True
        ),
        None,
    )
    risk = compute_risk(
        severities=[alert.severity for alert in alerts],
        event_counts=[alert.event_count for alert in alerts],
        threshold=threshold,
        verified_response=verified,
    )
    incident.risk_score = risk.score
    incident.risk_explanation = risk.explanation
    return risk
