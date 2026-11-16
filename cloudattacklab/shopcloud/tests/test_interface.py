"""docs/shopcloud-interface.md, section by section."""
from fastapi.testclient import TestClient

from conftest import LAB, login, token_for


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# --- section 3: application endpoints -----------------------------------------


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_both_test_accounts_log_in_with_their_roles(client):
    for username, password, role in (
        ("lab_user", "lab-user-pass", "user"),
        ("lab_restricted", "lab-restricted-pass", "restricted"),
    ):
        body = login(client, username, password).json()
        assert body["username"] == username and body["role"] == role and body["token"]


def test_wrong_password_unknown_user_and_bad_bodies_get_401(client):
    for payload in (
        {"username": "lab_user", "password": "wrong"},
        {"username": "nobody", "password": "lab-user-pass"},
        {"username": "lab_user"},
        {"username": "lab_user", "password": 123},
        {},
    ):
        response = client.post("/api/login", json=payload)
        assert response.status_code == 401, payload
        assert response.json() == {"error": "invalid_credentials"}
    response = client.post("/api/login", content=b"not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 401


def test_orders_200_for_user_403_for_restricted_401_without_a_good_token(client):
    response = client.get("/api/orders", headers=bearer(token_for(client)))
    assert response.status_code == 200
    orders = response.json()["orders"]
    assert [o["id"] for o in orders] == [1, 2, 3]
    assert orders[0] == {
        "id": 1, "item": "Mechanical Keyboard", "quantity": 1, "total": 49.0,
        "status": "delivered", "created_at": "2026-10-02T09:15:00Z",
    }

    restricted = token_for(client, "lab_restricted", "lab-restricted-pass")
    response = client.get("/api/orders", headers=bearer(restricted))
    assert response.status_code == 403 and response.json() == {"error": "forbidden"}

    for headers in ({}, bearer("not-a-token"), {"Authorization": "Bearer "}, {"Authorization": "Basic abc"}):
        response = client.get("/api/orders", headers=headers)
        assert response.status_code == 401 and response.json() == {"error": "not_authenticated"}


# --- section 4: lab control ---------------------------------------------------


def test_lab_endpoints_need_the_lab_token(client):
    for method, path in (("GET", "/lab/rate-limit"), ("POST", "/lab/rate-limit"), ("POST", "/lab/reset")):
        for headers in ({}, {"X-Lab-Token": "wrong"}):
            response = client.request(method, path, headers=headers, json={"enabled": True})
            assert response.status_code == 403, (method, path, headers)
    # Nothing was changed by those.
    assert client.get("/lab/rate-limit", headers=LAB).json()["enabled"] is False


def test_rate_limit_is_off_at_baseline_and_can_be_set(client):
    assert client.get("/lab/rate-limit", headers=LAB).json() == {
        "enabled": False, "max_attempts": 5, "window_seconds": 60,
    }
    settings = {"enabled": True, "max_attempts": 5, "window_seconds": 60}
    response = client.post("/lab/rate-limit", json=settings, headers=LAB)
    assert response.status_code == 200 and response.json() == settings
    assert client.get("/lab/rate-limit", headers=LAB).json() == settings


def test_invalid_rate_limit_settings_get_422_and_change_nothing(client):
    for body in (
        {"enabled": "yes"},
        {"max_attempts": 3},
        {"enabled": True, "max_attempts": 0},
        {"enabled": True, "max_attempts": 2.5},
        {"enabled": True, "max_attempts": True},
        {"enabled": True, "window_seconds": 0},
        {"enabled": True, "window_seconds": 100000},
        [1, 2],
    ):
        response = client.post("/lab/rate-limit", json=body, headers=LAB)
        assert response.status_code == 422, body
    assert client.get("/lab/rate-limit", headers=LAB).json()["enabled"] is False


def test_rate_limit_blocks_after_max_attempts_per_source_ip_until_the_window_passes(app, client, clock):
    client.post("/lab/rate-limit", json={"enabled": True, "max_attempts": 3, "window_seconds": 60}, headers=LAB)
    codes = [login(client, password="wrong").status_code for _ in range(5)]
    assert codes == [401, 401, 401, 429, 429]
    # Even the right password is blocked while the window is full.
    response = login(client)
    assert response.status_code == 429 and response.json() == {"error": "too_many_attempts"}

    # Another source IP has its own budget.
    with TestClient(app, client=("10.0.0.9", 50001)) as other:
        assert login(other).status_code == 200

    clock.now += 61
    assert login(client).status_code == 200


def test_rate_limit_off_never_blocks(client):
    assert {login(client, password="wrong").status_code for _ in range(30)} == {401}


def test_reset_turns_the_limit_off_clears_tokens_and_answers_reset(client):
    token = token_for(client)
    client.post("/lab/rate-limit", json={"enabled": True, "max_attempts": 1, "window_seconds": 60}, headers=LAB)
    response = client.post("/lab/reset", headers=LAB)
    assert response.status_code == 200 and response.json() == {"status": "reset"}
    assert client.get("/lab/rate-limit", headers=LAB).json()["enabled"] is False
    assert client.get("/api/orders", headers=bearer(token)).status_code == 401
    assert login(client).status_code == 200  # the attempt counter is gone too


# --- section 5: log format ----------------------------------------------------

FIELDS = ["timestamp", "service", "level", "event", "request_id", "source_ip", "method", "path", "status", "username", "message"]


def test_each_interface_event_is_logged_with_every_field(client, logs):
    token = token_for(client)
    restricted = token_for(client, "lab_restricted", "lab-restricted-pass")
    login(client, password="wrong")
    client.get("/api/orders", headers=bearer(token))
    client.get("/api/orders", headers=bearer(restricted))
    client.get("/api/orders")
    client.post("/lab/reset", headers={"X-Lab-Token": "wrong"})
    client.post("/lab/rate-limit", json={"enabled": True, "max_attempts": 1, "window_seconds": 60}, headers=LAB)
    login(client, password="wrong")
    login(client, password="wrong")
    client.post("/lab/reset", headers=LAB)

    records = logs()
    assert [(r["event"], r["status"], r["username"]) for r in records] == [
        ("login_success", 200, "lab_user"),
        ("login_success", 200, "lab_restricted"),
        ("login_failed", 401, "lab_user"),
        ("access_granted", 200, "lab_user"),
        ("access_denied", 403, "lab_restricted"),
        ("access_denied", 401, None),
        ("access_denied", 403, None),
        ("rate_limit_changed", 200, None),
        ("login_failed", 401, "lab_user"),
        ("login_rate_limited", 429, "lab_user"),
        ("lab_reset", 200, None),
    ]
    for r in records:
        assert list(r) == FIELDS
        assert r["service"] == "shopcloud"
        assert r["timestamp"].endswith("Z") and "T" in r["timestamp"]
        assert r["level"] == ("info" if r["status"] == 200 else "warning")
        assert r["source_ip"] == "10.0.0.5"
        assert r["message"]
    assert len({r["request_id"] for r in records}) == len(records)
    assert records[2]["method"] == "POST" and records[2]["path"] == "/api/login"


def test_logs_never_contain_passwords_or_tokens(client, logs):
    token = token_for(client)
    client.get("/api/orders", headers=bearer(token))
    client.get("/api/orders", headers=bearer("leaked-looking-token"))
    login(client, password="a-wrong-secret")
    client.post("/lab/reset", headers={"X-Lab-Token": "wrong-lab-secret"})
    client.post("/lab/reset", headers=LAB)
    text = "\n".join(str(r) for r in logs())
    for secret in ("lab-user-pass", "a-wrong-secret", token, "leaked-looking-token", "wrong-lab-secret", LAB["X-Lab-Token"]):
        assert secret not in text


def test_health_is_not_logged(client, logs):
    client.get("/health")
    assert logs() == []
