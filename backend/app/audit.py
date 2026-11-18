"""Audit trail helper. Adds a row to audit_log; the caller commits."""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from .models import AuditLog

# There is no dashboard login yet (step 6), so every API call is this actor.
ANONYMOUS_ACTOR = "anonymous"


def record(
    session: Session,
    *,
    action: str,
    object_type: str,
    object_id: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
    actor: str = ANONYMOUS_ACTOR,
) -> AuditLog:
    row = AuditLog(actor=actor, action=action, object_type=object_type, object_id=object_id, detail=detail)
    session.add(row)
    return row
