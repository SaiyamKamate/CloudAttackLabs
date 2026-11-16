"""Unit tests for the suspicious_container_activity scenario, its telemetry, the
suspicious_container_process rule and the stop_container response.

No Docker: a fake adapter stands in for the lab. It uses the real
check_test_container guard, and produces Docker event and Falco records shaped
like the ones recorded on this machine (fixtures/docker_events_test_container.jsonl,
fixtures/falco_marker_alert.json).
"""
import json
import subprocess
import sys
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.db import get_session  # noqa: E402
from app.lab_manager import docker_adapter  # noqa: E402
from app.lab_manager.adapter import ContainerStatus, LabAdapterError  # noqa: E402
from app.lab_manager.container import (  # noqa: E402
    MARKER_COMMAND,
    ContainerInfo,
    ExecResult,
    TargetRefused,
    check_test_container,
)
from app.lab_manager.docker_adapter import DockerComposeAdapter  # noqa: E402
from app.lab_manager.router import get_adapter, get_target_clients  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Alert, Base, Event, Incident  # noqa: E402
from app.scenario_engine import service as service_module  # noqa: E402
from app.scenario_engine.engine import ScenarioEngine  # noqa: E402
from app.scenario_engine.runner import ContainerHandle, execute_container_script  # noqa: E402
from app.schemas.scenario import load_scenario  # noqa: E402
from app.telemetry.container import make_docker_event_normalizer, make_falco_normalizer  # noqa: E402
from app.telemetry.normalize import RunContext  # noqa: E402

from conftest import FakeAdapter, FakeTarget  # noqa: E402

API = "/api/v1"
TERMINAL = {"completed", "failed", "cleaned_up"}
FIXTURES = Path(__file__).parent / "fixtures"
LAB_PROJECT = "cloudattacklab-lab"
ROLE = {"cloudattacklab.role": "test-target"}


def compose_labels(project, service, **extra):
    return {"com.docker.compose.project": project, "com.docker.compose.service": service, **extra}


# --- a fake lab with a test container ---------------------------------------
class FakeContainerLab(FakeAdapter):
    """The fake lab plus a test container, a ShopCloud container and a platform container."""

    project = LAB_PROJECT

    def __init__(self, *, falco=False):
        super().__init__()
        self.falco = falco
        self.generation_test = 1
        self.execs = []          # services the marker ran in
        self.event_lines = []    # docker events (JSON lines)
        self.falco_lines = ["Falco initialized with configuration files:"]  # non-JSON startup line
        self.recreated = []
        self.stop_calls = []
        self.containers = {}
        self._add(self._test_id(), "cloudattacklab-lab-test_container-1", compose_labels(LAB_PROJECT, "test_container", **ROLE))
        self._add("5" * 64, "cloudattacklab-lab-shopcloud-1", compose_labels(LAB_PROJECT, "shopcloud"))
        self._add("b" * 64, "cloudattacklab-backend-1", compose_labels("cloudattacklab", "backend"))
        if falco:
            self._add("f" * 64, "cloudattacklab-lab-falco-1", compose_labels(LAB_PROJECT, "falco"))

    def _test_id(self):
        return f"{self.generation_test:x}" * 64

    def _add(self, cid, name, labels, running=True):
        self.containers[cid] = ContainerInfo(id=cid, name=name, running=running, labels=labels)

    def status(self):
        base = super().status()
        if self.generation == 0:
            return base
        extra = []
        for info in self.containers.values():
            if info.project == LAB_PROJECT and info.service in ("test_container", "falco"):
                health = "healthy" if info.service == "falco" else None
                extra.append(ContainerStatus(
                    service=info.service, name=info.name, container_id=info.id[:12],
                    state="running" if info.running else "exited", health=health,
                ))
        return base + extra

    def test_container(self):
        return next(i for i in self.containers.values() if i.service == "test_container")

    # The real guard decides, as in the Docker adapter.
    def exec_marker(self, service):
        info = next(i for i in self.containers.values() if i.service == service)
        check_test_container(info, self.project)
        self.execs.append(service)
        ns = time.time_ns()
        command = " ".join(MARKER_COMMAND)
        attrs = {**info.labels, "name": info.name, "image": "alpine:3.20"}
        ns -= 1_000_000  # the events happened before exec returned
        for offset, action in ((0, f"exec_create: {command}"), (400_000, f"exec_start: {command}"), (900_000, "exec_die")):
            self.event_lines.append(json.dumps({
                "Type": "container", "Action": action,
                "Actor": {"ID": info.id, "Attributes": {**attrs, "execID": "e" * 64}},
                "scope": "local", "time": (ns + offset) // 10**9, "timeNano": ns + offset,
            }))
        if self.falco:
            alert = json.loads((FIXTURES / "falco_marker_alert.json").read_text())
            alert["output_fields"]["container.id"] = info.id[:12]
            alert["output_fields"]["container.name"] = info.name
            alert["output_fields"]["evt.time.iso8601"] = ns + 500_000
            alert["time"] = datetime.fromtimestamp((ns + 500_000) / 1e9, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            self.falco_lines.append(json.dumps(alert))
            # An alert about another container must be ignored.
            other = json.loads(json.dumps(alert))
            other["output_fields"]["container.id"] = "5" * 12
            self.falco_lines.append(json.dumps(other))
        return ExecResult(command=MARKER_COMMAND, exit_code=0, output="uid=0(root) gid=0(root)")

    def container_events(self, since, until):
        lo, hi = int(since.timestamp() * 1e9), int(until.timestamp() * 1e9)
        return [line for line in self.event_lines if lo <= json.loads(line)["timeNano"] <= hi]

    def logs(self, service, since=None):
        if service == "falco":
            return "\n".join(self.falco_lines)
        return super().logs(service, since)

    def inspect(self, container):
        for cid, info in self.containers.items():
            if cid == container or cid.startswith(container) or info.name == container:
                return info
        raise LabAdapterError(f"No such container: {container}")

    def stop_test_container(self, container):
        info = self.inspect(container)
        check_test_container(info, self.project)  # the real guard
        self.stop_calls.append(info.id)
        self.containers[info.id] = replace(info, running=False)
        return info

    def recreate_service(self, service):
        self.recreated.append(service)
        old = self.test_container()
        del self.containers[old.id]
        self.generation_test += 1
        self._add(self._test_id(), old.name, dict(old.labels))


# --- fixtures ------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clear_active_labs():
    service_module._active_labs.clear()
    yield
    service_module._active_labs.clear()


@pytest.fixture
def file_engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'container.db'}", connect_args={"check_same_thread": False}, poolclass=NullPool
    )
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA busy_timeout=5000")
    yield engine
    engine.dispose()


def make_client(file_engine, lab, **settings_over):
    app = create_app()

    def _session():
        with Session(file_engine, expire_on_commit=False) as session:
            yield session

    settings = replace(
        get_settings(),
        **{
            "telemetry_collect_interval_seconds": 0.05,
            "loki_settle_interval_seconds": 0.05,
            "loki_settle_timeout_seconds": 1,
            **settings_over,
        },
    )
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_adapter] = lambda: lab
    app.dependency_overrides[get_target_clients] = lambda: {"shopcloud": FakeTarget(lab)}
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def running_lab(client):
    lab = client.post(f"{API}/labs", json={"name": "container-lab", "target": "shopcloud"}).json()
    assert client.post(f"{API}/labs/{lab['id']}/start").status_code == 200
    return lab


def run_to_end(client, lab_id, scenario_id="suspicious_container_activity", timeout=15.0):
    response = client.post(f"{API}/labs/{lab_id}/runs", json={"scenario_id": scenario_id})
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"{API}/runs/{run_id}").json()
        if body["status"] in TERMINAL:
            return body
        time.sleep(0.05)
    raise AssertionError(f"run did not settle: {body}")


# --- the scenario file ------------------------------------------------------
def test_scenario_file_validates_as_a_container_scenario():
    scenario = load_scenario(ROOT / "scenarios" / "suspicious_container_activity.yaml", {"shopcloud", "test_container"})
    assert scenario.type == "container"
    assert scenario.target == "test_container"
    assert scenario.requires == ["container_target"]
    assert [p.check for p in scenario.prerequisites] == ["container_running"]
    assert [(e.source, e.event_type) for e in scenario.expected_telemetry] == [("docker", "suspicious_process")]
    assert scenario.detection.rule == "suspicious_container_process" and scenario.detection.threshold == 1
    assert scenario.response.action == "stop_container" and scenario.response.requires_approval is True
    assert scenario.cleanup.steps == ["recreate_test_container"]


def test_a_container_scenario_must_target_a_container_target(tmp_path):
    text = (ROOT / "scenarios" / "suspicious_container_activity.yaml").read_text().replace(
        "target: test_container", "target: shopcloud"
    )
    (tmp_path / "suspicious_container_activity.yaml").write_text(text)
    engine = ScenarioEngine(tmp_path, {"shopcloud", "test_container"}, {"test_container"})
    assert engine.list() == []  # skipped as invalid: a container scenario aimed at ShopCloud

    # And the real files load: two application scenarios and this one.
    real = ScenarioEngine(ROOT / "scenarios", {"shopcloud", "test_container"}, {"test_container"})
    assert {s.id: s.type for s in real.list()} == {
        "authentication_abuse": "application",
        "suspicious_container_activity": "container",
        "unauthorized_resource_access": "application",
    }


# --- the fixed command ------------------------------------------------------
def test_the_marker_command_is_fixed_in_code_and_harmless():
    assert MARKER_COMMAND == ("sh", "-c", "id; : cloudattacklab-scenario-marker")
    # The Falco rule matches the same marker.
    assert "cloudattacklab-scenario-marker" in (ROOT / "lab" / "falco" / "cloudattacklab_rules.yaml").read_text()


def test_scenario_parameters_cannot_change_the_command(tmp_path):
    """Even a scenario whose parameters name another command runs only MARKER_COMMAND:
    the script gets no parameters and the handle has no way to pass a command."""
    lab = FakeContainerLab()
    lab.start()
    handle = ContainerHandle(lab, "test_container", "test_container")
    script = ROOT / "scenarios" / "scripts" / "suspicious_container_activity.py"
    result = execute_container_script(script, handle, timeout_seconds=5)
    assert result.outcome == "completed"
    assert result.summary["command"] == " ".join(MARKER_COMMAND)
    assert lab.execs == ["test_container"]
    with pytest.raises(TypeError):
        handle.run_marker("rm -rf /")  # no command can be passed
    assert not hasattr(handle, "adapter")


def test_the_api_request_cannot_carry_a_command(file_engine):
    lab = FakeContainerLab()
    with make_client(file_engine, lab) as client:
        lab_row = running_lab(client)
        response = client.post(
            f"{API}/labs/{lab_row['id']}/runs",
            json={"scenario_id": "suspicious_container_activity", "command": "rm -rf /"},
        )
    assert response.status_code == 422  # extra fields are refused
    assert lab.execs == []


def test_docker_adapter_exec_runs_only_the_fixed_command(tmp_path, monkeypatch):
    compose = tmp_path / "docker-compose.lab.yml"
    compose.write_text(f"name: {LAB_PROJECT}\nservices: {{}}\n")
    test_id = "1" * 64
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[3:5] == [str(compose), "ps"] or command[-4:-2] == ["--format", "json"]:
            out = json.dumps({"Service": "test_container", "Name": "t", "ID": test_id[:12], "State": "running"})
            return subprocess.CompletedProcess(command, 0, stdout=out, stderr="")
        if command[1] == "inspect":
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps([inspect_json(test_id, "t", compose_labels(LAB_PROJECT, "test_container", **ROLE))]), stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="uid=0(root)\n", stderr="")

    monkeypatch.setattr(docker_adapter.subprocess, "run", fake_run)
    result = DockerComposeAdapter(compose, lab_control_token="t").exec_marker("test_container")
    exec_call = next(c for c in calls if "exec" in c)
    assert exec_call == ["docker", "compose", "-f", str(compose), "exec", "-T", "test_container", *MARKER_COMMAND]
    assert result.exit_code == 0 and result.output == "uid=0(root)"


# --- telemetry normalization ------------------------------------------------
def ctx_for(container_id="2d6831595b7e", started=None):
    return RunContext(
        run_id=uuid.uuid4(), lab_id=uuid.uuid4(), scenario_id="suspicious_container_activity",
        started_at=started or datetime(2026, 11, 16, 12, 18, 0, tzinfo=timezone.utc), container_id=container_id,
    )


def test_recorded_docker_events_normalize_to_one_suspicious_process():
    lines = (FIXTURES / "docker_events_test_container.jsonl").read_text().splitlines()
    normalize = make_docker_event_normalizer("test_container")
    events = [normalize(json.loads(line), ctx_for(), "docker") for line in lines]
    kept = [e for e in events if e is not None]
    # create, exec_create and exec_die are skipped; start and exec_start are kept.
    assert [e.event_type for e in kept] == ["container_started", "suspicious_process"]
    exec_event = kept[1]
    assert exec_event.source == "docker"
    assert exec_event.container_id == "2d6831595b7e"
    assert exec_event.resource == "test_container"
    assert exec_event.raw_event_reference == "docker:test_container:exec_start-1794831543602722263"
    assert "sh -c id; : cloudattacklab-scenario-marker" in exec_event.message


def test_recorded_falco_alert_normalizes_with_its_rule_name():
    record = json.loads((FIXTURES / "falco_marker_alert.json").read_text())
    event = make_falco_normalizer("test_container")(record, ctx_for(), "docker")
    assert event.source == "falco"
    assert event.event_type == "suspicious_process"
    assert event.rule_id == "CloudAttackLab marker process in test container"
    assert event.container_id == "2d6831595b7e"
    assert event.severity == "medium"  # Falco priority Warning
    assert event.raw_event_reference.startswith("docker:falco:1794831543658816106-")
    assert event.message.startswith("Marker process started in test container")
    assert event.timestamp == datetime(2026, 11, 16, 12, 19, 3, 658816, tzinfo=timezone.utc)

    # An alert about another container is not this run's evidence.
    assert make_falco_normalizer("test_container")(record, ctx_for(container_id="5555555555ab"), "docker") is None


# --- runs through the API ---------------------------------------------------
def test_docker_event_alone_raises_the_alert_with_falco_off(file_engine):
    lab = FakeContainerLab(falco=False)
    with make_client(file_engine, lab, falco_enabled=False) as client:
        lab_row = running_lab(client)
        run = run_to_end(client, lab_row["id"])
        assert run["status"] == "completed", run
        assert run["summary"]["command"] == " ".join(MARKER_COMMAND)
        alerts = client.get(f"{API}/alerts", params={"run_id": run["id"]}).json()
        events = client.get(f"{API}/events", params={"run_id": run["id"]}).json()
        assert alerts, {"run": run, "events": events}
        detail = client.get(f"{API}/alerts/{alerts[0]['id']}").json()
        incidents = [i for i in client.get(f"{API}/incidents").json() if i["run_id"] == run["id"]]
    assert [a["rule_id"] for a in alerts] == ["suspicious_container_process"]
    assert [(e["source"], e["event_type"]) for e in detail["events"]] == [("docker", "suspicious_process")]
    assert detail["events"][0]["container_id"] == lab.test_container().id[:12]
    assert len(incidents) == 1
    assert lab.execs == ["test_container"]


def test_docker_and_falco_events_merge_into_one_alert_and_one_incident(file_engine):
    lab = FakeContainerLab(falco=True)
    with make_client(file_engine, lab, falco_enabled=True) as client:
        lab_row = running_lab(client)
        run = run_to_end(client, lab_row["id"])
        assert run["status"] == "completed", run
        alerts = client.get(f"{API}/alerts", params={"run_id": run["id"]}).json()
        detail = client.get(f"{API}/alerts/{alerts[0]['id']}").json()
        incidents = [i for i in client.get(f"{API}/incidents").json() if i["run_id"] == run["id"]]
        timeline = client.get(f"{API}/incidents/{incidents[0]['id']}").json()["timeline"]
    assert len(alerts) == 1
    assert sorted(e["source"] for e in detail["events"]) == ["docker", "falco"]
    falco = next(e for e in detail["events"] if e["source"] == "falco")
    assert falco["rule_id"] == "CloudAttackLab marker process in test container"
    assert len({e["container_id"] for e in detail["events"]}) == 1
    assert len(incidents) == 1
    # The timeline names each event's source, and Falco's rule.
    descriptions = [t["description"] for t in timeline if t["type"] == "event"]
    assert any(d.startswith("docker event: suspicious_process") for d in descriptions)
    assert any(d.startswith("falco event: suspicious_process") and "Falco rule: CloudAttackLab marker" in d for d in descriptions)


def test_falco_enabled_but_not_running_refuses_the_run(file_engine):
    lab = FakeContainerLab(falco=False)  # no Falco container
    with make_client(file_engine, lab, falco_enabled=True) as client:
        lab_row = running_lab(client)
        response = client.post(f"{API}/labs/{lab_row['id']}/runs", json={"scenario_id": "suspicious_container_activity"})
    assert response.status_code == 409
    assert "FALCO_ENABLED is true but the Falco container is missing" in response.json()["detail"]
    assert lab.execs == []


def test_a_stopped_test_container_fails_the_prerequisite(file_engine):
    lab = FakeContainerLab()
    with make_client(file_engine, lab) as client:
        lab_row = running_lab(client)
        lab.containers[lab.test_container().id] = replace(lab.test_container(), running=False)
        response = client.post(f"{API}/labs/{lab_row['id']}/runs", json={"scenario_id": "suspicious_container_activity"})
    assert response.status_code == 409
    assert "pre-flight check 'container_running' failed: the 'test_container' container is exited" in response.json()["detail"]


# --- stop_container -----------------------------------------------------------
def inspect_json(cid, name, labels, running=True):
    return {"Id": cid, "Name": "/" + name, "State": {"Running": running}, "Config": {"Labels": labels}}


@pytest.mark.parametrize(
    "name,labels",
    [
        ("cloudattacklab-lab-shopcloud-1", compose_labels(LAB_PROJECT, "shopcloud")),
        ("cloudattacklab-lab-falco-1", compose_labels(LAB_PROJECT, "falco")),
        ("cloudattacklab-backend-1", compose_labels("cloudattacklab", "backend")),
        ("cloudattacklab-postgres-1", compose_labels("cloudattacklab", "postgres")),
        ("cloudattacklab-frontend-1", compose_labels("cloudattacklab", "frontend")),
        # The right label, but not in the lab's project: still refused.
        ("someone-elses-container", compose_labels("other-project", "app", **ROLE)),
    ],
)
def test_docker_adapter_refuses_to_stop_anything_but_the_test_container(tmp_path, monkeypatch, name, labels):
    compose = tmp_path / "docker-compose.lab.yml"
    compose.write_text(f"name: {LAB_PROJECT}\nservices: {{}}\n")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1] == "inspect":
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps([inspect_json("9" * 64, name, labels)]), stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(docker_adapter.subprocess, "run", fake_run)
    with pytest.raises(TargetRefused):
        DockerComposeAdapter(compose, lab_control_token="t").stop_test_container(name)
    assert [c[1] for c in calls] == ["inspect"]  # never `docker stop`


def test_docker_adapter_stops_the_test_container(tmp_path, monkeypatch):
    compose = tmp_path / "docker-compose.lab.yml"
    compose.write_text(f"name: {LAB_PROJECT}\nservices: {{}}\n")
    calls = []
    labels = compose_labels(LAB_PROJECT, "test_container", **ROLE)

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1] == "inspect":
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps([inspect_json("7" * 64, "t", labels)]), stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(docker_adapter.subprocess, "run", fake_run)
    DockerComposeAdapter(compose, lab_control_token="t").stop_test_container("7" * 12)
    assert calls[-1] == ["docker", "stop", "--time", "5", "7" * 64]


def finished_container_incident(client, lab_row):
    run = run_to_end(client, lab_row["id"])
    assert run["status"] == "completed", run
    incident = next(i for i in client.get(f"{API}/incidents").json() if i["run_id"] == run["id"])
    return run, incident


def test_stop_container_request_refuses_an_incident_pointing_at_shopcloud(file_engine):
    lab = FakeContainerLab()
    with make_client(file_engine, lab) as client:
        lab_row = running_lab(client)
        run, incident = finished_container_incident(client, lab_row)
        # Make the incident's evidence name ShopCloud's container instead.
        with Session(file_engine) as db:
            for event in db.scalars(select(Event).where(Event.run_id == uuid.UUID(run["id"]))):
                event.container_id = "5" * 12
            db.commit()
        response = client.post(f"{API}/incidents/{incident['id']}/responses")
    assert response.status_code == 409
    assert response.json()["error"] == "target_refused"
    assert "does not have the label cloudattacklab.role=test-target" in response.json()["detail"]
    assert lab.stop_calls == []


def test_stop_container_is_approved_verified_and_undone_by_cleanup(file_engine):
    lab = FakeContainerLab()
    with make_client(file_engine, lab) as client:
        lab_row = running_lab(client)
        run, incident = finished_container_incident(client, lab_row)
        original = lab.test_container()

        requested = client.post(f"{API}/incidents/{incident['id']}/responses").json()
        assert requested["status"] == "pending_approval"
        assert requested["action"] == "stop_container"
        assert requested["parameters"] == {"target": "test_container", "container_id": original.id[:12]}
        assert lab.stop_calls == []  # nothing happens before approval

        approved = client.post(f"{API}/responses/{requested['id']}/approve").json()
        assert approved["status"] == "verified", approved
        assert approved["verification_result"]["check"] == "container_not_running"
        assert approved["verification_result"]["passed"] is True
        assert approved["verification_result"]["received"] == {"running": False}
        assert lab.stop_calls == [original.id]

        cleaned = client.post(f"{API}/runs/{run['id']}/cleanup").json()
        assert cleaned["status"] == "cleaned_up"
        assert lab.recreated == ["test_container"]
        fresh = lab.test_container()
        assert fresh.running and fresh.id != original.id  # back at baseline: a new, running container
        assert client.get(f"{API}/responses/{requested['id']}").json()["status"] == "reverted"


def test_lab_reset_also_brings_the_test_container_back(file_engine):
    lab = FakeContainerLab()
    with make_client(file_engine, lab) as client:
        lab_row = running_lab(client)
        assert client.post(f"{API}/labs/{lab_row['id']}/reset").status_code == 200
    assert "reset" in lab.calls  # the Docker adapter's reset recreates every container, the test one included


def test_lab_detail_shows_falco(file_engine):
    lab = FakeContainerLab(falco=True)
    with make_client(file_engine, lab, falco_enabled=True) as client:
        lab_row = running_lab(client)
        body = client.get(f"{API}/labs/{lab_row['id']}").json()
    assert body["falco"] == {"enabled": True, "state": "running", "health": "healthy", "healthy": True}
    assert {c["service"] for c in body["containers"]} == {"shopcloud", "test_container", "falco"}

    off = FakeContainerLab(falco=False)
    with make_client(file_engine, off, falco_enabled=False) as client:
        lab_row = client.post(f"{API}/labs", json={"name": "off-lab", "target": "shopcloud"}).json()
        client.post(f"{API}/labs/{lab_row['id']}/start")
        body = client.get(f"{API}/labs/{lab_row['id']}").json()
    assert body["falco"] == {"enabled": False, "state": None, "health": None, "healthy": False}


def test_docker_adapter_reset_recreates_and_falco_profile_follows_the_setting(tmp_path, monkeypatch):
    compose = tmp_path / "docker-compose.lab.yml"
    compose.write_text(f"name: {LAB_PROJECT}\nservices: {{}}\n")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs["env"]["COMPOSE_PROFILES"]))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(docker_adapter.subprocess, "run", fake_run)
    DockerComposeAdapter(compose, lab_control_token="t", falco_enabled=False).start()
    DockerComposeAdapter(compose, lab_control_token="t", falco_enabled=True).start()
    DockerComposeAdapter(compose, lab_control_token="t", falco_enabled=True).reset()
    DockerComposeAdapter(compose, lab_control_token="t", falco_enabled=False).stop()
    assert [profile for _, profile in calls] == ["", "falco", "falco", "falco"]  # stop always includes Falco
    assert "--force-recreate" in calls[2][0]
