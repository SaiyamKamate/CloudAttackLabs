# CloudAttackLab

A local-first security experimentation platform: start an isolated Docker lab, run a controlled attack scenario against ShopCloud, collect real logs, detect, investigate and verify a response.

**Status:** Backend, lab control, the Scenario Engine, and telemetry ingestion with the first detection are implemented (steps 3–5 in `docs/api-contract.md`). Not started: response actions, incidents, Loki/Grafana, dashboard.

What works now:

- FastAPI backend in `backend/app` with PostgreSQL; tables are created by Alembic (`backend/alembic`).
- `GET /api/v1/health` and every "Labs" endpoint: create, list, details (with container status and ShopCloud health), start, stop, reset.
- Lab Manager (`backend/app/lab_manager`). Its Docker adapter is the only code that talks to Docker, and it only runs `docker compose -f lab/docker-compose.lab.yml`. Reset recreates the container and then calls ShopCloud's `POST /lab/reset`.
- Scenario Engine (`backend/app/scenario_engine`) and every "Scenarios and runs" endpoint: list/get scenarios, start a run, get a run, run cleanup.
  - Scenarios are loaded from `/scenarios` with the validating loader; an invalid file is skipped and logged, never run. The allowed targets come from the lab configuration, never the request.
  - Before a run, pre-flight checks the scenario's prerequisites (ShopCloud health, and that the named test account can log in). A run is refused with 409 if the lab is not running, the target is not the lab's target, or a check fails.
  - The run executes the scenario's script from `scenarios/scripts/` as a separate process, bounded by `timeout_seconds`, with the target URL taken from the lab configuration (so a script can never be pointed outside the lab). Runs happen in the background; the API returns the run id at once. One active run per lab.
  - Cleanup runs the scenario's named steps through ShopCloud's `/lab` endpoints, and also runs automatically if a run fails or times out.
- The first scenario, `authentication_abuse`: sends 20 wrong-password logins for `lab_user` and reports how many got 401/429. Its script uses a deliberately wrong password and never a real one.
- Telemetry ingestion and detection (`backend/app/telemetry`, `backend/app/detection`) and every "Events and alerts" read endpoint: `GET /events`, `GET /events/{id}`, `GET /alerts`, `GET /alerts/{id}`.
  - While a run is active (and once more when it finishes), ShopCloud's logs are collected through the Docker adapter, behind a small telemetry-source interface so Loki can replace it later. Lines that are not valid JSON are skipped and counted; nothing is invented.
  - Each log record is normalized into a `NormalizedEvent` using the mapping in `docs/shopcloud-interface.md` section 5. The original record is kept in `events.raw`; `raw_event_reference` is `docker:shopcloud:<request_id>`; the same request_id is never stored twice.
  - Detection rules are small validated YAML files in `/rules`. `repeated_failed_logins` fires when one source IP produces `threshold` failed logins within `window_seconds` (taken from the scenario's `detection`: 10 in 60s). One alert per run and source IP, with every contributing event linked through `alert_events`.
- Every lab start/stop/reset and every run start/finish/failure/cleanup writes an `audit_log` row. There is no login yet, so the actor is `anonymous`.
- One lab can be running at a time, because the lab compose file uses fixed ports.

Run it:

```bash
cp .env.example .env                 # then change the values
docker compose up -d --build         # PostgreSQL + backend (runs the migrations), API on 127.0.0.1:8000

LAB=$(curl -s -X POST http://127.0.0.1:8000/api/v1/labs -H 'Content-Type: application/json' -d '{"name": "demo", "target": "shopcloud"}' | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')
curl -X POST http://127.0.0.1:8000/api/v1/labs/$LAB/start
RUN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/labs/$LAB/runs -H 'Content-Type: application/json' -d '{"scenario_id": "authentication_abuse"}' | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')
curl http://127.0.0.1:8000/api/v1/runs/$RUN          # poll until "completed"
curl "http://127.0.0.1:8000/api/v1/events?run_id=$RUN"   # the normalized events
curl "http://127.0.0.1:8000/api/v1/alerts?run_id=$RUN"   # the alert it raised
curl -X POST http://127.0.0.1:8000/api/v1/runs/$RUN/cleanup

pytest                               # unit tests, no Docker needed
pytest -m integration                # really starts the lab, runs a scenario, collects and detects
```

The backend container uses host networking and mounts the Docker socket, so it needs a Linux Docker Engine (native, or inside WSL 2). On Docker Desktop, enable host networking or run the backend outside Docker: `cd backend && alembic upgrade head && uvicorn app.main:app`.

## What is here

| Path | What it is |
|---|---|
| `docs/shopcloud-interface.md` | What ShopCloud must expose. Shared with whoever builds ShopCloud |
| `docs/api-contract.md` | Planned backend API for the dashboard |
| `backend/app/schemas/event.py` | Common event schema |
| `backend/app/schemas/scenario.py` | Scenario format and the validating loader |
| `backend/app/models.py` | Database tables (SQLAlchemy) |
| `contracts/*.schema.json` | JSON Schema copies of the two schemas, generated by `backend/scripts/export_schemas.py` |
| `scenarios/authentication_abuse.yaml` | First scenario definition |
| `scenarios/scripts/authentication_abuse.py` | The script that scenario runs |
| `rules/repeated_failed_logins.yaml` | First detection rule |
| `mock_shopcloud/` | Fake ShopCloud that follows the interface |
| `lab/docker-compose.lab.yml` | The isolated lab (ShopCloud) |
| `docker-compose.yml` | Platform services (PostgreSQL for now) |
| `tools/check_shopcloud.py` | Checks any running ShopCloud against the interface |

## Try it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt

pytest backend/tests                                   # contract tests

docker compose -f lab/docker-compose.lab.yml up --build -d
python tools/check_shopcloud.py http://127.0.0.1:8081  # should print 14/14
docker compose -f lab/docker-compose.lab.yml logs shopcloud   # the JSON logs
```

## Build order

1. Contracts and fake ShopCloud (this commit)
2. Backend and lab control: FastAPI, PostgreSQL, Docker adapter
3. Scenario Engine and the authentication-abuse script
4. Log ingestion, normalization, first detection rule
5. Basic dashboard, then the Week 3 checkpoint with the real ShopCloud
6. Response and verification, scenario 3, incident timeline, Loki and Grafana, scenario 2 with Falco

## Safety

Scenarios run only against configured lab targets, with bounded parameters, mandatory approval for responses and mandatory cleanup. The scenario loader enforces these rules.
