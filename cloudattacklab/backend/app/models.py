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
    stopped = "stopped"
    error = "error"


class RunStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
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
