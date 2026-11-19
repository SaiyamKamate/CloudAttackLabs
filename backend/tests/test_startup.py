"""Audit bug 8: the backend warns at startup when a published default credential is in use."""
import dataclasses
import logging

from app.config import get_settings
from app.startup import check_default_credentials

DB = "postgresql+psycopg://cloudattacklab:{}@127.0.0.1:5432/cloudattacklab"


def _settings(token, password):
    return dataclasses.replace(get_settings(), lab_control_token=token, database_url=DB.format(password))


def test_default_token_and_database_password_are_warned_about(caplog):
    with caplog.at_level(logging.WARNING, logger="cloudattacklab.startup"):
        warnings = check_default_credentials(_settings("lab-dev-token", "change-me-locally"))
    assert len(warnings) == 2
    assert "LAB_CONTROL_TOKEN is the published default 'lab-dev-token'" in caplog.text
    assert "database password is the published default 'change-me-locally'" in caplog.text
    assert "local lab only" in caplog.text


def test_the_env_example_placeholder_token_is_warned_about_too():
    assert len(check_default_credentials(_settings("change-me-lab-token", "my-own-db-pass"))) == 1


def test_own_values_give_no_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="cloudattacklab.startup"):
        assert check_default_credentials(_settings("a-token-of-my-own", "my-own-db-pass")) == []
    assert caplog.text == ""
