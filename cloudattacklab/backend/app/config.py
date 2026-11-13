"""Settings, read from environment variables (see .env.example).

Defaults are for local development only and match docker-compose.yml.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy.engine import URL

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    database_url: str
    # Lab control
    docker_bin: str
    lab_compose_file: Path
    lab_control_token: str
    lab_start_timeout_seconds: int
    # Configured lab targets: name -> base URL as seen from the backend.
    # A lab (and later a scenario) may only name one of these.
    lab_targets: dict[str, str]


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
    )
