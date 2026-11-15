# CloudAttackLab

A local-first security experimentation platform: start an isolated Docker lab, run a controlled attack scenario against ShopCloud, collect real logs, detect, investigate and verify a response.

**Status:** Backend, lab control, the Scenario Engine, telemetry ingestion with the first detection, a basic dashboard, and one response action with approval and verification are implemented (steps 3–7 in `docs/api-contract.md`), plus, in step 8, the second application scenario, `unauthorized_resource_access`, in step 9 incident correlation, the incident timeline, a risk score and a run report, and in step 10 reading ShopCloud's logs from Loki (`TELEMETRY_SOURCE=loki`). Not started: login/auth, the container scenario with Falco, the `stop_container` and `isolate_container` actions, PDF export and sharing of reports.

What works now:

- FastAPI backend in `backend/app` with PostgreSQL; tables are created by Alembic (`backend/alembic`).
- `GET /api/v1/health` and every "Labs" endpoint: create, list, details (with container status and ShopCloud health), start, stop, reset.
- Lab Manager (`backend/app/lab_manager`). Its Docker adapter is the only code that talks to Docker, and it only runs `docker compose -f lab/docker-compose.lab.yml`. Reset recreates the container and then calls ShopCloud's `POST /lab/reset`.
- Scenario Engine (`backend/app/scenario_engine`) and every "Scenarios and runs" endpoint: list/get scenarios, start a run, get a run, run cleanup.
  - Scenarios are loaded from `/scenarios` with the validating loader; an invalid file is skipped and logged, never run. The allowed targets come from the lab configuration, never the request.
  - Before a run, pre-flight checks the scenario's prerequisites (ShopCloud health, and that the named test account can log in). A run is refused with 409 if the lab is not running, the target is not the lab's target, or a check fails.
  - The run executes the scenario's script from `scenarios/scripts/` as a separate process, bounded by `timeout_seconds`, with the target URL taken from the lab configuration (so a script can never be pointed outside the lab). Runs happen in the background; the API returns the run id at once. One active run per lab.
  - Cleanup runs the scenario's named steps through ShopCloud's `/lab` endpoints, and also runs automatically if a run fails or times out.
  - A script that exits with a non-zero code fails the run; if it printed a JSON summary, the summary is kept on the failed run.
  - A scenario with no response action can name a post-run check in `execution.parameters` (`verification_check`). After the run the engine repeats that check once against the lab's ShopCloud and stores the result under `verification` in the run's `summary`.
- The first scenario, `authentication_abuse`: sends 20 wrong-password logins for `lab_user` and reports how many got 401/429. Its script uses a deliberately wrong password and never a real one.
- The second scenario, `unauthorized_resource_access` (guide section 5.3): logs in as `lab_restricted`, then requests `GET /api/orders` 8 times with that token. Every request must be refused with 403. The summary reports attempts sent, how many got 403 and how many got anything else. If any request returns 200 the summary says `"access_control_failed": true` and the run ends as `failed`. Its verification, `access_remains_denied`, makes one more request as `lab_restricted` after the run and records whether it was still denied. It has no response action; cleanup is `reset_target_state`.
- Telemetry ingestion and detection (`backend/app/telemetry`, `backend/app/detection`) and every "Events and alerts" read endpoint: `GET /events`, `GET /events/{id}`, `GET /alerts`, `GET /alerts/{id}`.
  - While a run is active (and once more when it finishes), ShopCloud's logs are collected from the telemetry source chosen by `TELEMETRY_SOURCE` (see "Telemetry source: Docker or Loki" below). Lines that are not valid JSON are skipped and counted; nothing is invented.
  - Each log record is normalized into a `NormalizedEvent` using the mapping in `docs/shopcloud-interface.md` section 5. The original record is kept in `events.raw`; `raw_event_reference` is `docker:shopcloud:<request_id>` or `loki:shopcloud:<request_id>`, depending on the source; the same request_id is never stored twice, whichever source read it.
  - Detection rules are small validated YAML files in `/rules`. `repeated_failed_logins` fires when one source IP produces `threshold` failed logins within `window_seconds` (taken from the scenario's `detection`: 10 in 60s). One alert per run and source IP, with every contributing event linked through `alert_events`.
  - A rule's `group_by` is one field or a list of fields, and an optional `match` keeps only events with the listed values. Fields are `source_ip`, `username`, `resource`, or `raw.<name>` for a field of the original log record.
  - `repeated_access_denied` fires when one username gets `threshold` `authorization_denied` events on the same path within `window_seconds` (5 in 60s for the scenario). It counts only 403 denials for a logged-in user (`match: raw.status: 403`); a 401 with no token does not count. One alert per run, username and path.
- Incidents (`backend/app/incidents`): `GET /incidents` lists them with status and risk score; `GET /incidents/{id}` returns one with its alerts, responses, risk score and timeline.
  - Correlation. Only alerts of the same run can share an incident, so two scenarios run one after the other always give two incidents. Inside a run, two alerts are related when the events behind them share a resource and share either a source IP or a username. A new alert joins the oldest incident of its run that holds a related alert; otherwise it opens a new incident. Incidents are never merged or split afterwards. The rules are written at the top of `backend/app/incidents/service.py`. The incident's `correlation_key` records what opened it, for example `run:<run_id>|resource:shopcloud|ip:172.21.0.1`.
  - Evidence chain: incident -> alerts -> `alert_events` -> events -> each event's `raw_event_reference`.
  - Timeline. One time-ordered list mixing: scenario started, the events behind the alerts, each alert firing, scenario finished, every response step (requested, approved or rejected, executed, verified or failed, reverted) and cleanup. Each entry has `time`, `type`, `description` and `evidence` (an API path to the run, event, alert, response or audit rows, and for an event its `raw_event_reference`). It is built on request from the run, the events, the alerts and the audit log; nothing extra is stored.
  - Risk score (`backend/app/incidents/risk.py`). A 0-100 heuristic stored on the incident with a plain-language explanation. It is the sum of: base points for the highest alert severity (info 10, low 25, medium 40, high 60, critical 80); 2 points per event over the detection threshold, at most 20; 5 points per additional correlated alert, at most 15; minus 20 when a response action was verified as working. The result is limited to 0-100. It is recomputed when an alert joins the incident and when a response is verified. No machine learning. It does not predict financial loss and is not a universal measure of risk; the explanation shown to the user says so.
- Run report (`backend/app/reports`): `GET /runs/{run_id}/report` returns JSON with the lab, the scenario and the version that was run, parameters, start and finish times, the script's summary, expected telemetry next to what was collected, the alerts with their detection latency, the incidents with risk score, the responses with their verification result, the post-run verification and the cleanup status. Detection latency is the time from the event that crossed the threshold (the `threshold`-th event linked to the alert) to the alert being created; detection runs when the script ends, so it includes the rest of the run. JSON only: no PDF export and no sharing.
- Response Manager (`backend/app/response_manager`) and every "Response actions" endpoint, plus `GET /audit`.
  - `POST /incidents/{id}/responses` creates a `response_actions` row with status `pending_approval`. The action and its parameters are read from the scenario's `response` section; the endpoints take no request body, so a caller cannot choose the action, its values or the target. One open response per incident.
  - Nothing is executed until it is approved. Reject marks the row `rejected` and leaves ShopCloud untouched.
  - Approve executes `temporary_rate_limit`: `POST /lab/rate-limit` with `enabled=true` and the scenario's `max_attempts` and `window_seconds`, sent to the lab's configured ShopCloud. It is refused with 409 if the lab is not running.
  - Then it verifies with the scenario's check `login_returns_429`: `max_attempts + 2` logins with a deliberately wrong password, and the ones beyond the limit must all get 429. What was sent and the status of every answer is saved in `verification_result`. The status becomes `verified`, or `failed` if ShopCloud did not apply the limit or could not be reached.
  - It is reversible: the cleanup step `disable_rate_limit` turns the limit off and the response becomes `reverted`. Lab reset clears it too.
- A basic React dashboard (`frontend/`) with four pages and a report view: Lab (status, ShopCloud health, Start/Stop/Reset), Scenarios (list, Run, live status, the post-run verification in plain words, Cleanup) and Alerts (table, and each alert's linked events with their `raw_event_reference`). The alert detail has a "Request response" button, Approve and Reject for a pending response, then its status, the verification result in plain words and its audit trail. Incidents lists incidents with status and risk score; an incident's detail shows the risk score with its explanation, the same response controls, the linked alerts and the timeline. A finished run (and an incident) has a "View report" button that shows the run report on a readable page with "Download as JSON". All API calls go through one module, `frontend/src/api.js`.
- Every lab start/stop/reset, every run start/finish/failure/cleanup, and every response step (requested, approved or rejected with who decided, executed, verified or failed, reverted) writes an `audit_log` row, readable through `GET /audit`. There is no login yet, so the actor is `anonymous`.
- One lab can be running at a time, because the lab compose file uses fixed ports.

Run the whole platform (PostgreSQL, backend, dashboard):

```bash
cp .env.example .env                 # then change the values
docker compose up -d --build         # then open the dashboard at http://127.0.0.1:5173
```

The backend API is on `127.0.0.1:8000`; the dashboard proxies `/api` to it. From the dashboard: create and Start the lab, Run `authentication_abuse`, watch it reach `completed`, then open Alerts to see the `repeated_failed_logins` alert and its 20 linked events. On that alert, click "Request response", then Approve: the status becomes `verified` with "2 of 7 follow-up logins were blocked with 429". Back on Scenarios, Cleanup turns the rate limit off and the response shows `reverted`. Then Run `unauthorized_resource_access`: it reaches `completed` with 8 of 8 requests refused and "Verification passed: access is still denied", and Alerts shows a second alert, `repeated_access_denied`, with its 8 linked events. Incidents now shows two incidents, one per run: the first with risk score 40 (60 before its response was verified) and the second with 46. Open one for its timeline, and use "View report" for the run report.

Both the backend and the dashboard containers use host networking and the backend mounts the Docker socket, so this needs a Linux Docker Engine (native, or inside WSL 2). On Docker Desktop, enable host networking.

### Telemetry source: Docker or Loki

The backend reads ShopCloud's logs from one of two sources, chosen with one setting in `.env`:

- `TELEMETRY_SOURCE=docker` (the default): the lab container's logs through the Docker adapter. Works without the monitoring stack.
- `TELEMETRY_SOURCE=loki`: Loki's HTTP API (`/loki/api/v1/query_range`) for `{service="shopcloud"}`, from the run's start to now, at `LOKI_URL` (default `http://127.0.0.1:3100`). Start the monitoring stack first: `docker compose -f monitoring/docker-compose.monitoring.yml up -d`.

```bash
TELEMETRY_SOURCE=loki docker compose up -d backend    # or set it in .env
```

Both sources return the same ShopCloud log lines, so normalization and detection are the same; only `raw_event_reference` changes (`loki:shopcloud:<request_id>`). The Lab page shows the active source and, for Loki, whether it can be reached.

- Late logs. Logs reach Loki a second or two after ShopCloud writes them. After a run, the backend polls Loki every `LOKI_SETTLE_INTERVAL_SECONDS` (2) until the number of records stops growing, for at most `LOKI_SETTLE_TIMEOUT_SECONDS` (20). An empty answer does not count as settled. Alloy (`monitoring/config.alloy`) looks for new containers every 5 seconds instead of its default minute, because a lab start or reset creates a new ShopCloud container whose logs only reach Loki once Alloy has found it.
- No fallback. If `TELEMETRY_SOURCE=loki` and Loki cannot be reached, the run fails before its script starts (nothing is sent to ShopCloud) with an error such as "Telemetry source 'loki' is not available: Loki at http://127.0.0.1:3100 could not be reached ...", shown on the run in the dashboard. A Loki failure during a run, or no ShopCloud logs at all after the timeout, also fails the run, and no alert is raised from partial telemetry. The backend never falls back to Docker and never invents events.

Networking. The backend and Loki are in different Compose projects. The backend already uses host networking (that is how it reaches ShopCloud on `127.0.0.1:8081`), and the monitoring stack publishes Loki on the host's `127.0.0.1:3100`, so the backend reaches Loki at `http://127.0.0.1:3100` with no extra wiring. I chose this over joining the two projects with a shared external Docker network because it is the pattern the backend already uses for the lab, it needs no change to either compose project's networks, neither project has to start first to create a network, and Loki stays reachable from this machine only. If the backend ever leaves host networking, the alternative is an external network that both projects join, with `LOKI_URL=http://loki:3100`.

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
| `scenarios/unauthorized_resource_access.yaml` | Second scenario definition |
| `scenarios/scripts/unauthorized_resource_access.py` | The script that scenario runs |
| `rules/repeated_access_denied.yaml` | Second detection rule |
| `backend/app/incidents/service.py` | Correlation rules: which alerts share an incident |
| `backend/app/incidents/risk.py` | The risk score heuristic and its explanation |
| `backend/app/incidents/timeline.py` | Builds the incident timeline |
| `backend/app/reports/service.py` | Builds the run report |
| `backend/app/telemetry/source.py` | The telemetry source interface and the Docker source |
| `backend/app/telemetry/loki.py` | The Loki telemetry source |
| `monitoring/` | Loki, Alloy and Grafana (`docker compose -f monitoring/docker-compose.monitoring.yml up -d`) |
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
