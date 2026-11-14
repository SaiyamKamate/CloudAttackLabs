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
# only by the test_account pre-flight check (a real login with the right
# password). Scenario scripts never receive these; they use a wrong password.
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
    )


def _test_accounts() -> dict[str, str]:
    raw = os.environ.get("LAB_TEST_ACCOUNTS")
    if not raw:
        return dict(DEFAULT_TEST_ACCOUNTS)
    parsed = json.loads(raw)
    return {str(k): str(v) for k, v in parsed.items()}
