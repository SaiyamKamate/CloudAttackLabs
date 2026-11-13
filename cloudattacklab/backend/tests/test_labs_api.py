"""Unit tests for GET /health and the Labs endpoints, using the fake adapter and fake target."""
import uuid

from sqlalchemy import select

from app.models import AuditLog, Lab

API = "/api/v1"


def create_lab(client, name="lab-one"):
    response = client.post(f"{API}/labs", json={"name": name, "target": "shopcloud"})
    assert response.status_code == 201, response.text
    return response.json()


def audit_rows(db):
    db.expire_all()
    return list(db.scalars(select(AuditLog).order_by(AuditLog.id)))


# --- health ---------------------------------------------------------------
def test_health(client):
    response = client.get(f"{API}/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


# --- create / list / get --------------------------------------------------
def test_create_lab(client):
    lab = create_lab(client)
    uuid.UUID(lab["id"])
    assert lab["name"] == "lab-one"
    assert lab["target"] == "shopcloud"
    assert lab["environment"] == "docker"
    assert lab["status"] == "created"


def test_create_lab_rejects_duplicate_name(client):
    create_lab(client)
    response = client.post(f"{API}/labs", json={"name": "lab-one", "target": "shopcloud"})
    assert response.status_code == 409
    assert response.json()["error"] == "lab_name_taken"


def test_create_lab_rejects_unconfigured_target(client):
    response = client.post(f"{API}/labs", json={"name": "x", "target": "production_shop"})
    assert response.status_code == 422
    assert response.json()["error"] == "unknown_target"


def test_validation_errors_use_the_contract_error_format(client):
    response = client.post(f"{API}/labs", json={"name": ""})
    assert response.status_code == 422
    assert set(response.json()) == {"error", "detail"}
    assert response.json()["error"] == "validation_error"


def test_list_labs(client):
    create_lab(client, "a")
    create_lab(client, "b")
    response = client.get(f"{API}/labs")
    assert response.status_code == 200
    assert [lab["name"] for lab in response.json()] == ["a", "b"]


def test_get_unknown_lab_is_404(client):
    response = client.get(f"{API}/labs/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"] == "lab_not_found"


def test_get_lab_before_start_shows_no_containers_and_unreachable_target(client):
    lab = create_lab(client)
    body = client.get(f"{API}/labs/{lab['id']}").json()
    assert body["status"] == "created"
    assert body["containers"] == []
    assert body["target_health"]["reachable"] is False
    assert body["target_health"]["healthy"] is False


def test_get_lab_includes_container_status_and_target_health(client):
    lab = create_lab(client)
    client.post(f"{API}/labs/{lab['id']}/start")
    body = client.get(f"{API}/labs/{lab['id']}").json()
    assert body["status"] == "running"
    assert body["containers"] == [
        {
            "service": "shopcloud",
            "name": "cloudattacklab-lab-shopcloud-1",
            "container_id": "fake1",
            "state": "running",
            "health": "healthy",
        }
    ]
    assert body["target_health"] == {
        "reachable": True,
        "healthy": True,
        "status_code": 200,
        "body": {"status": "ok"},
        "error": None,
    }


# --- start ----------------------------------------------------------------
def test_start_lab(client, adapter, db):
    lab = create_lab(client)
    response = client.post(f"{API}/labs/{lab['id']}/start")
    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert adapter.calls == ["start"]

    (row,) = audit_rows(db)
    assert (row.action, row.object_type, row.object_id) == ("lab.start", "lab", lab["id"])
    assert row.detail["outcome"] == "success"
    assert row.detail["status_before"] == "created" and row.detail["status_after"] == "running"


def test_start_failure_sets_error_and_is_audited(client, adapter, db):
    adapter.fail_on.add("start")
    lab = create_lab(client)
    response = client.post(f"{API}/labs/{lab['id']}/start")
    assert response.status_code == 502
    assert response.json()["error"] == "lab_start_failed"
    assert db.get(Lab, uuid.UUID(lab["id"])).status == "error"

    (row,) = audit_rows(db)
    assert row.action == "lab.start" and row.detail["outcome"] == "failed"
    assert "fake start failure" in row.detail["error"]


def test_only_one_lab_can_run_at_a_time(client, adapter):
    first, second = create_lab(client, "first"), create_lab(client, "second")
    client.post(f"{API}/labs/{first['id']}/start")
    response = client.post(f"{API}/labs/{second['id']}/start")
    assert response.status_code == 409
    assert response.json()["error"] == "lab_conflict"
    assert adapter.calls == ["start"]

    # The idle lab must not report the running lab's containers as its own.
    body = client.get(f"{API}/labs/{second['id']}").json()
    assert body["containers"] == []
    assert body["target_health"]["healthy"] is False


# --- stop -----------------------------------------------------------------
def test_stop_lab(client, adapter, db):
    lab = create_lab(client)
    client.post(f"{API}/labs/{lab['id']}/start")
    response = client.post(f"{API}/labs/{lab['id']}/stop")
    assert response.status_code == 200
    assert response.json()["status"] == "stopped"
    assert adapter.calls == ["start", "stop"]
    assert [row.action for row in audit_rows(db)] == ["lab.start", "lab.stop"]

    body = client.get(f"{API}/labs/{lab['id']}").json()
    assert body["containers"][0]["state"] == "exited"
    assert body["target_health"]["reachable"] is False


def test_stop_a_lab_that_is_not_running_is_refused(client, adapter, db):
    lab = create_lab(client)
    response = client.post(f"{API}/labs/{lab['id']}/stop")
    assert response.status_code == 409
    assert response.json()["error"] == "lab_not_running"
    assert adapter.calls == []
    assert audit_rows(db) == []


def test_stopped_lab_can_be_started_again(client):
    lab = create_lab(client)
    for action in ("start", "stop", "start"):
        response = client.post(f"{API}/labs/{lab['id']}/{action}")
        assert response.status_code == 200
    assert response.json()["status"] == "running"


# --- reset ----------------------------------------------------------------
def test_reset_recreates_the_container_and_resets_the_target(client, adapter, target, db):
    lab = create_lab(client)
    client.post(f"{API}/labs/{lab['id']}/start")
    before = client.get(f"{API}/labs/{lab['id']}").json()["containers"][0]["container_id"]

    response = client.post(f"{API}/labs/{lab['id']}/reset")
    assert response.status_code == 200
    assert response.json()["status"] == "running"

    # Both halves of a real reset happened: a new container and ShopCloud's POST /lab/reset.
    assert "reset" in adapter.calls
    assert target.reset_calls == 1
    after = client.get(f"{API}/labs/{lab['id']}").json()["containers"][0]["container_id"]
    assert after != before

    rows = audit_rows(db)
    assert [row.action for row in rows] == ["lab.start", "lab.reset"]
    assert rows[-1].detail["outcome"] == "success"


def test_reset_requires_a_running_lab(client, adapter, target, db):
    lab = create_lab(client)
    response = client.post(f"{API}/labs/{lab['id']}/reset")
    assert response.status_code == 409
    assert response.json()["error"] == "lab_not_running"
    assert adapter.calls == [] and target.reset_calls == 0
    assert audit_rows(db) == []


def test_reset_fails_if_the_target_refuses_the_reset(client, target, db):
    lab = create_lab(client)
    client.post(f"{API}/labs/{lab['id']}/start")
    target.fail_reset = True
    response = client.post(f"{API}/labs/{lab['id']}/reset")
    assert response.status_code == 502
    assert response.json()["error"] == "lab_reset_failed"
    assert db.get(Lab, uuid.UUID(lab["id"])).status == "error"
    assert audit_rows(db)[-1].detail["outcome"] == "failed"


def test_lab_in_error_can_be_stopped(client, adapter):
    adapter.fail_on.add("start")
    lab = create_lab(client)
    client.post(f"{API}/labs/{lab['id']}/start")
    response = client.post(f"{API}/labs/{lab['id']}/stop")
    assert response.status_code == 200
    assert response.json()["status"] == "stopped"


def test_actions_on_unknown_lab_are_404(client, adapter):
    for action in ("start", "stop", "reset"):
        response = client.post(f"{API}/labs/{uuid.uuid4()}/{action}")
        assert response.status_code == 404
    assert adapter.calls == []
