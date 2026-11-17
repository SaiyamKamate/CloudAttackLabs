"""Checks the backend runs once at startup. They only log; they never stop the backend,
so the dashboard can still show what is wrong."""
from __future__ import annotations

import logging

from sqlalchemy.engine import make_url

from .config import Settings
from .lab_manager.adapter import LabAdapter
from .lab_manager.target_guard import TargetGuard

log = logging.getLogger("cloudattacklab.startup")


def configure_logging() -> None:
    """Make the backend's own warnings visible in `docker compose logs backend`."""
    logger = logging.getLogger("cloudattacklab")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False


def check_lab_targets(settings: Settings, adapter: LabAdapter) -> list[str]:
    """Log an error for every lab target URL that does not point into the lab. Returns the errors."""
    errors = []
    for result in TargetGuard(settings.lab_targets, settings.lab_compose_file, adapter).check_all():
        if result.allowed:
            log.info("lab target '%s' at %s is inside the lab", result.target, result.url)
        else:
            log.error("%s Runs, lab start and reset are refused until this is fixed.", result.error)
            errors.append(result.error)
    return errors


# Published in this repository (docker-compose.yml, .env.example, config.py): fine for
# a local lab on 127.0.0.1, never for anything else.
DEFAULT_LAB_TOKENS = frozenset({"lab-dev-token", "change-me-lab-token"})
DEFAULT_DB_PASSWORDS = frozenset({"change-me-locally"})


def check_default_credentials(settings: Settings) -> list[str]:
    """Log a warning for each published default credential in use. Returns the warnings."""
    warnings = []
    if settings.lab_control_token in DEFAULT_LAB_TOKENS:
        warnings.append(
            f"LAB_CONTROL_TOKEN is the published default '{settings.lab_control_token}'. "
            "It is for a local lab only; set your own value in .env."
        )
    try:
        password = make_url(settings.database_url).password
    except Exception:  # an unparsable URL is reported elsewhere, when the database is used
        password = None
    if password in DEFAULT_DB_PASSWORDS:
        warnings.append(
            f"The database password is the published default '{password}'. "
            "It is for a local lab only; set POSTGRES_PASSWORD in .env (before the database is first created)."
        )
    for message in warnings:
        log.warning("Default credential in use: %s", message)
    return warnings


def run_startup_checks(settings: Settings, adapter: LabAdapter) -> None:
    configure_logging()
    check_lab_targets(settings, adapter)
    check_default_credentials(settings)
