"""Integration test: drives the real Docker lab through the API.

It builds, starts, resets and stops lab/docker-compose.lab.yml, so it only runs
when asked for:

    pytest -m integration

Needs Docker and free port 8081. The database is still in-memory SQLite; the
real Docker adapter and the real ShopCloud HTTP client are what is under test.
"""
import httpx
import pytest

from app.config import get_settings
from app.lab_manager.router import get_adapter, get_target_clients

pytestmark = pytest.mark.integration

API = "/api/v1"


@pytest.fixture
def real_client(app):
    from fastapi.testclient import TestClient

    get_adapter.cache_clear()
    get_target_clients.cache_clear()
    adapter = get_adapter()  # the real DockerComposeAdapter, from settings
    try:
        with TestClient(app) as client:
            yield client
    finally:
        adapter.stop()  # never leave the lab running, even if an assertion failed


def test_start_check_reset_stop_the_real_lab(real_client):
    settings = get_settings()
    shopcloud = settings.lab_targets["shopcloud"]
    lab_headers = {"X-Lab-Token": settings.lab_control_token}

    lab = real_client.post(f"{API}/labs", json={"name": "integration", "target": "shopcloud"}).json()
    url = f"{API}/labs/{lab['id']}"

    # --- start, and ShopCloud is healthy ----------------------------------
    response = real_client.post(f"{url}/start")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "running"

    details = real_client.get(url).json()
    (container,) = details["containers"]
    assert container["service"] == "shopcloud"
    assert container["state"] == "running" and container["health"] == "healthy"
    assert details["target_health"]["healthy"] is True
    assert details["target_health"]["body"]["status"] == "ok"
    assert httpx.get(f"{shopcloud}/health").json()["status"] == "ok"

    # --- move ShopCloud away from its baseline ----------------------------
    changed = httpx.post(
        f"{shopcloud}/lab/rate-limit",
        json={"enabled": True, "max_attempts": 3, "window_seconds": 60},
        headers=lab_headers,
    )
    assert changed.status_code == 200 and changed.json()["enabled"] is True

    # --- reset restores the real baseline ---------------------------------
    response = real_client.post(f"{url}/reset")
    assert response.status_code == 200, response.text

    after = real_client.get(url).json()
    assert after["containers"][0]["container_id"] != container["container_id"]  # a new container
    assert after["containers"][0]["health"] == "healthy"
    assert after["target_health"]["healthy"] is True
    assert httpx.get(f"{shopcloud}/lab/rate-limit", headers=lab_headers).json()["enabled"] is False

    # --- stop ---------------------------------------------------------------
    response = real_client.post(f"{url}/stop")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "stopped"

    stopped = real_client.get(url).json()
    assert stopped["containers"][0]["state"] == "exited"
    assert stopped["target_health"]["reachable"] is False
