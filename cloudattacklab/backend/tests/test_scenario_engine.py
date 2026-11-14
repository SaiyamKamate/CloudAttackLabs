"""Unit tests for the Scenario Engine loader, pre-flight checks and the script runner."""
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.errors import ApiError  # noqa: E402
from app.lab_manager.target import LoginResult, TargetHealth  # noqa: E402
from app.scenario_engine import runner  # noqa: E402
from app.scenario_engine.engine import ScenarioEngine  # noqa: E402
from app.scenario_engine.preflight import run_preflight  # noqa: E402

SHIPPED = ROOT / "scenarios" / "authentication_abuse.yaml"
TARGETS = {"shopcloud"}


# --- engine ---------------------------------------------------------------
def test_loads_the_shipped_scenario():
    engine = ScenarioEngine(ROOT / "scenarios", TARGETS)
    scenario = engine.get("authentication_abuse")
    assert scenario.target == "shopcloud"
    assert [s.id for s in engine.list()] == ["authentication_abuse"]


def _write(dir_path: Path, name: str, data: dict) -> None:
    (dir_path / name).write_text(yaml.safe_dump(data))


def test_invalid_file_is_skipped_and_cannot_run(tmp_path):
    good = yaml.safe_load(SHIPPED.read_text())
    _write(tmp_path, "authentication_abuse.yaml", good)
    (tmp_path / "broken.yaml").write_text(textwrap.dedent("id: broken\nname: x\n"))  # fails validation

    engine = ScenarioEngine(tmp_path, TARGETS)
    loaded = engine.load_all()
    assert set(loaded) == {"authentication_abuse"}
    with pytest.raises(ApiError) as exc:
        engine.get("broken")
    assert exc.value.status_code == 404


def test_scenario_targeting_outside_the_lab_is_skipped(tmp_path):
    data = yaml.safe_load(SHIPPED.read_text())
    data["target"] = "production_shop"
    _write(tmp_path, "authentication_abuse.yaml", data)

    engine = ScenarioEngine(tmp_path, TARGETS)  # allowed targets from lab config
    assert engine.load_all() == {}
    with pytest.raises(ApiError):
        engine.get("authentication_abuse")


def test_missing_scenarios_dir_is_empty(tmp_path):
    assert ScenarioEngine(tmp_path / "nope", TARGETS).load_all() == {}


# --- pre-flight -----------------------------------------------------------
class StubTarget:
    def __init__(self, healthy=True, login_ok=True):
        self._healthy = healthy
        self._login_ok = login_ok
        self.logins = []

    def health(self):
        if self._healthy:
            return TargetHealth(reachable=True, healthy=True, status_code=200, body={"status": "ok"})
        return TargetHealth(reachable=False, healthy=False, error="connection refused")

    def login(self, username, password):
        self.logins.append((username, password))
        return LoginResult(ok=self._login_ok, status_code=200 if self._login_ok else 401)


ACCOUNTS = {"lab_user": "lab-user-pass"}


def _scenario():
    return ScenarioEngine(ROOT / "scenarios", TARGETS).get("authentication_abuse")


def test_preflight_passes_when_healthy_and_account_works():
    target = StubTarget()
    results = run_preflight(_scenario(), target, ACCOUNTS)
    assert all(r.passed for r in results)
    assert target.logins == [("lab_user", "lab-user-pass")]  # real password, from config


def test_preflight_fails_on_unhealthy_target():
    with pytest.raises(ApiError) as exc:
        run_preflight(_scenario(), StubTarget(healthy=False), ACCOUNTS)
    assert exc.value.status_code == 409
    assert "http_health" in exc.value.detail


def test_preflight_fails_when_test_account_cannot_log_in():
    with pytest.raises(ApiError) as exc:
        run_preflight(_scenario(), StubTarget(login_ok=False), ACCOUNTS)
    assert exc.value.status_code == 409
    assert "test_account" in exc.value.detail


def test_preflight_fails_when_account_credentials_are_unknown():
    with pytest.raises(ApiError) as exc:
        run_preflight(_scenario(), StubTarget(), {})  # no configured password
    assert exc.value.status_code == 409
    assert "test_account" in exc.value.detail


# --- runner ---------------------------------------------------------------
def _script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body))
    return path


def test_execute_script_parses_the_json_summary(tmp_path):
    script = _script(
        tmp_path,
        "good.py",
        """
        import json, sys
        params = json.loads(sys.argv[2])
        print("some noise")
        print(json.dumps({"attempts_sent": params["attempts"], "base": sys.argv[1]}))
        """,
    )
    result = runner.execute_script(script, "http://127.0.0.1:8081", {"attempts": 3}, timeout_seconds=10)
    assert result.outcome == "completed"
    assert result.summary == {"attempts_sent": 3, "base": "http://127.0.0.1:8081"}


def test_execute_script_times_out(tmp_path):
    script = _script(tmp_path, "slow.py", "import time\ntime.sleep(5)\n")
    result = runner.execute_script(script, "http://x", {}, timeout_seconds=1)
    assert result.outcome == "failed"
    assert "timed out" in result.error


def test_execute_script_reports_nonzero_exit(tmp_path):
    script = _script(tmp_path, "boom.py", "import sys\nsys.stderr.write('kaboom')\nsys.exit(2)\n")
    result = runner.execute_script(script, "http://x", {}, timeout_seconds=10)
    assert result.outcome == "failed"
    assert "code 2" in result.error and "kaboom" in result.error


def test_execute_script_without_json_summary_fails(tmp_path):
    script = _script(tmp_path, "quiet.py", "print('no json here')\n")
    result = runner.execute_script(script, "http://x", {}, timeout_seconds=10)
    assert result.outcome == "failed"
    assert "JSON summary" in result.error


def test_resolve_script_requires_an_existing_file(tmp_path):
    with pytest.raises(ApiError):
        runner.resolve_script(tmp_path, "nope.py")
