"""Settings, read from environment variables (see .env.example).

Defaults are for local development only and match docker-compose.yml.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy.engine import URL

REPO_ROOT = Path(__file__).resolve().parents[2]

# Synthetic lab test accounts, documented in docs/shopcloud-interface.md. Used
# by the test_account pre-flight check and the access_remains_denied
# verification (a real login with the right password). The engine never passes
# them to a scenario script. authentication_abuse uses a wrong password;
# unauthorized_resource_access must log in as lab_restricted, so it reads
# LAB_TEST_ACCOUNTS itself and otherwise uses the documented default.
DEFAULT_TEST_ACCOUNTS = {
    "lab_user": "lab-user-pass",
    "lab_restricted": "lab-restricted-pass",
}


@dataclass(frozen=True)
class Settings:
    database_url: str
    # Lab control
    docker_bin: str
    lab_compose_file: Path
    lab_control_token: str
    lab_start_timeout_seconds: int
    # Configured lab targets: name -> base URL as seen from the backend.
    # A lab (and a scenario) may only name one of these.
    lab_targets: dict[str, str]
    # Scenario Engine
    scenarios_dir: Path
    scenario_scripts_dir: Path
    lab_test_accounts: dict[str, str]
    # Telemetry and detection
    rules_dir: Path
    telemetry_service: str               # which lab service's logs to collect
    telemetry_collect_interval_seconds: float
    telemetry_since_grace_seconds: float  # widen the collection window slightly
    # Where telemetry comes from: "docker" (the lab's container logs, the
    # default) or "loki" (the monitoring stack). No fallback between them.
    telemetry_source: str
    loki_url: str
    # Logs reach Loki a little late: after a run, poll until the count of
    # records stops growing or this timeout passes.
    loki_settle_interval_seconds: float
    loki_settle_timeout_seconds: float


TELEMETRY_SOURCES = ("docker", "loki")


def _database_url() -> str:
    explicit = os.environ.get("DATABASE_URL")
    if explicit:
        return explicit
    return URL.create(
        "postgresql+psycopg",
        username=os.environ.get("POSTGRES_USER", "cloudattacklab"),
        password=os.environ.get("POSTGRES_PASSWORD", "change-me-locally"),
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        database=os.environ.get("POSTGRES_DB", "cloudattacklab"),
    ).render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings(
        database_url=_database_url(),
        docker_bin=os.environ.get("DOCKER_BIN", "docker"),
        lab_compose_file=Path(
            os.environ.get("LAB_COMPOSE_FILE", str(REPO_ROOT / "lab" / "docker-compose.lab.yml"))
        ),
        lab_control_token=os.environ.get("LAB_CONTROL_TOKEN", "lab-dev-token"),
        lab_start_timeout_seconds=int(os.environ.get("LAB_START_TIMEOUT_SECONDS", "180")),
        lab_targets={"shopcloud": os.environ.get("SHOPCLOUD_URL", "http://127.0.0.1:8081").rstrip("/")},
        scenarios_dir=Path(os.environ.get("SCENARIOS_DIR", str(REPO_ROOT / "scenarios"))),
        scenario_scripts_dir=Path(
            os.environ.get("SCENARIO_SCRIPTS_DIR", str(REPO_ROOT / "scenarios" / "scripts"))
        ),
        lab_test_accounts=_test_accounts(),
        rules_dir=Path(os.environ.get("RULES_DIR", str(REPO_ROOT / "rules"))),
        telemetry_service=os.environ.get("TELEMETRY_SERVICE", "shopcloud"),
        telemetry_collect_interval_seconds=float(os.environ.get("TELEMETRY_COLLECT_INTERVAL_SECONDS", "2")),
        telemetry_since_grace_seconds=float(os.environ.get("TELEMETRY_SINCE_GRACE_SECONDS", "3")),
        telemetry_source=_telemetry_source(),
        loki_url=os.environ.get("LOKI_URL", "http://127.0.0.1:3100").rstrip("/"),
        loki_settle_interval_seconds=float(os.environ.get("LOKI_SETTLE_INTERVAL_SECONDS", "2")),
        loki_settle_timeout_seconds=float(os.environ.get("LOKI_SETTLE_TIMEOUT_SECONDS", "15")),
    )


def _telemetry_source() -> str:
    value = os.environ.get("TELEMETRY_SOURCE", "docker").strip().lower()
    if value not in TELEMETRY_SOURCES:
        raise ValueError(f"TELEMETRY_SOURCE must be 'docker' or 'loki', not '{value}'.")
    return value


def _test_accounts() -> dict[str, str]:
    raw = os.environ.get("LAB_TEST_ACCOUNTS")
    if not raw:
        return dict(DEFAULT_TEST_ACCOUNTS)
    parsed = json.loads(raw)
    return {str(k): str(v) for k, v in parsed.items()}
