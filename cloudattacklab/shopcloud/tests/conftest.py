import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shop.main import create_app  # noqa: E402

LAB_TOKEN = "test-lab-token"
LAB = {"X-Lab-Token": LAB_TOKEN}


class Clock:
    """A monotonic clock the tests move by hand (for the rate-limit window)."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app(tmp_path, clock):
    return create_app(db_path=str(tmp_path / "shop.db"), lab_token=LAB_TOKEN, clock=clock)


@pytest.fixture
def client(app):
    with TestClient(app, client=("10.0.0.5", 50000)) as c:
        yield c


@pytest.fixture
def logs(capsys):
    """Return the JSON log lines written to stdout so far (and consume them)."""

    def read():
        out = capsys.readouterr().out
        return [json.loads(line) for line in out.splitlines() if line.startswith("{")]

    read()  # drop anything from start-up
    return read


def login(client, username="lab_user", password="lab-user-pass"):
    return client.post("/api/login", json={"username": username, "password": password})


def token_for(client, username="lab_user", password="lab-user-pass"):
    response = login(client, username, password)
    assert response.status_code == 200, response.text
    return response.json()["token"]
