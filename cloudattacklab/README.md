# CloudAttackLab

A local-first security experimentation platform: start an isolated Docker lab, run a controlled attack scenario against ShopCloud, collect real logs, detect, investigate and verify a response.

**Status:** Backend, lab control, the Scenario Engine, telemetry ingestion with the first detection, and a basic dashboard are implemented (steps 3–6 in `docs/api-contract.md`). Not started: login/auth, response actions, incidents, reports.

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
- A basic React dashboard (`frontend/`) with three pages: Lab (status, ShopCloud health, Start/Stop/Reset), Scenarios (list, Run, live status, Cleanup) and Alerts (table, and each alert's linked events with their `raw_event_reference`). All API calls go through one module, `frontend/src/api.js`.
- Every lab start/stop/reset and every run start/finish/failure/cleanup writes an `audit_log` row. There is no login yet, so the actor is `anonymous`.
- One lab can be running at a time, because the lab compose file uses fixed ports.

Run the whole platform (PostgreSQL, backend, dashboard):

```bash
cp .env.example .env                 # then change the values
docker compose up -d --build         # then open the dashboard at http://127.0.0.1:5173
```

The backend API is on `127.0.0.1:8000`; the dashboard proxies `/api` to it. From the dashboard: create and Start the lab, Run `authentication_abuse`, watch it reach `completed`, then open Alerts to see the `repeated_failed_logins` alert and its 20 linked events.

Both the backend and the dashboard containers use host networking and the backend mounts the Docker socket, so this needs a Linux Docker Engine (native, or inside WSL 2). On Docker Desktop, enable host networking.

### Frontend development

```bash
cd frontend
npm install
npm run dev                          # http://localhost:5173, proxies /api to the backend on :8000
```

Two notes for this repo's setup, both already configured in `frontend/vite.config.js`:

- **Hot reload on the Windows `D:` drive (via WSL).** Native file-change events from `/mnt/d` do not reach WSL's inotify, so Vite never notices saved files. `server.watch.usePolling` makes Vite poll instead, and saving a file now updates the browser.
- **Dev API calls use a Vite proxy, not CORS.** The dashboard calls relative paths (`/api/v1/...`) and Vite forwards them to `http://127.0.0.1:8000`. I chose the proxy over enabling CORS in FastAPI because it keeps the browser talking to a single origin in both development and the container (where nginx does the same forwarding), so `api.js` needs no environment-specific base URL and the backend needs no cross-origin configuration or relaxed headers.

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
