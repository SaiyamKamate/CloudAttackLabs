"""Target Management against the real ShopCloud app, in-process.

The backend's IntegrationClient talks to ShopCloud's create_app() through an
ASGI test client, so request signing, response signing, the handshake proof and
the event feed are tested against the real counterpart, not a fake.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import AuditLog, Lab, ScenarioRun, Target, TargetAuthorization
from app.targets.integration_client import IntegrationClient, IntegrationError, Pinned
from app.targets.router import get_client_factory
from app.targets.service import make_rechecker, pinned_of
from app.telemetry.ingest import TelemetryIngestor
from app.telemetry.integration import IntegrationFeedSource
from app.telemetry.normalize import RunContext
from app.telemetry.source import TelemetrySourceError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shopcloud"))
from shop.main import create_app as create_shop  # noqa: E402

API = "/api/v1"
KEY = "integration-key-for-tests-0123456789abcdef"
TOKEN = "control-token-for-tests"
URL = "http://127.0.0.1:8090"


@pytest.fixture
def shop(tmp_path):
    """A running ShopCloud with the integration on (what shopcloud/docker-compose.yml starts)."""
    app = create_shop(db_path=str(tmp_path / "shop.db"), lab_token=TOKEN, integration_key=KEY, instance_id="shop-1")
    return TestClient(app, client=("172.23.0.1", 40000))


@pytest.fixture
def api(client, app, shop):
    """The CloudAttackLab API, with every target request going to `shop`."""
    def factory(target, settings):
        return IntegrationClient(
            pinned_of(target), target.integration_key, recheck=make_rechecker(settings), http=shop
        )

    app.dependency_overrides[get_client_factory] = lambda: factory
    return client


def register(api, **overrides):
    body = {"name": "shop-demo", "kind": "shopcloud", "base_url": URL, "integration_key": KEY, "control_token": TOKEN}
    body.update(overrides)
    return api.post(f"{API}/targets", json=body)


def ready_target(api):
    target = register(api).json()
    assert api.post(f"{API}/targets/{target['id']}/verify").status_code == 200
    assert api.post(f"{API}/targets/{target['id']}/verify-telemetry").status_code == 200
    return target


def authorize(api, target_id, **overrides):
    body = {"scenario_ids": ["authentication_abuse"], "duration_minutes": 30, "authorized_by": "Sam", "acknowledgement": True}
    body.update(overrides)
    return api.post(f"{API}/targets/{target_id}/authorizations", json=body)


def actions(db):
    db.expire_all()
    return [row.action for row in db.scalars(select(AuditLog).order_by(AuditLog.id))]


# --- registration ---------------------------------------------------------------
def test_register_stores_the_target_and_its_external_lab(api, db, shop):
    response = register(api)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "registered"
    assert body["base_url"] == URL
    assert body["url_allowed"] is True
    lab = db.get(Lab, __import__("uuid").UUID(body["lab_id"]))
    assert (lab.environment, lab.target, lab.name) == ("external", "shop-demo", "shop-demo (external)")
    assert actions(db) == ["target.registered"]
    # Registering sends nothing to the target.
    assert shop.app.state.events.query(None, 0, 100)["events"] == []


def test_secrets_are_never_returned_or_audited(api, db):
    target = ready_target(api)
    authorize(api, target["id"])
    texts = [
        json.dumps(api.get(f"{API}/targets").json()),
        json.dumps(api.get(f"{API}/targets/{target['id']}").json()),
        json.dumps([row.detail for row in db.scalars(select(AuditLog))]),
    ]
    for text in texts:
        assert KEY not in text and TOKEN not in text
        assert "integration_key" not in text and "control_token" not in text


@pytest.mark.parametrize("url", ["http://169.254.169.254:8090", "https://example.com", "http://127.0.0.1:8000"])
def test_forbidden_urls_are_refused_and_audited(api, db, url):
    response = register(api, base_url=url)
    assert response.status_code == 422
    assert response.json()["error"] == "url_refused"
    assert db.scalars(select(Target)).first() is None
    assert actions(db) == ["target.register_refused"]


def test_builtin_target_names_are_reserved(api):
    assert register(api, name="shopcloud").json()["error"] == "target_name_taken"


def test_duplicate_names_are_refused(api):
    register(api)
    assert register(api).status_code == 409


def test_only_shopcloud_kind_is_supported(api):
    assert register(api, kind="wordpress").json()["error"] == "unsupported_kind"


def test_short_integration_key_is_rejected(api):
    assert register(api, integration_key="short").status_code == 422


# --- verification ---------------------------------------------------------------------
def test_verify_proves_identity_with_the_handshake(api, db):
    target = register(api).json()
    response = api.post(f"{API}/targets/{target['id']}/verify")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "verified"
    assert body["instance_id"] == "shop-1"
    assert "telemetry_feed_v1" in body["capabilities"]
    assert actions(db)[-1] == "target.verified"


def test_verify_fails_with_the_wrong_key(api, db):
    target = register(api, integration_key="a-different-key-that-shopcloud-does-not-know").json()
    response = api.post(f"{API}/targets/{target['id']}/verify")
    assert response.status_code == 502
    assert response.json()["error"] == "handshake_failed"
    assert "refused the integration key" in response.json()["detail"]
    assert api.get(f"{API}/targets/{target['id']}").json()["status"] == "registered"
    assert actions(db)[-1] == "target.verify_failed"


def test_verify_fails_against_an_app_without_the_integration(client, app, tmp_path):
    plain = TestClient(create_shop(db_path=str(tmp_path / "plain.db"), lab_token=TOKEN))  # the bundled lab's ShopCloud

    def factory(target, settings):
        return IntegrationClient(pinned_of(target), target.integration_key, recheck=make_rechecker(settings), http=plain)

    app.dependency_overrides[get_client_factory] = lambda: factory
    target = register(client).json()
    response = client.post(f"{API}/targets/{target['id']}/verify")
    assert response.status_code == 502
    assert "no integration API" in response.json()["detail"]


def test_an_impostor_cannot_forge_the_handshake(api, app, shop, tmp_path):
    impostor = TestClient(create_shop(db_path=str(tmp_path / "x.db"), integration_key="z" * 40, instance_id="evil"))

    def factory(target, settings):
        return IntegrationClient(pinned_of(target), target.integration_key, recheck=make_rechecker(settings), http=impostor)

    target = register(api).json()
    app.dependency_overrides[get_client_factory] = lambda: factory
    response = api.post(f"{API}/targets/{target['id']}/verify")
    assert response.status_code == 502


def test_verify_telemetry_reads_the_test_event_back(api, db):
    target = register(api).json()
    api.post(f"{API}/targets/{target['id']}/verify")
    response = api.post(f"{API}/targets/{target['id']}/verify-telemetry")
    assert response.status_code == 200, response.text
    assert response.json()["telemetry_verified_at"] is not None
    assert actions(db)[-1] == "target.telemetry_verified"


def test_telemetry_needs_a_verified_connection(api):
    target = register(api).json()
    response = api.post(f"{API}/targets/{target['id']}/verify-telemetry")
    assert response.status_code == 409
    assert response.json()["error"] == "not_verified"


# --- authorization ----------------------------------------------------------------------
def test_authorize_after_both_verifications(api, db):
    target = ready_target(api)
    response = authorize(api, target["id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "active"
    assert body["scenario_ids"] == ["authentication_abuse"]
    assert URL in body["acknowledgement"] and "authentication_abuse" in body["acknowledgement"]
    assert api.get(f"{API}/targets/{target['id']}").json()["active_authorization"]["id"] == body["id"]
    assert actions(db)[-1] == "target.authorized"


def test_registration_alone_does_not_allow_authorization(api):
    target = register(api).json()
    assert authorize(api, target["id"]).json()["error"] == "not_verified"


def test_authorization_needs_the_acknowledgement(api):
    target = ready_target(api)
    assert authorize(api, target["id"], acknowledgement=False).json()["error"] == "acknowledgement_required"


@pytest.mark.parametrize("scenarios", [["suspicious_container_activity"], ["benign_baseline"], ["no_such"]])
def test_only_application_scenarios_for_the_kind(api, scenarios):
    target = ready_target(api)
    assert authorize(api, target["id"], scenario_ids=scenarios).json()["error"] == "scenario_not_allowed"


def test_duration_is_bounded(api):
    target = ready_target(api)
    assert authorize(api, target["id"], duration_minutes=61).json()["error"] == "invalid_duration"
    assert authorize(api, target["id"], duration_minutes=0).json()["error"] == "invalid_duration"


def test_one_active_authorization_at_a_time_and_revoke(api, db):
    target = ready_target(api)
    first = authorize(api, target["id"]).json()
    assert authorize(api, target["id"]).json()["error"] == "authorization_active"
    revoked = api.post(f"{API}/targets/{target['id']}/authorizations/{first['id']}/revoke")
    assert revoked.json()["status"] == "revoked"
    assert api.get(f"{API}/targets/{target['id']}").json()["active_authorization"] is None
    assert authorize(api, target["id"]).status_code == 201
    assert "target.authorization_revoked" in actions(db)


def test_an_expired_authorization_is_not_active(api, db):
    target = ready_target(api)
    row_id = authorize(api, target["id"]).json()["id"]
    row = db.get(TargetAuthorization, __import__("uuid").UUID(row_id))
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    detail = api.get(f"{API}/targets/{target['id']}").json()
    assert detail["active_authorization"] is None
    assert detail["authorizations"][0]["status"] == "expired"


# --- the external lab is never driven through Docker -------------------------------------
def test_external_lab_is_never_driven_through_docker(api, adapter):
    target = ready_target(api)
    lab_id = target["lab_id"]
    assert api.post(f"{API}/labs/{lab_id}/start").json()["error"] == "external_lab"
    assert api.post(f"{API}/labs/{lab_id}/stop").json()["error"] == "external_lab"
    assert api.post(f"{API}/labs/{lab_id}/reset").json()["error"] == "external_lab"
    detail = api.get(f"{API}/labs/{lab_id}").json()
    assert detail["status"] == "running"  # verified
    assert detail["containers"] == []
    assert detail["target_health"]["healthy"] is True  # ShopCloud's own /health
    assert detail["target_url"] == {"url": URL, "allowed": True, "error": None}
    assert not {"start", "stop", "reset"} & set(adapter.calls)


def test_external_lab_does_not_block_the_docker_lab(api):
    ready_target(api)
    lab = api.post(f"{API}/labs", json={"name": "docker-lab", "target": "shopcloud"}).json()
    assert api.post(f"{API}/labs/{lab['id']}/start").status_code == 200


# --- the telemetry feed source -----------------------------------------------------------
def feed_source(shop, instance_id="shop-1"):
    client = IntegrationClient(Pinned(URL, "127.0.0.1", 8090), KEY, recheck=lambda pinned: None, http=shop)
    return IntegrationFeedSource(client, instance_id, page_limit=3)


def test_feed_source_collects_real_shopcloud_records(shop, db):
    started = datetime.now(timezone.utc) - timedelta(seconds=1)
    for _ in range(7):  # more than one page of 3
        shop.post("/api/login", json={"username": "lab_user", "password": "wrong"})
    lab = Lab(name="ext", target="shop-demo", environment="external")
    db.add(lab)
    db.flush()
    run = ScenarioRun(lab_id=lab.id, scenario_id="authentication_abuse", scenario_version="1", status="running", parameters={})
    db.add(run)
    db.commit()

    source = feed_source(shop)
    source.check()
    ctx = RunContext(run_id=run.id, lab_id=lab.id, scenario_id="authentication_abuse", started_at=started)
    result = TelemetryIngestor(db, source, ctx).ingest()
    assert result.stored == 7
    again = TelemetryIngestor(db, source, ctx).ingest()
    assert again.stored == 0 and again.duplicates == 7  # idempotent across polls
    from app.models import Event
    events = list(db.scalars(select(Event).where(Event.run_id == run.id)))
    assert {e.event_type for e in events} == {"authentication_failure"}
    assert all(e.raw_event_reference.startswith("integration:shopcloud:") for e in events)
    assert all(e.source == "application" and e.source_ip == "172.23.0.1" for e in events)


def test_feed_source_rejects_records_from_another_instance(shop):
    shop.post("/api/login", json={"username": "lab_user", "password": "wrong"})
    with pytest.raises(TelemetrySourceError, match="instance"):
        feed_source(shop, instance_id="someone-else").collect()


def test_feed_source_fails_closed_when_the_buffer_wrapped(shop):
    buffer = shop.app.state.events
    buffer._events = type(buffer._events)(maxlen=2)
    for _ in range(4):
        shop.post("/api/login", json={"username": "lab_user", "password": "wrong"})
    with pytest.raises(TelemetrySourceError, match="wrapped"):
        feed_source(shop).collect()


def test_feed_source_check_fails_with_the_wrong_key(shop):
    client = IntegrationClient(Pinned(URL, "127.0.0.1", 8090), "w" * 40, recheck=lambda pinned: None, http=shop)
    with pytest.raises(TelemetrySourceError, match="refused the integration key"):
        IntegrationFeedSource(client, "shop-1").check()


def test_client_refuses_redirects():
    import httpx

    def handler(request):
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = IntegrationClient(Pinned(URL, "127.0.0.1", 8090), KEY, recheck=lambda pinned: None, http=http)
    with pytest.raises(IntegrationError, match="redirect"):
        client.handshake("c" * 32, "t")
    assert client.health().healthy is False


def test_client_rechecks_the_url_before_sending():
    import httpx

    sent = []
    http = httpx.Client(transport=httpx.MockTransport(lambda request: sent.append(request) or httpx.Response(200)))
    from app.targets.url_policy import UrlRefused

    def refuse(pinned):
        raise UrlRefused("policy changed")

    client = IntegrationClient(Pinned(URL, "127.0.0.1", 8090), KEY, recheck=refuse, http=http)
    with pytest.raises(IntegrationError, match="URL refused"):
        client.events()
    assert client.health().healthy is False
    assert sent == []
