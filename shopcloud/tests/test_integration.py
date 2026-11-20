"""The CloudAttackLab integration API (shop/integration.py)."""
import json
import secrets
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from shop.integration import handshake_proof, request_message, response_message, sign
from shop.main import create_app

from conftest import LAB_TOKEN, login

KEY = "k" * 40


class Wall:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def wall():
    return Wall()


@pytest.fixture
def shop(tmp_path, clock, wall):
    app = create_app(
        db_path=str(tmp_path / "shop.db"), lab_token=LAB_TOKEN, clock=clock,
        integration_key=KEY, instance_id="shop-test-1", wall_clock=wall,
    )
    with TestClient(app, client=("10.0.0.5", 50000)) as c:
        yield c


def signed(client, method, path, *, body=None, params=None, key=KEY, ts=None, nonce=None):
    data = json.dumps(body).encode() if body is not None else b""
    path_query = path + ("?" + urlencode(params) if params else "")
    ts = str(int(ts if ts is not None else time.time()))
    nonce = nonce or secrets.token_hex(16)
    headers = {
        "X-CAL-Timestamp": ts,
        "X-CAL-Nonce": nonce,
        "X-CAL-Signature": sign(key, request_message(method, path_query, ts, nonce, data)),
    }
    if data:
        headers["Content-Type"] = "application/json"
    response = client.request(method, path_query, content=data, headers=headers)
    return response, nonce


def assert_response_signed(response, nonce):
    expected = sign(KEY, response_message(nonce, response.content))
    assert response.headers["X-CAL-Response-Signature"] == expected


# --- off by default --------------------------------------------------------
def test_integration_is_off_without_a_key(client):
    # The bundled lab (no INTEGRATION_KEY) behaves exactly as before.
    assert client.post("/integration/v1/handshake").status_code == 404
    assert client.get("/integration/v1/events").status_code == 404


def test_short_key_is_refused(tmp_path):
    with pytest.raises(ValueError):
        create_app(db_path=str(tmp_path / "s.db"), integration_key="too-short")


# --- handshake ---------------------------------------------------------------
def test_handshake_returns_identity_and_proof(shop):
    challenge = secrets.token_hex(16)
    response, nonce = signed(shop, "POST", "/integration/v1/handshake", body={"challenge": challenge, "target_id": "t1"})
    assert response.status_code == 200, response.text
    assert_response_signed(response, nonce)
    body = response.json()
    assert body["kind"] == "shopcloud"
    assert body["instance_id"] == "shop-test-1"
    assert body["telemetry_schema"] == "shopcloud.security_event/1"
    assert "telemetry_feed_v1" in body["capabilities"]
    assert body["proof"] == handshake_proof(KEY, challenge, "t1", "shop-test-1")


@pytest.mark.parametrize(
    "change",
    [
        {"key": "x" * 40},                  # wrong key
        {"ts": time.time() - 300},          # stale timestamp
        {"nonce": "short"},                 # malformed nonce
    ],
)
def test_handshake_rejects_bad_signatures(shop, change):
    response, _ = signed(shop, "POST", "/integration/v1/handshake", body={"challenge": "c" * 32, "target_id": "t"}, **change)
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_unsigned_request_is_refused(shop):
    assert shop.post("/integration/v1/handshake", json={"challenge": "c" * 32, "target_id": "t"}).status_code == 401


def test_replayed_nonce_is_refused(shop):
    nonce = secrets.token_hex(16)
    first, _ = signed(shop, "POST", "/integration/v1/handshake", body={"challenge": "c" * 32, "target_id": "t"}, nonce=nonce)
    again, _ = signed(shop, "POST", "/integration/v1/handshake", body={"challenge": "c" * 32, "target_id": "t"}, nonce=nonce)
    assert first.status_code == 200
    assert again.status_code == 401


def test_tampered_body_is_refused(shop):
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    good = json.dumps({"challenge": "c" * 32, "target_id": "t"}).encode()
    headers = {
        "X-CAL-Timestamp": ts, "X-CAL-Nonce": nonce, "Content-Type": "application/json",
        "X-CAL-Signature": sign(KEY, request_message("POST", "/integration/v1/handshake", ts, nonce, good)),
    }
    tampered = json.dumps({"challenge": "d" * 32, "target_id": "t"}).encode()
    assert shop.post("/integration/v1/handshake", content=tampered, headers=headers).status_code == 401


def test_handshake_validates_its_body(shop):
    response, _ = signed(shop, "POST", "/integration/v1/handshake", body={"challenge": "short", "target_id": "t"})
    assert response.status_code == 422


# --- the event feed ------------------------------------------------------------
def test_feed_returns_the_logged_security_events(shop, logs):
    login(shop, password="wrong-password")
    login(shop, password="wrong-password")
    printed = logs()
    response, nonce = signed(shop, "GET", "/integration/v1/events", params={"after_seq": 0, "limit": 100})
    assert response.status_code == 200
    assert_response_signed(response, nonce)
    body = response.json()
    failed = [e for e in body["events"] if e["event"] == "login_failed"]
    assert len(failed) == 2
    # The feed carries exactly the record written to stdout, plus the integration fields.
    first = failed[0]
    assert {k: first[k] for k in printed[0]} == printed[0]
    assert first["schema"] == "shopcloud.security_event/1"
    assert first["instance_id"] == "shop-test-1"
    assert body["next_seq"] == body["events"][-1]["seq"]
    assert body["truncated"] is False


def test_feed_pages_with_after_seq_and_limit(shop):
    for _ in range(5):
        login(shop, password="wrong-password")
    page1, _ = signed(shop, "GET", "/integration/v1/events", params={"after_seq": 0, "limit": 2})
    page2, _ = signed(shop, "GET", "/integration/v1/events", params={"after_seq": page1.json()["next_seq"], "limit": 2})
    seqs1 = [e["seq"] for e in page1.json()["events"]]
    seqs2 = [e["seq"] for e in page2.json()["events"]]
    assert len(seqs1) == len(seqs2) == 2
    assert seqs2[0] > seqs1[-1]


def test_feed_filters_by_since(shop):
    login(shop, password="wrong-password")
    response, _ = signed(shop, "GET", "/integration/v1/events", params={"since": "2999-01-01T00:00:00Z"})
    assert response.json()["events"] == []


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 5000}, {"after_seq": -1}, {"since": "yesterday"}])
def test_feed_rejects_bad_paging(shop, params):
    response, _ = signed(shop, "GET", "/integration/v1/events", params=params)
    assert response.status_code == 422


def test_integration_requests_are_not_logged(shop, logs):
    signed(shop, "POST", "/integration/v1/handshake", body={"challenge": "c" * 32, "target_id": "t"})
    signed(shop, "GET", "/integration/v1/events")
    shop.get("/integration/v1/events")  # unsigned, refused
    assert logs() == []
    response, _ = signed(shop, "GET", "/integration/v1/events")
    assert response.json()["events"] == []


def test_truncated_when_the_buffer_wrapped(shop):
    buffer = shop.app.state.events
    buffer._events = type(buffer._events)(maxlen=3)
    for _ in range(5):
        login(shop, password="wrong-password")
    response, _ = signed(shop, "GET", "/integration/v1/events", params={"after_seq": 0})
    assert response.json()["truncated"] is True


# --- telemetry test event ----------------------------------------------------------
def test_telemetry_test_emits_an_event_with_the_nonce(shop, logs):
    test_nonce = secrets.token_hex(16)
    response, nonce = signed(shop, "POST", "/integration/v1/telemetry/test", body={"nonce": test_nonce})
    assert response.status_code == 200
    assert_response_signed(response, nonce)
    printed = logs()
    assert printed[-1]["event"] == "integration_test"
    assert printed[-1]["test_nonce"] == test_nonce
    feed, _ = signed(shop, "GET", "/integration/v1/events")
    assert feed.json()["events"][-1]["test_nonce"] == test_nonce


# --- persistence -----------------------------------------------------------------
def test_reset_on_start_can_be_turned_off(tmp_path):
    path = str(tmp_path / "keep.db")
    app = create_app(db_path=path, reset_on_start=False)
    with app.state.db.transaction() as conn:
        conn.execute("UPDATE products SET stock = 1 WHERE id = 1")
    again = create_app(db_path=path, reset_on_start=False)
    with again.state.db.connect() as conn:
        assert conn.execute("SELECT stock FROM products WHERE id = 1").fetchone()[0] == 1
    fresh = create_app(db_path=path)  # the default restores the baseline
    with fresh.state.db.connect() as conn:
        assert conn.execute("SELECT stock FROM products WHERE id = 1").fetchone()[0] == 25
