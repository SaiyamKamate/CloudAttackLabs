"""Checks the backend runs once at startup. They only log; they never stop the backend,
so the dashboard can still show what is wrong."""
from __future__ import annotations

import logging

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


def run_startup_checks(settings: Settings, adapter: LabAdapter) -> None:
    configure_logging()
    check_lab_targets(settings, adapter)
