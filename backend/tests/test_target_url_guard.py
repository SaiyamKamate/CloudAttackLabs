"""Audit bug 1: the lab target URL (SHOPCLOUD_URL) must point into the lab.

The audit's case: a ShopCloud-like server started outside the lab compose
project, on 127.0.0.1:9081, with SHOPCLOUD_URL pointing at it. The run used to
go ahead and send 20 logins there. Now it is refused and nothing reaches it.
"""
import dataclasses
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app.config import REPO_ROOT, get_settings
from app.lab_manager.adapter import LabAdapterError
from app.lab_manager.container import ContainerInfo
from app.lab_manager.router import get_adapter, get_target_clients
from app.lab_manager.target import HttpTargetClient
from app.lab_manager.target_guard import GuardedTargetClient, TargetGuard, check_target_url
from app.models import Lab, LabStatus
from app.startup import check_lab_targets

API = "/api/v1"
COMPOSE = REPO_ROOT / "lab" / "docker-compose.lab.yml"
OUTSIDE_PORT = 9081


class _Recorder(BaseHTTPRequestHandler):
    requests: list[str] = []

    def _answer(self):
        type(self).requests.append(f"{self.command} {self.path}")
        body = b'{"status": "ok", "service": "shopcloud"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer

    def log_message(self, *args):  # keep test output quiet
        pass


@pytest.fixture
def outside_server():
    """A ShopCloud look-alike outside the lab, on 127.0.0.1:9081, that records every request."""
    _Recorder.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", OUTSIDE_PORT), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield _Recorder.requests
    server.shutdown()
    server.server_close()


class InspectOnlyAdapter:
    """Answers `docker inspect` for a few made-up containers."""

    def __init__(self, containers: dict[str, ContainerInfo]):
        self.containers = containers

    def inspect(self, name):
        if name not in self.containers:
            raise LabAdapterError(f"docker inspect returned no container for '{name}'")
        return self.containers[name]


def _container(project, service):
    labels = {"com.docker.compose.project": project, "com.docker.compose.service": service}
    return ContainerInfo(id="abc123", name=f"{project}-{service}-1", running=True, labels=labels)


# --- the check itself -------------------------------------------------------
@pytest.mark.parametrize("url", ["http://127.0.0.1:8081", "http://localhost:8081", "http://[::1]:8081"])
def test_loopback_on_the_lab_published_port_is_allowed(url):
    assert check_target_url("shopcloud", url, compose_file=COMPOSE).allowed


@pytest.mark.parametrize(
    "url, reason",
    [
        ("http://127.0.0.1:9081", "port 9081 is not the port"),          # the audit's case
        ("http://127.0.0.1", "port 80 is not the port"),
        ("http://192.0.2.10:8081", "not a loopback address"),
        ("http://10.0.0.5:8081", "not a loopback address"),
        ("ftp://127.0.0.1:8081", "only http"),
        ("http://user:pw@127.0.0.1:8081", "user name or password"),
        ("http://example.com", "not a container of the 'cloudattacklab-lab' project"),
    ],
)
def test_anything_outside_the_lab_is_refused(url, reason):
    result = check_target_url("shopcloud", url, compose_file=COMPOSE, adapter=InspectOnlyAdapter({}))
    assert not result.allowed
    assert reason in result.error
    assert url in result.error


def test_a_container_of_the_lab_project_is_allowed_and_any_other_is_refused():
    adapter = InspectOnlyAdapter(
        {
            "cloudattacklab-lab-shopcloud-1": _container("cloudattacklab-lab", "shopcloud"),
            "other-shopcloud-1": _container("other", "shopcloud"),
            "cloudattacklab-lab-test_container-1": _container("cloudattacklab-lab", "test_container"),
        }
    )
    ok = check_target_url("shopcloud", "http://cloudattacklab-lab-shopcloud-1:8000", compose_file=COMPOSE, adapter=adapter)
    assert ok.allowed
    other = check_target_url("shopcloud", "http://other-shopcloud-1:8000", compose_file=COMPOSE, adapter=adapter)
    assert not other.allowed and "belongs to Compose project 'other'" in other.error
    wrong = check_target_url(
        "shopcloud", "http://cloudattacklab-lab-test_container-1:8000", compose_file=COMPOSE, adapter=adapter
    )
    assert not wrong.allowed and "runs service 'test_container'" in wrong.error


def test_startup_logs_an_error_for_an_outside_url(caplog):
    settings = dataclasses.replace(get_settings(), lab_targets={"shopcloud": f"http://127.0.0.1:{OUTSIDE_PORT}"})
    with caplog.at_level(logging.ERROR, logger="cloudattacklab.startup"):
        errors = check_lab_targets(settings, InspectOnlyAdapter({}))
    assert len(errors) == 1
    assert f"127.0.0.1:{OUTSIDE_PORT} is refused" in caplog.text


# --- the audit's case, end to end -------------------------------------------
@pytest.fixture
def outside_client(app, adapter, outside_server):
    """The backend with SHOPCLOUD_URL=http://127.0.0.1:9081 and the real (guarded) HTTP target client."""
    settings = dataclasses.replace(get_settings(), lab_targets={"shopcloud": f"http://127.0.0.1:{OUTSIDE_PORT}"})
    guard = TargetGuard(settings.lab_targets, settings.lab_compose_file, adapter)
    real = GuardedTargetClient(HttpTargetClient(settings.lab_targets["shopcloud"], "lab-dev-token"), "shopcloud", guard)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_adapter] = lambda: adapter
    app.dependency_overrides[get_target_clients] = lambda: {"shopcloud": real}
    with TestClient(app) as client:
        yield client


def _running_lab(client, db):
    lab_id = client.post(f"{API}/labs", json={"name": "outside", "target": "shopcloud"}).json()["id"]
    # Pretend the lab is up, so only the URL check stands between the run and the outside server.
    lab = db.get(Lab, __import__("uuid").UUID(lab_id))
    lab.status = LabStatus.running.value
    db.commit()
    return lab_id


def test_run_against_an_outside_server_is_refused_and_sends_nothing(outside_client, db, outside_server):
    lab_id = _running_lab(outside_client, db)

    response = outside_client.post(f"{API}/labs/{lab_id}/runs", json={"scenario_id": "authentication_abuse"})

    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "target_url_refused"
    assert "http://127.0.0.1:9081" in body["detail"] and "port 9081" in body["detail"]
    assert outside_server == []  # not a single request reached the outside server


def test_lab_page_shows_the_refused_url_without_contacting_it(outside_client, db, outside_server):
    lab_id = _running_lab(outside_client, db)

    detail = outside_client.get(f"{API}/labs/{lab_id}").json()

    assert detail["target_url"]["allowed"] is False
    assert "port 9081" in detail["target_url"]["error"]
    assert detail["target_health"]["healthy"] is False
    assert outside_server == []


def test_reset_and_responses_never_reach_an_outside_server(outside_client, db, outside_server):
    lab_id = _running_lab(outside_client, db)
    response = outside_client.post(f"{API}/labs/{lab_id}/reset")
    assert response.status_code == 409 and response.json()["error"] == "target_url_refused"

    # The guarded client itself refuses every call that would carry the lab token.
    real = outside_client.app.dependency_overrides[get_target_clients]()["shopcloud"]
    with pytest.raises(Exception, match="refused"):
        real.set_rate_limit(True, 5, 60)
    with pytest.raises(Exception, match="refused"):
        real.reset()
    assert outside_server == []
