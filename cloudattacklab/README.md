# CloudAttackLab

CloudAttackLab is a local security lab that runs on one computer with Docker. It starts an isolated copy of a small web shop, ShopCloud, runs a controlled attack scenario against it (repeated failed logins, attempts to read data a user may not see, or a shell started inside a container), collects the real logs and container events that the attack produces, detects the attack with simple rules, groups the alerts into an incident with an evidence timeline and a risk score, and lets you approve a response (a login rate limit, or stopping the container) and check that it worked. Everything is shown on a web dashboard, every step is recorded in an audit log, and the lab can be cleaned up and reset to where it started. It is a student project, built to show one complete, evidence-based workflow, not a production security tool.

![The dashboard's Lab page with a running lab](docs/images/01-lab-running.jpg)

## Quick start

You need Docker Engine with Docker Compose on Linux, or inside WSL 2 on Windows 11 (the only setup tested). The [installation guide](docs/install.md) has the details and a checklist for a new machine.

```bash
git clone https://github.com/SaiyamKamate/CloudAttackLabs.git
cd CloudAttackLabs/cloudattacklab
[ -f .env ] || cp .env.example .env     # creates .env only if you have none; then set your own passwords in it
docker compose up -d --build
curl -s http://127.0.0.1:8000/api/v1/health    # {"status":"ok","database":"ok"}
```

Then open <http://127.0.0.1:5173>, click **Create lab** and **Start**, and follow the [user guide](docs/user-guide.md) to run the first scenario.

Optional monitoring stack (Loki, Alloy, Grafana at <http://127.0.0.1:3000>):

```bash
docker compose --env-file .env -f monitoring/docker-compose.monitoring.yml up -d
```

## Documentation

| Document | What it covers |
|---|---|
| [Installation guide](docs/install.md) | Requirements, installing Docker, `.env`, starting and checking every part, Falco, stopping, uninstalling, a fresh-machine checklist |
| [User guide](docs/user-guide.md) | The dashboard step by step, with screenshots: run the three scenarios, read alerts and evidence, incidents, responses, reports, cleanup and reset |
| [Architecture](docs/architecture.md) | The modules, the containers and networks, how data flows from a scenario to an alert to a response, and the common event schema |
| [Adding a scenario](docs/adding-a-scenario.md) | The scenario YAML fields, the script, the detection rule, tests and cleanup, with a template |
| [Troubleshooting](docs/troubleshooting.md) | Real problems from this project and their fixes |
| [Known limitations](docs/known-limitations.md) | What the lab does not do, or does only simply, and why |
| [Safety controls](docs/safety.md) | Each safety control and where the code enforces it |
| [API contract](docs/api-contract.md) | Every backend endpoint, its body, results and errors |
| [ShopCloud interface](docs/shopcloud-interface.md) | What ShopCloud must provide to the platform: endpoints, test accounts, log format |
| [Evaluation results](docs/evaluation-results.md) | Measurements of 17 November 2026 (detection rate, latency, correlation, responses, setup time, resources), with threats to validity and how to repeat them |
| [Audit, November 2026](docs/audit-2026-11.md) | The independent audit of 16 November 2026. A record of that day: its findings were fixed or are listed as known limitations |

## Status

Version 1.0 of the planned workflow is built and was evaluated on 17 November 2026 on one laptop: 54 scenario runs, all `completed`; in the 45 runs measured for detection, the expected alert was raised every time; all 30 approved responses were executed, verified and reverted by cleanup. Details, and why these numbers prove less than they seem, are in the [evaluation](docs/evaluation-results.md).

What exists:

- The platform: FastAPI backend with PostgreSQL, and a React dashboard (Lab, Scenarios, Alerts, Incidents, run report).
- The lab: the real ShopCloud (`shopcloud/`) and a test container, started, stopped and reset by the backend through Docker Compose.
- Three scenarios: `authentication_abuse`, `unauthorized_resource_access` and `suspicious_container_activity`, each with a detection rule and cleanup.
- Telemetry from ShopCloud's logs (through Docker or Loki), Docker events and, optionally, Falco; normalized into one event schema.
- Rule-based detection, incident correlation, an explained risk score, an evidence timeline, and a JSON run report.
- Two responses with approval, verification and revert: `temporary_rate_limit` and `stop_container`.
- An audit log of every action, and the safety controls in [safety.md](docs/safety.md).
- A monitoring stack (Loki, Alloy, Grafana with a provisioned dashboard).
- Tests: `pytest backend/tests` (246 unit tests), `pytest shopcloud/tests` (22), and `pytest -m integration` (8, against the real lab).
- An evaluation script, `tools/evaluate.py`, and a ShopCloud interface checker, `tools/check_shopcloud.py`.

Tested only on Windows 11 with Docker Engine inside WSL 2. See [known limitations](docs/known-limitations.md).

## Out of scope

As planned in the project guide (section 2, "Version 1.0 scope"), these are not part of version 1.0:

- **Kubernetes**: deferred. Docker Compose is the only environment. The Lab Manager talks to Docker through one interface (`LabAdapter`), which a Kubernetes adapter (for example with Kind or Minikube) would implement later.
- **Cloud (AWS, Azure, GCP)**: deferred. Cloud adapters and cloud-specific scenarios need accounts, credentials, logging and cost controls, and belong to later releases.
- **Machine learning**: out of scope. Detection uses rules, and the risk score is a fixed formula that explains itself.

Also not built: a dashboard login, Prometheus (optional in the guide), the `isolate_container` response, and PDF export or sharing of reports.

## Repository layout

| Path | What it is |
|---|---|
| `backend/` | The FastAPI backend (`backend/app`, one package per module), Alembic migrations and tests |
| `frontend/` | The React dashboard, served by nginx in its container |
| `shopcloud/` | ShopCloud, the lab target (FastAPI, SQLite), with its own tests |
| `mock_shopcloud/` | A small in-memory stand-in that follows the same interface; not used by the lab. Kept for quick runs and to test `tools/check_shopcloud.py` |
| `lab/` | The lab's compose file (ShopCloud, the test container, optional Falco) and Falco's image and rule |
| `scenarios/` | Scenario definitions (YAML) and their scripts |
| `rules/` | Detection rules (YAML) |
| `contracts/` | JSON Schema copies of the event and scenario formats, generated by `backend/scripts/export_schemas.py` |
| `monitoring/` | Loki, Alloy and Grafana, with the provisioned dashboard |
| `tools/` | `check_shopcloud.py`, `evaluate.py`, `benign_traffic.py`, `evaluation_charts.py` |
| `evaluation/results/` | Raw results of the evaluation |
| `docs/` | The documentation above |
| `docker-compose.yml` | The platform: PostgreSQL, backend, dashboard |
| `.env.example` | Every setting, with comments. Copy to `.env` |

## Running the tests

From `cloudattacklab/`, in WSL, with Python 3:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
pytest backend/tests        # 246 passed, 8 deselected (the integration tests)
pytest shopcloud/tests      # 22 passed
pytest -m integration       # starts the real lab with Docker; several minutes
```

The two first lines were not run for this README (the test machine already had a virtual environment with these requirements); the three `pytest` counts are from 17 November 2026, except `pytest -m integration`, last run by the November 2026 audit (8 passed).

## Safety

Scenarios run only against the lab's own targets, whose URL is checked to be inside the lab; their parameters are bounded; the only command run in a container is fixed in the code; every response needs approval and is verified and reversible; only the lab's test container can be stopped; every scenario has cleanup; and every action is audited. [safety.md](docs/safety.md) shows where each rule is enforced, and [known limitations](docs/known-limitations.md) what they do not cover. Every port is bound to `127.0.0.1`, and the default credentials in this repository are for a local lab only.
