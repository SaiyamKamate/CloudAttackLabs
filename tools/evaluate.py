#!/usr/bin/env python3
"""Repeatable evaluation of CloudAttackLab (project guide section 14).

Drives the platform through its HTTP API only, the same calls the dashboard
makes (`/api/v1/...`), and writes raw results to
`evaluation/results/<date>/` as CSV and JSON.

Outside the API it does only what an operator would do around it:

- `docker compose up -d backend` with TELEMETRY_SOURCE / FALCO_ENABLED set in
  the process environment (never in .env), to switch the telemetry source.
  The backend reports the active source and Falco on GET /labs/{id}, and the
  script checks that before measuring.
- `docker compose --profile monitoring up -d loki alloy grafana` for the Loki phase.
- `docker stats` to sample CPU and memory, and `docker version` / `git`
  for the environment record.
- benign traffic (tools/benign_traffic.py): ShopCloud requests on its
  published port and ordinary `docker exec` commands in the test container.

Run it where the `docker` CLI is (on this project: inside WSL), from the
project root, with the platform built:

    python3 tools/evaluate.py                       # the full plan
    python3 tools/evaluate.py --mode benign_baseline   # only the benign baseline (about 12 min)
    python3 tools/evaluate.py --docker-runs 1 --loki-runs 1 --no-benign-baseline   # quick check
    python3 tools/evaluate.py --summarize evaluation/results/2026-11-17

Standard library only. Charts: tools/evaluation_charts.py (needs matplotlib).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benign_traffic import SCENARIO_MARKER, BenignTraffic, NearMissPlan  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
API = "http://127.0.0.1:8000/api/v1"
SCENARIOS = ("authentication_abuse", "unauthorized_resource_access", "suspicious_container_activity")
RESPONSE_OF = {"authentication_abuse": "temporary_rate_limit", "suspicious_container_activity": "stop_container"}
ACTIVE_RUN = ("pending", "running")
LAB_NAME = "evaluation"
BASELINE = "benign_baseline"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.isoformat()


def parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def log(message: str) -> None:
    print(f"[{utcnow().strftime('%H:%M:%S')}] {message}", flush=True)


# --- the platform API ---------------------------------------------------------
class ApiError(Exception):
    def __init__(self, status: Optional[int], body: Any) -> None:
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


class Api:
    def __init__(self, base: str = API) -> None:
        self.base = base

    def call(self, method: str, path: str, body: Any = None, timeout: float = 300) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = raw.decode(errors="replace")
            raise ApiError(error.code, parsed) from None
        except (urllib.error.URLError, OSError) as error:
            raise ApiError(None, str(error)) from None

    def get(self, path: str, **kwargs) -> Any:
        return self.call("GET", path, **kwargs)

    def post(self, path: str, body: Any = None, **kwargs) -> Any:
        return self.call("POST", path, body, **kwargs)

    def wait_healthy(self, timeout: float = 180) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.get("/health", timeout=5)
                return
            except ApiError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(1)


# --- shell helpers (outside the API) -------------------------------------------
def sh(args: list[str], env: Optional[dict] = None, timeout: float = 600, check: bool = True) -> str:
    done = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout, env=env)
    if check and done.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed ({done.returncode}): {done.stderr.strip()[-800:]}")
    return done.stdout.strip()


def compose_env(telemetry_source: Optional[str], falco: Optional[bool]) -> dict:
    """The process environment for `docker compose`. Shell variables win over
    .env, so .env itself is never changed. None = take the value from .env."""
    env = dict(os.environ)
    for key in ("TELEMETRY_SOURCE", "FALCO_ENABLED"):
        env.pop(key, None)
    if telemetry_source is not None:
        env["TELEMETRY_SOURCE"] = telemetry_source
    if falco is not None:
        env["FALCO_ENABLED"] = "true" if falco else "false"
    return env


def configure_backend(api: Api, telemetry_source: Optional[str], falco: Optional[bool]) -> None:
    log(f"backend: TELEMETRY_SOURCE={telemetry_source or '(.env)'} FALCO_ENABLED={falco if falco is not None else '(.env)'}")
    sh(["docker", "compose", "up", "-d", "--build", "postgres", "backend", "frontend"], env=compose_env(telemetry_source, falco))
    api.wait_healthy()


MONITORING_SERVICES = ["loki", "alloy", "grafana"]


def monitoring(up: bool) -> None:
    args = ["docker", "compose", "--profile", "monitoring"]
    sh(args + (["up", "-d", *MONITORING_SERVICES] if up else ["stop", *MONITORING_SERVICES]))


# --- environment record ------------------------------------------------------
def _try(args: list[str]) -> Optional[str]:
    try:
        return sh(args, timeout=30)
    except Exception as exc:  # noqa: BLE001 - best effort, recorded as unknown
        return f"unavailable: {exc}"[:300]


def record_environment(api: Api, lab: dict, rules_dir: Path) -> dict:
    meminfo = Path("/proc/meminfo").read_text() if Path("/proc/meminfo").exists() else ""
    mem_kb = int(re.search(r"MemTotal:\s+(\d+)", meminfo).group(1)) if "MemTotal" in meminfo else None
    cpuinfo = Path("/proc/cpuinfo").read_text() if Path("/proc/cpuinfo").exists() else ""
    cpu_model = re.search(r"model name\s*:\s*(.+)", cpuinfo)
    os_release = {}
    if Path("/etc/os-release").exists():
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                os_release[key] = value.strip('"')
    scenarios = {}
    for scenario_id in SCENARIOS:
        definition = api.get(f"/scenarios/{scenario_id}")
        scenarios[scenario_id] = {
            "version": definition.get("version"),
            "detection": definition.get("detection"),
            "response": (definition.get("response") or {}).get("action"),
            "expected_telemetry": definition.get("expected_telemetry"),
            "execution_parameters": (definition.get("execution") or {}).get("parameters"),
        }
    rules = {}
    for path in sorted(rules_dir.glob("*.yaml")):
        text = path.read_text()
        rule = {}
        for key in ("id", "event_type", "severity", "default_threshold", "default_window_seconds"):
            match = re.search(rf"^{key}:\s*(.+)$", text, re.M)
            rule[key] = match.group(1).strip() if match else None
        rules[path.name] = rule
    commit = _try(["git", "rev-parse", "HEAD"])
    # filemode/autocrlf off: git inside WSL otherwise reports every file on a Windows drive as changed.
    dirty = _try(["git", "-c", "core.filemode=false", "-c", "core.autocrlf=true", "status", "--porcelain",
                  "--", ".", ":!evaluation"])
    return {
        "recorded_at": iso(utcnow()),
        "git": {"commit": commit, "branch": _try(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
                "uncommitted_changes": bool(dirty) and not str(dirty).startswith("unavailable")},
        "os": {
            "system": platform.system(), "kernel": platform.release(), "machine": platform.machine(),
            "distribution": os_release.get("PRETTY_NAME"),
            "windows_host": _try(["cmd.exe", "/c", "ver"]),
            "python": platform.python_version(),
        },
        "docker": {
            "server": _try(["docker", "version", "--format", "{{.Server.Version}}"]),
            "client": _try(["docker", "version", "--format", "{{.Client.Version}}"]),
            "compose": _try(["docker", "compose", "version", "--short"]),
        },
        "cpu": {"model": cpu_model.group(1).strip() if cpu_model else platform.processor(),
                "logical_cpus_visible": os.cpu_count()},
        "memory": {"total_mib_visible": round(mem_kb / 1024) if mem_kb else None,
                   "note": "As seen by the Linux VM that runs Docker (WSL 2), not the Windows host total."},
        "scenarios": scenarios,
        "rules": rules,
        "telemetry_source": lab.get("telemetry"),
        "falco": lab.get("falco"),
        "loki_settle": {"interval_seconds": os.environ.get("LOKI_SETTLE_INTERVAL_SECONDS", "2 (default)"),
                        "timeout_seconds": os.environ.get("LOKI_SETTLE_TIMEOUT_SECONDS", "20 (default)")},
    }


# --- docker stats -------------------------------------------------------------
_SIZE = {"b": 1 / 1048576, "kib": 1 / 1024, "kb": 1 / 1024, "mib": 1, "mb": 1, "gib": 1024, "gb": 1024}


def _mib(text: str) -> Optional[float]:
    match = re.match(r"\s*([\d.]+)\s*([a-zA-Z]+)", text)
    if not match:
        return None
    return float(match.group(1)) * _SIZE.get(match.group(2).lower(), 1)


class StatsSampler:
    """Samples `docker stats --no-stream` for every cloudattacklab container until stopped."""

    def __init__(self, label: dict) -> None:
        self.label = label
        self.samples: list[dict] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _sample(self) -> None:
        try:
            out = sh(["docker", "stats", "--no-stream", "--format", "{{json .}}"], timeout=30)
        except Exception:  # noqa: BLE001 - a missed sample is just missing
            return
        now = iso(utcnow())
        for line in out.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            name = row.get("Name", "")
            if not name.startswith("cloudattacklab"):
                continue
            usage = (row.get("MemUsage") or "").split("/")[0]
            self.samples.append({
                **self.label, "time": now, "container": name,
                "cpu_percent": float((row.get("CPUPerc") or "0%").rstrip("%") or 0),
                "mem_mib": round(_mib(usage) or 0, 2),
            })

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._sample()

    def __enter__(self) -> "StatsSampler":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=60)


# --- lab ------------------------------------------------------------------------
def find_or_create_lab(api: Api) -> dict:
    """The lab that holds the physical lab now (only one can), else one named `evaluation`."""
    labs = api.get("/labs")
    for lab in labs:
        if lab["status"] in ("starting", "running", "degraded"):
            return lab
    for lab in labs:
        if lab["name"] == LAB_NAME:
            return lab
    return api.post("/labs", {"name": LAB_NAME, "target": "shopcloud"})


def lab_ready(detail: dict, falco: bool) -> bool:
    ok = detail["status"] == "running" and detail["target_health"]["healthy"]
    return ok and (not falco or detail["falco"]["healthy"])


def wait_lab_ready(api: Api, lab_id: str, falco: bool, timeout: float = 300) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        detail = api.get(f"/labs/{lab_id}")
        if lab_ready(detail, falco):
            return detail
        if time.monotonic() > deadline:
            raise RuntimeError(f"lab not ready after {timeout}s: {detail['status']} {detail.get('status_detail')}")
        time.sleep(0.5)


def lab_operation(api: Api, lab_id: str, op: str, falco: bool) -> dict:
    """POST start/reset and wait for a healthy target (and Falco when on). Returns the timing."""
    started = time.monotonic()
    row = {"operation": op, "requested_at": iso(utcnow()), "ok": False, "error": None}
    try:
        api.post(f"/labs/{lab_id}/{op}", timeout=600)
        row["api_returned_seconds"] = round(time.monotonic() - started, 3)
        wait_lab_ready(api, lab_id, falco)
        row["ready_seconds"] = round(time.monotonic() - started, 3)
        row["ok"] = True
    except (ApiError, RuntimeError) as exc:
        row["error"] = str(exc)[:500]
    return row


def stop_lab(api: Api, lab_id: str) -> None:
    try:
        api.post(f"/labs/{lab_id}/stop", timeout=300)
    except ApiError as exc:
        log(f"lab stop: {exc}")


# --- one scenario run --------------------------------------------------------------
def wait_run(api: Api, run_id: str, timeout: float = 300) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        run = api.get(f"/runs/{run_id}")
        if run["status"] not in ACTIVE_RUN:
            return run
        if time.monotonic() > deadline:
            return run
        time.sleep(0.25)


def alert_details(api: Api, run_id: str) -> list[dict]:
    return [api.get(f"/alerts/{alert['id']}") for alert in api.get(f"/alerts?run_id={run_id}")]


def benign_event(event: dict) -> bool:
    """An event that came from the benign generator, not from a scenario script.

    Benign typos use a wrong password; in the overlap session they use only
    `lab_restricted` (authentication_abuse uses lab_user), and benign
    container commands never carry the scenario marker.
    """
    if event["event_type"] == "authentication_failure" and event.get("username") == "lab_restricted":
        return True
    if event["source"] == "docker" and event["event_type"] == "suspicious_process":
        return SCENARIO_MARKER not in (event.get("message") or "")
    return False


def execute_run(api: Api, lab_id: str, scenario_id: str, label: dict, *, falco: bool,
                respond: bool, stats: list) -> dict:
    record: dict[str, Any] = {**label, "scenario_id": scenario_id}
    record["reset"] = lab_operation(api, lab_id, "reset", falco)
    if not record["reset"]["ok"]:
        record["status"] = "not_started"
        record["error"] = "lab reset failed: " + str(record["reset"]["error"])
        return record

    requested = utcnow()
    with StatsSampler({**label, "scenario_id": scenario_id}) as sampler:
        try:
            run = api.post(f"/labs/{lab_id}/runs", {"scenario_id": scenario_id})
        except ApiError as exc:
            record["status"] = "refused"
            record["error"] = str(exc)[:500]
            return record
        record["run_id"] = run["id"]
        run = wait_run(api, run["id"])
    stats.extend({**sample, "run_id": record["run_id"]} for sample in sampler.samples)
    record["stats_samples"] = len(sampler.samples)
    record["wall_seconds"] = round((utcnow() - requested).total_seconds(), 3)
    record.update(status=run["status"], error=run["error"], scenario_version=run["scenario_version"],
                  started_at=run["started_at"], finished_at=run["finished_at"])

    report = api.get(f"/runs/{run['id']}/report")
    record["report"] = report
    alerts = alert_details(api, run["id"])
    record["alerts"] = alerts
    incidents = [api.get(f"/incidents/{i['id']}") for i in api.get(f"/incidents?run_id={run['id']}")]
    record["incidents"] = [{k: v for k, v in i.items() if k != "timeline"} | {"timeline_entries": len(i["timeline"])}
                           for i in incidents]

    if respond and scenario_id in RESPONSE_OF and incidents:
        record["response"] = respond_and_verify(api, incidents[0]["id"])

    # Cleanup (as the dashboard's Cleanup button), then read the response again.
    try:
        after = api.post(f"/runs/{run['id']}/cleanup", timeout=300)
        record["cleanup"] = {"ok": True, "run_status": after["status"]}
    except ApiError as exc:
        record["cleanup"] = {"ok": False, "error": str(exc)[:300]}
    if record.get("response", {}).get("id"):
        record["response"]["status_after_cleanup"] = api.get(f"/responses/{record['response']['id']}")["status"]
    for incident in incidents:
        try:
            api.post(f"/incidents/{incident['id']}/close")
        except ApiError:
            pass
    return record


def respond_and_verify(api: Api, incident_id: str) -> dict:
    out: dict[str, Any] = {"id": None}
    started = time.monotonic()
    try:
        response = api.post(f"/incidents/{incident_id}/responses")
        out.update(id=response["id"], action=response["action"], requested_status=response["status"])
        response = api.post(f"/responses/{response['id']}/approve", timeout=300)
        out.update(status=response["status"], executed=response["executed_at"] is not None,
                   verified=response["status"] == "verified",
                   approve_to_final_seconds=round(time.monotonic() - started, 3),
                   verification_result=response["verification_result"])
    except ApiError as exc:
        out["error"] = str(exc)[:500]
        out.setdefault("executed", False)
        out.setdefault("verified", False)
    return out


# --- phases ---------------------------------------------------------------------------
class Evaluation:
    def __init__(self, args: argparse.Namespace, out: Path) -> None:
        self.args = args
        self.out = out
        self.api = Api(args.api)
        self.runs: list[dict] = []
        self.stats: list[dict] = []
        self.lab_ops: list[dict] = []
        self.correlation: list[dict] = []
        self.benign: dict[str, Any] = {}
        self.lab: Optional[dict] = None

    def save(self) -> None:
        write_json(self.out / "runs.json", self.runs)
        write_json(self.out / "correlation.json", self.correlation)
        if self.benign:
            write_json(self.out / "benign_baseline.json", self.benign)
        write_csv(self.out / "runs.csv", [run_row(r) for r in self.runs])
        write_csv(self.out / "docker_stats.csv", self.stats)
        write_csv(self.out / "lab_setup.csv", self.lab_ops)
        write_csv(self.out / "correlation.csv", [run_check_row(c) for c in self.correlation if "checks" in c])

    def setup_phase(self, name: str, source: str, falco: bool) -> None:
        if self.lab:
            stop_lab(self.api, self.lab["id"])
        if source == "loki":
            log("starting the monitoring stack (Loki, Alloy, Grafana)")
            monitoring(True)
        configure_backend(self.api, source, falco)
        self.lab = self.lab or find_or_create_lab(self.api)  # the same lab for every phase
        detail = self.api.get(f"/labs/{self.lab['id']}")
        if detail["telemetry"]["source"] != source or detail["falco"]["enabled"] != falco:
            raise RuntimeError(f"backend reports {detail['telemetry']} / falco {detail['falco']}, wanted {source} / {falco}")
        if not (self.out / "environment.json").exists():
            write_json(self.out / "environment.json", record_environment(self.api, detail, ROOT / "rules"))
        write_json(self.out / f"environment_{name}.json", {"telemetry": detail["telemetry"], "falco": detail["falco"]})
        # Lab setup time: from a stopped lab, start request -> healthy target (and Falco when on).
        for trial in range(self.args.setup_trials):
            stop_lab(self.api, self.lab["id"])
            row = lab_operation(self.api, self.lab["id"], "start", falco)
            row.update(phase=name, telemetry_source=source, falco=falco, trial=trial + 1)
            self.lab_ops.append(row)
            log(f"lab start {trial + 1}/{self.args.setup_trials}: {row.get('ready_seconds')}s ok={row['ok']}")
            if not row["ok"]:
                raise RuntimeError(f"lab start failed: {row['error']}")

    def scenario_runs(self, name: str, source: str, falco: bool, count: int) -> None:
        for index in range(count):
            for scenario_id in self.args.scenarios:
                label = {"phase": name, "telemetry_source": source, "falco": falco, "index": index + 1}
                record = execute_run(self.api, self.lab["id"], scenario_id, label, falco=falco,
                                     respond=True, stats=self.stats)
                self.runs.append(record)
                self.lab_ops.append({**record["reset"], "phase": name, "telemetry_source": source,
                                     "falco": falco, "trial": f"before {scenario_id} #{index + 1}"})
                row = run_row(record)
                log(f"{name} {scenario_id} #{index + 1}: {row['status']} alert={row['expected_alert']} "
                    f"latency={row['detection_latency_s']} response={row['response_status']}")
                self.save()

    def correlation_session(self, rounds: int) -> None:
        """The three scenarios back to back while benign traffic runs, then check the incidents."""
        traffic = BenignTraffic(seed=self.args.seed, typo_users=("lab_restricted",),
                                typo_gap_seconds=20, container_gap_seconds=20)
        traffic.start()
        log("correlation session: benign traffic started")
        try:
            for round_no in range(rounds):
                for scenario_id in self.args.scenarios:
                    label = {"phase": "correlation", "telemetry_source": "docker", "falco": False,
                             "index": round_no + 1}
                    record = execute_run(self.api, self.lab["id"], scenario_id, label, falco=False,
                                         respond=False, stats=self.stats)
                    self.correlation.append(record)
                    log(f"correlation {scenario_id} round {round_no + 1}: {record.get('status')}")
        finally:
            traffic.stop()
        traffic.write_csv(self.out / "benign_actions_correlation.csv")
        run_ids = {r.get("run_id") for r in self.correlation if r.get("run_id")}
        for record in self.correlation:
            record["checks"] = correlation_checks(record, run_ids)
        self.correlation.append({"benign_counts": traffic.counts()})
        self.save()

    def benign_baseline(self) -> None:
        """Normal traffic, near-misses included, during a `benign_baseline` observation run.

        The platform collects, normalizes and stores the run's telemetry and
        checks every detection rule over it, exactly as for a scenario run
        (scenario type `observation`). Every alert it raises is a false positive.
        """
        lab_id = self.lab["id"]
        definition = self.api.get(f"/scenarios/{BASELINE}")
        duration = int(definition["execution"]["parameters"]["duration_seconds"])
        record: dict[str, Any] = {"phase": "benign_baseline", "telemetry_source": "docker", "falco": False,
                                  "scenario_id": BASELINE, "duration_seconds": duration}
        record["reset"] = lab_operation(self.api, lab_id, "reset", False)
        if not record["reset"]["ok"]:
            raise RuntimeError(f"lab reset failed: {record['reset']['error']}")
        before = self._counts()
        run = self.api.post(f"/labs/{lab_id}/runs", {"scenario_id": BASELINE})
        record["run_id"] = run["id"]
        while run["status"] == "pending":
            time.sleep(0.2)
            run = self.api.get(f"/runs/{run['id']}")
        started = parse_time(run["started_at"])
        # Stop sending 15 s before the observation ends, so nothing falls on the edge.
        traffic_seconds = duration - (utcnow() - started).total_seconds() - 15
        traffic = BenignTraffic(seed=self.args.seed, typo_users=(), container_commands=False)
        plan = NearMissPlan(traffic)
        if plan.last_offset() + 10 > traffic_seconds:
            raise RuntimeError(f"the near-miss plan ({plan.last_offset()} s) does not fit in {traffic_seconds:.0f} s")
        log(f"benign baseline: run {run['id']}, {duration} s observation, near-miss plan of {plan.last_offset():.0f} s")
        with StatsSampler({"phase": "benign_baseline", "telemetry_source": "docker", "falco": False,
                           "scenario_id": BASELINE, "index": 1}) as sampler:
            traffic.start(traffic_seconds)
            plan.start()
            run = wait_run(self.api, run["id"], timeout=duration + 300)
            traffic.stop()
        self.stats.extend({**s, "run_id": record["run_id"]} for s in sampler.samples)
        traffic.write_csv(self.out / "benign_baseline_actions.csv")
        record.update(status=run["status"], error=run["error"], started_at=run["started_at"],
                      finished_at=run["finished_at"], parameters=run["parameters"])
        record["report"] = self.api.get(f"/runs/{run['id']}/report")
        record["alerts"] = alert_details(self.api, run["id"])
        incidents = [self.api.get(f"/incidents/{i['id']}") for i in self.api.get(f"/incidents?run_id={run['id']}")]
        record["incidents"] = [{k: v for k, v in i.items() if k != "timeline"} for i in incidents]
        record["events"] = run_events(self.api, run["id"])
        record["actions"] = traffic.counts()
        record["http_errors"] = sum(1 for a in traffic.actions if a.status is None)
        record["near_miss_plan"] = [{"offset_s": at, "kind": kind} for at, kind, _ in plan.schedule()]
        after = self._counts()
        record["alerts_outside_run"] = (after["alerts"] - before["alerts"]) - len(record["alerts"])
        try:
            self.api.post(f"/runs/{run['id']}/cleanup", timeout=300)
            record["cleanup"] = {"ok": True}
        except ApiError as exc:
            record["cleanup"] = {"ok": False, "error": str(exc)[:300]}
        for incident in incidents:
            try:
                self.api.post(f"/incidents/{incident['id']}/close")
            except ApiError:
                pass
        record["false_positives"] = false_positive_table(record)
        self.benign = record
        fp = record["false_positives"]
        log(f"benign baseline: {run['status']}, {len(record['events'])} events stored, "
            f"{len(record['alerts'])} alert(s), false-positive rate {fp['rate']}")
        self.save()

    def _counts(self) -> dict:
        return {"alerts": len(self.api.get("/alerts")), "incidents": len(self.api.get("/incidents"))}


def run_events(api: Api, run_id: str) -> list[dict]:
    """Every stored event of a run (the API returns at most 1000 per call)."""
    events: dict[str, dict] = {}
    since = None
    while True:
        query = f"/events?run_id={run_id}&limit=1000" + (f"&since={since}" if since else "")
        page = api.get(query.replace("+", "%2B"))
        fresh = [e for e in page if e["event_id"] not in events]
        events.update((e["event_id"], e) for e in page)
        if len(page) < 1000 or not fresh:
            return sorted(events.values(), key=lambda e: e["timestamp"])
        since = page[-1]["timestamp"]


# What each rule counts and groups by, as far as the event API shows it (rules/*.yaml).
RULE_VIEW = {
    "repeated_failed_logins": ("authentication_failure", lambda e: (e.get("source_ip"),)),
    "repeated_access_denied": ("authorization_denied", lambda e: (e.get("username"), e.get("resource"))),
    "suspicious_container_process": ("suspicious_process", lambda e: (e.get("resource"),)),
}


def peak_in_window(times: list[datetime], window_seconds: int) -> int:
    """The most events that fall in any one window of `window_seconds`."""
    times = sorted(times)
    best = start = 0
    for end in range(len(times)):
        while (times[end] - times[start]).total_seconds() > window_seconds:
            start += 1
        best = max(best, end - start + 1)
    return best


def false_positive_table(record: dict) -> dict:
    """Per rule: the benign events it saw, how close they came to its threshold, and its alerts.

    False-positive rate: of the rules the benign traffic gave something to
    count, the share that raised an alert. Every alert of a baseline run is a
    false positive, because the platform sent nothing.
    """
    events = record.get("events") or []
    alerts = record.get("alerts") or []
    rules = ((record.get("report") or {}).get("detection") or {}).get("rules") or []
    rows = []
    for check in rules:
        rule = check["rule"]
        event_type, group = RULE_VIEW.get(rule, (None, None))
        matching = [e for e in events if e["event_type"] == event_type]
        groups: dict[tuple, list[datetime]] = {}
        for event in matching:
            groups.setdefault(group(event), []).append(parse_time(event["timestamp"]))
        peak = max((peak_in_window(t, check["window_seconds"]) for t in groups.values()), default=0)
        raised = [a for a in alerts if a["rule_id"] == rule]
        rows.append({
            "rule": rule, "threshold": check["threshold"], "window_seconds": check["window_seconds"],
            "benign_events": len(matching), "groups": len(groups), "peak_in_one_window": peak,
            "alerts": len(raised), "events_in_alerts": sum(a["event_count"] for a in raised),
        })
    exercised = [r for r in rows if r["benign_events"]]
    alerted = [r for r in exercised if r["alerts"]]
    minutes = (record.get("duration_seconds") or 0) / 60
    return {
        "per_rule": rows,
        "rules_exercised": len(exercised), "rules_alerted": len(alerted),
        "rate": _rate(len(alerted), len(exercised)),
        "alerts": len(alerts), "incidents": len(record.get("incidents") or []),
        "alerts_per_hour": round(len(alerts) * 60 / minutes, 1) if minutes else None,
    }


def correlation_checks(record: dict, all_run_ids: set) -> dict:
    run_id = record.get("run_id")
    alerts = record.get("alerts", [])
    incidents = record.get("incidents", [])
    events = [e for a in alerts for e in a.get("events", [])]
    benign = [e for e in events if benign_event(e)]
    return {
        "has_alert": bool(alerts),
        "incident_count": len(incidents),
        "every_alert_in_an_incident": all(a.get("incident_id") for a in alerts),
        "incidents_belong_to_run": all(i.get("run_id") == run_id for i in incidents),
        "incident_alerts_from_this_run": all(a.get("run_id") == run_id for i in incidents for a in i.get("alerts", [])),
        "alert_events_from_this_run": all(e.get("run_id") == run_id for e in events),
        "events_from_other_runs": sum(1 for e in events if e.get("run_id") != run_id and e.get("run_id") in all_run_ids),
        "benign_events_in_alerts": len(benign),
        "benign_event_references": [e["raw_event_reference"] for e in benign],
        "alert_event_count": len(events),
    }


def run_check_row(record: dict) -> dict:
    checks = record["checks"]
    separated = (checks["incident_count"] == 1 and checks["every_alert_in_an_incident"]
                 and checks["incidents_belong_to_run"] and checks["incident_alerts_from_this_run"]
                 and checks["alert_events_from_this_run"])
    return {
        "round": record.get("index"), "scenario_id": record["scenario_id"], "run_id": record.get("run_id"),
        "status": record.get("status"), **{k: v for k, v in checks.items() if k != "benign_event_references"},
        "own_incident_only": separated, "no_benign_merged": checks["benign_events_in_alerts"] == 0,
    }


# --- flattening and summary ----------------------------------------------------------------
def expected_rule(record: dict) -> Optional[str]:
    return ((record.get("report") or {}).get("detection") or {}).get("rule")


def run_row(record: dict) -> dict:
    report = record.get("report") or {}
    rule = expected_rule(record)
    alerts = [a for a in report.get("alerts", []) if a["rule_id"] == rule]
    latency = alerts[0]["detection_latency_seconds"] if alerts else None
    # Run finish: the last event in the run's alert, i.e. the script's last action.
    # (The run's finished_at is set after detection, so it cannot be used.)
    finish_to_alert = None
    if alerts and alerts[0].get("last_event_at") and alerts[0].get("created_at"):
        finish_to_alert = round((parse_time(alerts[0]["created_at"])
                                 - parse_time(alerts[0]["last_event_at"])).total_seconds(), 3)
    response = record.get("response") or {}
    sources: dict[str, int] = {}
    for alert in record.get("alerts", []):
        for event in alert.get("events", []):
            sources[event["source"]] = sources.get(event["source"], 0) + 1
    verification = report.get("post_run_verification") or {}
    return {
        "phase": record.get("phase"), "telemetry_source": record.get("telemetry_source"),
        "falco": record.get("falco"), "index": record.get("index"), "scenario_id": record.get("scenario_id"),
        "scenario_version": record.get("scenario_version"), "run_id": record.get("run_id"),
        "status": record.get("status"), "error": record.get("error"),
        "reset_ready_s": (record.get("reset") or {}).get("ready_seconds"),
        "run_duration_s": (report.get("run") or {}).get("duration_seconds"),
        "events_collected": (report.get("telemetry") or {}).get("collected_total"),
        "expected_telemetry_met": (report.get("telemetry") or {}).get("all_expected_met"),
        "alerts": len(record.get("alerts", [])), "expected_alert": bool(alerts),
        "alert_event_count": alerts[0]["event_count"] if alerts else None,
        "alert_events_by_source": json.dumps(sources, sort_keys=True) if sources else "",
        "detection_latency_s": latency,
        "finish_to_alert_s": finish_to_alert,
        "incidents": len(record.get("incidents", [])),
        "risk_score": (record.get("incidents") or [{}])[0].get("risk_score") if record.get("incidents") else None,
        "response_action": response.get("action"), "response_status": response.get("status"),
        "response_executed": response.get("executed"), "response_verified": response.get("verified"),
        "response_seconds": response.get("approve_to_final_seconds"),
        "response_after_cleanup": response.get("status_after_cleanup"),
        "post_run_verification_passed": verification.get("passed") if verification else None,
        "cleanup_ok": (record.get("cleanup") or {}).get("ok"),
        "stats_samples": record.get("stats_samples"),
    }


def _stats(values: list[float]) -> dict:
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0, "min": None, "median": None, "max": None}
    return {"n": len(values), "min": round(min(values), 3), "median": round(statistics.median(values), 3),
            "max": round(max(values), 3)}


def _rate(ok: int, total: int) -> dict:
    return {"ok": ok, "total": total, "rate": round(ok / total, 3) if total else None}


def summarize(out: Path) -> dict:
    runs = json.loads((out / "runs.json").read_text())
    rows = [run_row(r) for r in runs]
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        groups.setdefault((row["telemetry_source"], row["scenario_id"]), []).append(row)
    summary: dict[str, Any] = {"execution": [], "detection": [], "latency": [], "response": [],
                               "post_run_verification": []}
    for (source, scenario_id), group in sorted(groups.items()):
        key = {"telemetry_source": source, "scenario_id": scenario_id}
        summary["execution"].append({**key, **_rate(sum(r["status"] == "completed" for r in group), len(group)),
                                     "statuses": _count(r["status"] for r in group)})
        summary["detection"].append({**key, **_rate(sum(r["expected_alert"] for r in group), len(group)),
                                     "alert_events_by_source": _count(r["alert_events_by_source"] for r in group)})
        summary["latency"].append({**key, **_stats([r["detection_latency_s"] for r in group])})
        summary.setdefault("finish_to_alert", []).append({**key, **_stats([r["finish_to_alert_s"] for r in group])})
        responders = [r for r in group if r["response_action"]]
        if scenario_id in RESPONSE_OF:
            summary["response"].append({
                **key, "action": RESPONSE_OF[scenario_id], "requested": len(responders),
                "executed": _rate(sum(bool(r["response_executed"]) for r in responders), len(responders)),
                "verified": _rate(sum(bool(r["response_verified"]) for r in responders), len(responders)),
                "reverted_by_cleanup": _rate(sum(r["response_after_cleanup"] == "reverted" for r in responders), len(responders)),
                "seconds": _stats([r["response_seconds"] for r in responders]),
            })
        else:
            checked = [r for r in group if r["post_run_verification_passed"] is not None]
            summary["post_run_verification"].append({**key, **_rate(sum(bool(r["post_run_verification_passed"]) for r in checked), len(checked))})

    lab_setup = list(csv.DictReader((out / "lab_setup.csv").open())) if (out / "lab_setup.csv").exists() else []
    summary["lab_setup"] = []
    for (source, op), items in sorted(_group(lab_setup, lambda r: (r["telemetry_source"], r["operation"])).items()):
        summary["lab_setup"].append({"telemetry_source": source, "operation": op,
                                     "falco": items[0]["falco"],
                                     "ok": _rate(sum(r["ok"] == "True" for r in items), len(items)),
                                     **_stats([float(r["ready_seconds"]) for r in items if r.get("ready_seconds")])})

    stats_rows = list(csv.DictReader((out / "docker_stats.csv").open())) if (out / "docker_stats.csv").exists() else []
    summary["resources"] = []
    for (phase, scenario_id, container), items in sorted(
            _group(stats_rows, lambda r: (r["telemetry_source"], r["scenario_id"], r["container"])).items()):
        cpu = [float(r["cpu_percent"]) for r in items]
        mem = [float(r["mem_mib"]) for r in items]
        summary["resources"].append({"telemetry_source": phase, "scenario_id": scenario_id,
                                     "container": container.replace("cloudattacklab-", ""), "samples": len(items),
                                     "cpu_mean": round(statistics.mean(cpu), 2), "cpu_max": round(max(cpu), 2),
                                     "mem_mean_mib": round(statistics.mean(mem), 1), "mem_max_mib": round(max(mem), 1)})

    correlation_path = out / "correlation.json"
    if correlation_path.exists():
        correlation = json.loads(correlation_path.read_text())
        checks = [run_check_row(c) for c in correlation if "checks" in c]
        summary["correlation"] = {
            "runs": len(checks),
            "own_incident_only": _rate(sum(c["own_incident_only"] for c in checks), len(checks)),
            "no_benign_merged": _rate(sum(c["no_benign_merged"] for c in checks), len(checks)),
            "events_from_other_runs": sum(c["events_from_other_runs"] for c in checks),
            "benign_events_in_alerts": sum(c["benign_events_in_alerts"] for c in checks),
            "per_run": checks,
            "benign_counts": next((c["benign_counts"] for c in correlation if "benign_counts" in c), None),
        }
    benign_path = out / "benign_session.json"
    if benign_path.exists():
        summary["benign"] = json.loads(benign_path.read_text())
    baseline_path = out / "benign_baseline.json"
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text())
        if baseline.get("events") is not None:
            baseline["false_positives"] = false_positive_table(baseline)
            summary["benign_baseline"] = {
                "run_id": baseline.get("run_id"), "status": baseline.get("status"),
                "duration_seconds": baseline.get("duration_seconds"),
                "actions": baseline.get("actions"), "http_errors": baseline.get("http_errors"),
                "events_stored": len(baseline["events"]),
                "events_by_type": _count(f"{e['source']} {e['event_type']}" for e in baseline["events"]),
                "alerts": [{"rule_id": a["rule_id"], "event_count": a["event_count"], "title": a["title"]}
                           for a in baseline.get("alerts") or []],
                "false_positives": baseline["false_positives"],
            }
    write_json(out / "summary.json", summary)
    return summary


def _group(items, key) -> dict:
    out: dict = {}
    for item in items:
        out.setdefault(key(item), []).append(item)
    return out


def _count(values) -> dict:
    out: dict = {}
    for value in values:
        out[str(value)] = out.get(str(value), 0) + 1
    return out


def _pct(rate: dict) -> str:
    return f"{rate['ok']}/{rate['total']} ({rate['rate'] * 100:.0f}%)" if rate["total"] else "0/0"


def print_tables(summary: dict) -> str:
    lines: list[str] = []

    def table(title, header, rows):
        lines.extend(["", f"### {title}", "", "| " + " | ".join(header) + " |", "|" + "---|" * len(header)])
        lines.extend("| " + " | ".join(str(c) for c in row) + " |" for row in rows)

    table("Scenario execution success", ["Source", "Scenario", "Completed", "Statuses"],
          [[e["telemetry_source"], e["scenario_id"], _pct(e), e["statuses"]] for e in summary["execution"]])
    table("Detection rate", ["Source", "Scenario", "Expected alert raised", "Alert events by source"],
          [[d["telemetry_source"], d["scenario_id"], _pct(d), d["alert_events_by_source"]] for d in summary["detection"]])
    table("Time from threshold-crossing event to alert (s)", ["Source", "Scenario", "n", "min", "median", "max"],
          [[x["telemetry_source"], x["scenario_id"], x["n"], x["min"], x["median"], x["max"]] for x in summary["latency"]])
    table("Time from run finish (last event) to alert (s)", ["Source", "Scenario", "n", "min", "median", "max"],
          [[x["telemetry_source"], x["scenario_id"], x["n"], x["min"], x["median"], x["max"]]
           for x in summary.get("finish_to_alert", [])])
    table("Response actions", ["Source", "Action", "Executed", "Verified", "Reverted by cleanup", "Approve→result s (median)"],
          [[r["telemetry_source"], r["action"], _pct(r["executed"]), _pct(r["verified"]), _pct(r["reverted_by_cleanup"]),
            r["seconds"]["median"]] for r in summary["response"]])
    table("Post-run verification (no response action)", ["Source", "Scenario", "Passed"],
          [[p["telemetry_source"], p["scenario_id"], _pct(p)] for p in summary["post_run_verification"]])
    table("Lab setup / reset time (s, request → healthy)", ["Source", "Operation", "Falco", "OK", "n", "min", "median", "max"],
          [[s["telemetry_source"], s["operation"], s["falco"], _pct(s["ok"]), s["n"], s["min"], s["median"], s["max"]]
           for s in summary["lab_setup"]])
    if summary.get("correlation"):
        c = summary["correlation"]
        table("Correlation (three scenarios back to back, benign traffic running)", ["Runs", "Own incident only",
              "No benign event merged", "Events from other runs", "Benign events in alerts"],
              [[c["runs"], _pct(c["own_incident_only"]), _pct(c["no_benign_merged"]), c["events_from_other_runs"],
                c["benign_events_in_alerts"]]])
    if summary.get("benign_baseline"):
        b = summary["benign_baseline"]
        fp = b["false_positives"]
        table(f"Benign baseline ({b['duration_seconds']} s observation, every rule checked)",
              ["Status", "Actions", "Events stored", "Events by type", "Alerts", "Incidents", "False-positive rate"],
              [[b["status"], b["actions"], b["events_stored"], b["events_by_type"], fp["alerts"], fp["incidents"],
                _pct(fp["rate"])]])
        table("Benign baseline per rule", ["Rule", "Threshold", "Benign events", "Peak in one window", "Alerts"],
              [[r["rule"], f"{r['threshold']} in {r['window_seconds']} s", r["benign_events"],
                r["peak_in_one_window"], r["alerts"]] for r in fp["per_rule"]])
    elif summary.get("benign"):
        b = summary["benign"]
        table("Benign traffic only (no scenario running)", ["Minutes", "Actions", "New alerts", "New incidents", "Events stored"],
              [[b["minutes"], b["actions"], b["new_alerts"], b["new_incidents"], b["events_stored_since_start"]]])
    table("Resources during runs (docker stats)", ["Source", "Scenario", "Container", "Samples", "CPU % mean", "CPU % max",
          "Mem MiB mean", "Mem MiB max"],
          [[r["telemetry_source"], r["scenario_id"], r["container"], r["samples"], r["cpu_mean"], r["cpu_max"],
            r["mem_mean_mib"], r["mem_max_mib"]] for r in summary["resources"]])
    text = "\n".join(lines)
    print(text)
    return text


# --- output helpers ---------------------------------------------------------------------------
def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, default=str))


def write_csv(path: Path, rows: list[dict]) -> None:
    fields: list[str] = []
    for row in rows:
        fields.extend(k for k in row if k not in fields)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or ["empty"])
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Repeatable CloudAttackLab evaluation (API only).")
    parser.add_argument("--docker-runs", type=int, default=10, help="runs of each scenario with TELEMETRY_SOURCE=docker")
    parser.add_argument("--loki-runs", type=int, default=5, help="runs of each scenario with TELEMETRY_SOURCE=loki and Falco on")
    parser.add_argument("--mode", choices=("full", "benign_baseline"), default="full",
                        help="full: the whole plan; benign_baseline: only the benign baseline run")
    parser.add_argument("--no-benign-baseline", action="store_true",
                        help="skip the benign baseline (its length is set in scenarios/benign_baseline.yaml)")
    parser.add_argument("--correlation-rounds", type=int, default=3, help="back-to-back rounds of the three scenarios")
    parser.add_argument("--setup-trials", type=int, default=3, help="lab start measurements per phase")
    parser.add_argument("--scenarios", nargs="+", default=list(SCENARIOS))
    parser.add_argument("--seed", type=int, default=1, help="seed for the benign traffic")
    parser.add_argument("--api", default=API)
    parser.add_argument("--out", help="results directory (default evaluation/results/<UTC date>)")
    parser.add_argument("--keep-running", action="store_true", help="do not stop the lab and services at the end")
    parser.add_argument("--summarize", metavar="DIR", help="only summarize an existing results directory")
    args = parser.parse_args()

    if args.summarize:
        print_tables(summarize(Path(args.summarize)))
        return

    out = Path(args.out) if args.out else ROOT / "evaluation" / "results" / utcnow().strftime("%Y-%m-%d")
    if out.exists() and any(out.iterdir()) and not args.out:
        out = out.with_name(out.name + utcnow().strftime("-%H%M"))
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "plan.json", vars(args) | {"started_at": iso(utcnow())})
    log(f"results -> {out}")

    if args.mode == "benign_baseline":
        args.docker_runs = args.loki_runs = args.correlation_rounds = 0
        args.setup_trials = 1
        args.no_benign_baseline = False
    evaluation = Evaluation(args, out)
    try:
        if args.docker_runs or args.correlation_rounds or not args.no_benign_baseline:
            evaluation.setup_phase("docker", "docker", False)
            evaluation.scenario_runs("docker", "docker", False, args.docker_runs)
            if args.correlation_rounds:
                evaluation.correlation_session(args.correlation_rounds)
            if not args.no_benign_baseline:
                evaluation.benign_baseline()
        if args.loki_runs:
            evaluation.setup_phase("loki", "loki", True)
            evaluation.scenario_runs("loki", "loki", True, args.loki_runs)
    finally:
        evaluation.save()
        write_json(out / "plan.json", vars(args) | {"finished_at": iso(utcnow())})
        if not args.keep_running:
            log("stopping: lab, backend back to .env settings, monitoring, platform")
            if evaluation.lab:
                stop_lab(evaluation.api, evaluation.lab["id"])
            try:
                configure_backend(evaluation.api, None, None)  # back to what .env says
            except Exception as exc:  # noqa: BLE001
                log(f"restoring backend: {exc}")
            sh(["docker", "compose", "--profile", "monitoring", "stop", *MONITORING_SERVICES], check=False)
            sh(["docker", "compose", "stop"], check=False)
    print_tables(summarize(out))


if __name__ == "__main__":
    main()
