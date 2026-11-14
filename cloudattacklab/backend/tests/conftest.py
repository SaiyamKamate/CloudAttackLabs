"""Shared fixtures. Unit tests use an in-memory SQLite database and fakes, so they need no Docker."""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app.db import get_session  # noqa: E402
from app.lab_manager.adapter import ContainerStatus, LabAdapter, LabAdapterError  # noqa: E402
from app.lab_manager.router import get_adapter, get_target_clients  # noqa: E402
from app.lab_manager.target import LoginResult, TargetError, TargetHealth  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402


class FakeAdapter(LabAdapter):
    """Pretends to be the lab runtime and records what it was asked to do."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.running = False
        self.fail_on: set[str] = set()
        self.generation = 0  # goes up each time the container is (re)created

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail_on:
            raise LabAdapterError(f"fake {name} failure")

    def start(self) -> None:
        self._call("start")
        if self.generation == 0:
            self.generation = 1
        self.running = True

    def stop(self) -> None:
        self._call("stop")
        self.running = False

    def reset(self) -> None:
        self._call("reset")
        self.generation += 1
        self.running = True

    def status(self) -> list[ContainerStatus]:
        self._call("status")
        if self.generation == 0:
            return []
        return [
            ContainerStatus(
                service="shopcloud",
                name="cloudattacklab-lab-shopcloud-1",
                container_id=f"fake{self.generation}",
                state="running" if self.running else "exited",
                health="healthy" if self.running else None,
            )
        ]

    def logs(self, service, since=None) -> str:
        # Canned log text for telemetry collection; empty by default.
        return getattr(self, "log_text", "")


class FakeTarget:
    """Pretends to be ShopCloud, answering only while the fake container runs."""

    def __init__(self, adapter: FakeAdapter) -> None:
        self.adapter = adapter
        self.reset_calls = 0
        self.fail_reset = False
        # test_account pre-flight: which accounts may log in, and a switch to fail them.
        self.valid_accounts = {"lab_user": "lab-user-pass", "lab_restricted": "lab-restricted-pass"}
        self.fail_login = False
        self.logins: list[tuple[str, str]] = []
        self.rate_limit_calls: list[dict] = []

    def health(self) -> TargetHealth:
        if not self.adapter.running:
            return TargetHealth(reachable=False, healthy=False, error="ConnectError: connection refused")
        return TargetHealth(reachable=True, healthy=True, status_code=200, body={"status": "ok"})

    def wait_healthy(self, timeout_seconds: float = 30) -> TargetHealth:
        result = self.health()
        if not result.healthy:
            raise TargetError("fake target is not healthy")
        return result

    def reset(self) -> None:
        self.reset_calls += 1
        if self.fail_reset:
            raise TargetError("POST /lab/reset returned HTTP 403")

    def login(self, username: str, password: str) -> LoginResult:
        self.logins.append((username, password))
        if self.fail_login:
            return LoginResult(ok=False, status_code=401)
        ok = self.valid_accounts.get(username) == password
        return LoginResult(ok=ok, status_code=200 if ok else 401)

    def set_rate_limit(self, enabled: bool, max_attempts=None, window_seconds=None) -> None:
        self.rate_limit_calls.append(
            {"enabled": enabled, "max_attempts": max_attempts, "window_seconds": window_seconds}
        )


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine):
    """A session for looking at what the API wrote."""
    with Session(engine) as session:
        yield session


@pytest.fixture
def app(engine):
    app = create_app()

    def _session():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    return app


@pytest.fixture
def adapter():
    return FakeAdapter()


@pytest.fixture
def target(adapter):
    return FakeTarget(adapter)


@pytest.fixture
def client(app, adapter, target):
    app.dependency_overrides[get_adapter] = lambda: adapter
    app.dependency_overrides[get_target_clients] = lambda: {"shopcloud": target}
    with TestClient(app) as client:
        yield client
