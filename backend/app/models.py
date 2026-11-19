"""Database tables (SQLAlchemy 2.0 style) for PostgreSQL.

Status columns are plain strings checked in Python (see the *Status enums),
which keeps early migrations simple.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from sqlalchemy import JSON, BigInteger, Column, DateTime, ForeignKey, Index, Integer, String, Table, Text, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# --- allowed status values ----------------------------------------------------
class LabStatus(str, Enum):
    created = "created"
    starting = "starting"
    running = "running"
    degraded = "degraded"   # stored as running, but Docker shows a lab container down
    stopped = "stopped"
    error = "error"


class RunStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"      # the script finished and the expected evidence is all there
    incomplete = "incomplete"    # the script finished, but expected telemetry is missing or requests errored
    failed = "failed"
    cleaned_up = "cleaned_up"


class IncidentStatus(str, Enum):
    open = "open"
    closed = "closed"


class ResponseStatus(str, Enum):
    pending_approval = "pending_approval"
    approved = "approved"
    rejected = "rejected"
    executed = "executed"
    verified = "verified"
    failed = "failed"
    reverted = "reverted"


# --- tables -------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Lab(Base):
    __tablename__ = "labs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    target: Mapped[str] = mapped_column(String(64), default="shopcloud")
    environment: Mapped[str] = mapped_column(String(32), default="docker")
    status: Mapped[str] = mapped_column(String(20), default=LabStatus.created.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    runs: Mapped[list["ScenarioRun"]] = relationship(back_populates="lab")


class ScenarioRun(Base):
    """One execution of a scenario. The scenario definition itself lives in YAML."""

    __tablename__ = "scenario_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lab_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("labs.id"))
    scenario_id: Mapped[str] = mapped_column(String(64), index=True)
    scenario_version: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.pending.value)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error: Mapped[Optional[str]] = mapped_column(Text)
    summary: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)  # the script's JSON result

    lab: Mapped[Lab] = relationship(back_populates="runs")


class Event(Base):
    """A normalized event. Columns mirror app/schemas/event.py."""

    __tablename__ = "events"

    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    scenario_id: Mapped[Optional[str]] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(20))
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(10))
    source_ip: Mapped[Optional[str]] = mapped_column(String(45))
    resource: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    raw_event_reference: Mapped[str] = mapped_column(String(300))
    schema_version: Mapped[str] = mapped_column(String(10))
    lab_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("labs.id"))
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("scenario_runs.id"), index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64))
    container_id: Mapped[Optional[str]] = mapped_column(String(80))
    rule_id: Mapped[Optional[str]] = mapped_column(String(120))
    correlation_key: Mapped[Optional[str]] = mapped_column(String(200), index=True)
    raw: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)  # copy of the original record, as evidence


alert_events = Table(
    "alert_events",
    Base.metadata,
    Column("alert_id", ForeignKey("alerts.id"), primary_key=True),
    Column("event_id", ForeignKey("events.event_id"), primary_key=True),
)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    lab_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("labs.id"))
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("scenario_runs.id"))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default=IncidentStatus.open.value)
    risk_score: Mapped[Optional[int]] = mapped_column(Integer)  # 0-100, simple explainable heuristic
    risk_explanation: Mapped[Optional[str]] = mapped_column(Text)
    correlation_key: Mapped[Optional[str]] = mapped_column(String(200), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    alerts: Mapped[list["Alert"]] = relationship(back_populates="incident")
    responses: Mapped[list["ResponseAction"]] = relationship(back_populates="incident")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    lab_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("labs.id"))
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("scenario_runs.id"))
    incident_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("incidents.id"))
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    first_event_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    incident: Mapped[Optional[Incident]] = relationship(back_populates="alerts")
    events: Mapped[list[Event]] = relationship(secondary=alert_events)  # the evidence


class ResponseAction(Base):
    __tablename__ = "response_actions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"))
    action: Mapped[str] = mapped_column(String(40))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default=ResponseStatus.pending_approval.value)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[Optional[str]] = mapped_column(String(64))
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    verification_result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)

    incident: Mapped[Incident] = relationship(back_populates="responses")


class TargetStatus(str, Enum):
    registered = "registered"   # stored; nothing has been sent to it yet
    verified = "verified"       # /health answered and the integration handshake proved the key
    disabled = "disabled"


class AuthorizationStatus(str, Enum):
    active = "active"
    revoked = "revoked"
    expired = "expired"         # computed when read: an active row whose expires_at has passed


class Target(Base):
    """An application registered by URL (V1: kind "shopcloud" only), run outside CloudAttackLab.

    Registering one also creates its lab row (environment "external"), which holds
    its runs, events, alerts and incidents like any other lab.
    """

    __tablename__ = "targets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str] = mapped_column(String(300))       # normalized, built from the pinned address
    resolved_ip: Mapped[str] = mapped_column(String(45))     # the address the URL policy approved
    port: Mapped[int] = mapped_column(Integer)
    integration_key: Mapped[str] = mapped_column(Text)       # write-only through the API
    control_token: Mapped[str] = mapped_column(Text)         # write-only through the API
    status: Mapped[str] = mapped_column(String(20), default=TargetStatus.registered.value)
    instance_id: Mapped[Optional[str]] = mapped_column(String(64))
    app_version: Mapped[Optional[str]] = mapped_column(String(32))
    capabilities: Mapped[Optional[list[str]]] = mapped_column(JSON)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    telemetry_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    lab_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("labs.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    lab: Mapped[Lab] = relationship()
    authorizations: Mapped[list["TargetAuthorization"]] = relationship(
        back_populates="target", order_by="TargetAuthorization.created_at"
    )


class TargetAuthorization(Base):
    """Explicit, scoped and time-boxed permission to run scenarios against a target."""

    __tablename__ = "target_authorizations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("targets.id"))
    scenario_ids: Mapped[list[str]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default=AuthorizationStatus.active.value)
    authorized_by: Mapped[str] = mapped_column(String(64))
    acknowledgement: Mapped[str] = mapped_column(Text)
    max_runs: Mapped[int] = mapped_column(Integer, default=3)
    runs_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    target: Mapped[Target] = relationship(back_populates="authorizations")


class AuditLog(Base):
    """Scenario runs, approvals, response actions and cleanup (guide section 13)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    object_type: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[Optional[str]] = mapped_column(String(64))
    detail: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)


Index("ix_events_type_time", Event.event_type, Event.timestamp)
Index("ix_target_authorizations_lookup", TargetAuthorization.target_id, TargetAuthorization.status)
