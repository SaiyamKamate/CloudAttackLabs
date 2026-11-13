"""Unit tests for the Docker adapter's command building and output parsing (no Docker needed)."""
import subprocess

import pytest

from app.lab_manager import docker_adapter
from app.lab_manager.adapter import LabAdapterError
from app.lab_manager.docker_adapter import DockerComposeAdapter, _parse_ps


@pytest.fixture
def compose_file(tmp_path):
    path = tmp_path / "docker-compose.lab.yml"
    path.write_text("services: {}\n")
    return path


@pytest.fixture
def runs(monkeypatch):
    """Replace subprocess.run and record each call."""
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(docker_adapter.subprocess, "run", fake_run)
    return calls


def test_every_command_is_scoped_to_the_lab_compose_file(compose_file, runs):
    adapter = DockerComposeAdapter(compose_file, lab_control_token="t0ken")
    adapter.start()
    adapter.stop()
    adapter.reset()
    adapter.status()

    for command, kwargs in runs:
        assert command[:4] == ["docker", "compose", "-f", str(compose_file)]
        assert kwargs["env"]["LAB_CONTROL_TOKEN"] == "t0ken"
    assert [command[4] for command, _ in runs] == ["up", "stop", "up", "ps"]
    assert "--wait" in runs[0][0]
    assert "--force-recreate" in runs[2][0] and "--force-recreate" not in runs[0][0]


def test_failed_command_raises_with_the_docker_error(compose_file, monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="line1\nCannot connect to the Docker daemon")

    monkeypatch.setattr(docker_adapter.subprocess, "run", fake_run)
    with pytest.raises(LabAdapterError, match="Cannot connect to the Docker daemon"):
        DockerComposeAdapter(compose_file, lab_control_token="t").start()


def test_missing_docker_binary_is_a_clear_error(compose_file):
    adapter = DockerComposeAdapter(compose_file, docker_bin="no-such-docker-binary", lab_control_token="t")
    with pytest.raises(LabAdapterError, match="not found"):
        adapter.status()


def test_missing_compose_file_is_a_clear_error(tmp_path):
    adapter = DockerComposeAdapter(tmp_path / "nope.yml", lab_control_token="t")
    with pytest.raises(LabAdapterError, match="compose file not found"):
        adapter.start()


def test_parse_ps_handles_both_output_styles():
    one = '{"Service": "shopcloud", "Name": "n", "ID": "abc", "State": "running", "Health": "healthy"}'
    assert _parse_ps("") == []
    assert _parse_ps(one)[0]["Service"] == "shopcloud"          # one object per line
    assert len(_parse_ps(one + "\n" + one)) == 2
    assert len(_parse_ps(f"[{one}, {one}]")) == 2               # one JSON array (older compose)


def test_status_maps_compose_fields(compose_file, monkeypatch):
    line = '{"Service": "shopcloud", "Name": "lab-shopcloud-1", "ID": "abc123", "State": "exited", "Health": ""}'
    monkeypatch.setattr(
        docker_adapter.subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(command, 0, line, "")
    )
    (container,) = DockerComposeAdapter(compose_file, lab_control_token="t").status()
    assert (container.service, container.container_id, container.state, container.health) == (
        "shopcloud", "abc123", "exited", None,
    )
