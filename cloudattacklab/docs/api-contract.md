# CloudAttackLab backend API (v1)

The React dashboard talks to FastAPI only through these endpoints. All paths start with `/api/v1`, all bodies are JSON, all times are UTC. The running backend also serves its OpenAPI description at `/openapi.json`.

**Status:** every endpoint below is implemented, except `POST /auth/login`, which is not built (there is no dashboard login; see `docs/known-limitations.md`). The "Status" column says which build step added each endpoint, and what was added since. "Implemented" means it exists in `backend/app` and is covered by `pytest backend/tests`.

## Labs

| Method | Path | Purpose | Status |
|---|---|---|---|
| `POST` | `/labs` | Create a lab `{name, target}`. `target` must be a configured lab target (422 `unknown_target`) | Implemented (step 3) |
| `GET` | `/labs` | List labs with their stored status | Implemented (step 3) |
| `GET` | `/labs/{lab_id}` | Lab details: container status, target health, `target_url` (the configured URL and whether it is inside the lab), the active run, the telemetry source and Falco. The stored status is compared with the real containers and corrected (`stopped`, `degraded`, back to `running`); `status_detail` names what is down | Implemented (step 3; URL check, active run and status correction added for the November 2026 audit) |
| `POST` | `/labs/{lab_id}/start` | Start the Docker lab. 409 `target_url_refused` if the target URL is not inside the lab | Implemented (step 3) |
| `POST` | `/labs/{lab_id}/stop` | Stop it | Implemented (step 3) |
| `POST` | `/labs/{lab_id}/reset` | Restore the baseline (containers and target state). Allowed on a `running` or `degraded` lab. 409 `run_in_progress` while a run is active in the lab | Implemented (step 3; run check added for the audit) |

Lab statuses: `created`, `starting`, `running`, `degraded` (stored as running, but a lab container is down), `stopped`, `error`.

## Scenarios and runs

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/scenarios` | List valid scenario definitions | Implemented (step 4) |
| `GET` | `/scenarios/{scenario_id}` | One definition | Implemented (step 4) |
| `POST` | `/labs/{lab_id}/runs` | Start a run `{scenario_id}`. Checks the target URL, the target and prerequisites first | Implemented (step 4) |
| `GET` | `/runs/{run_id}` | Run status: `pending`, `running`, `completed`, `incomplete`, `failed`, `cleaned_up`. `error` says why a run is `failed` or `incomplete` | Implemented (step 4; `incomplete` added for the audit) |
| `POST` | `/runs/{run_id}/cleanup` | Run the scenario's cleanup steps. 409 `run_in_progress` while this run, or another run in the same lab, is active | Implemented (step 4; other-run check added for the audit) |

A run is `completed` only when its script finished, every `expected_telemetry` item was collected and the script reported no request errors; otherwise it is `incomplete` (see the README, "Run statuses").

## Events, alerts and incidents

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/events` | Normalized events. Filters: `run_id`, `event_type`, `since`, `limit` | Implemented (step 5) |
| `GET` | `/events/{event_id}` | One event with its raw evidence | Implemented (step 5) |
| `GET` | `/alerts` | Alerts. Filters: `run_id`, `rule_id` | Implemented (step 5) |
| `GET` | `/alerts/{alert_id}` | One alert and the events that caused it | Implemented (step 5) |
| `GET` | `/incidents` | Incidents. Filter: `run_id` | Implemented (step 9) |
| `GET` | `/incidents/{incident_id}` | Incident with alerts, responses, timeline and risk score | Implemented (step 9) |
| `POST` | `/incidents/{incident_id}/close` | Close the incident (no body). 409 `incident_closed` if already closed, 409 `response_in_progress` while a response is pending approval | Implemented (added for the audit) |

Incident statuses: `open`, `closed`. A closed incident takes no new response (409 `incident_closed`).

## Response actions

| Method | Path | Purpose | Status |
|---|---|---|---|
| `POST` | `/incidents/{incident_id}/responses` | Request the scenario's response action (no body). Starts as `pending_approval` | Implemented (step 7) |
| `POST` | `/responses/{response_id}/approve` | Approve, execute, then verify | Implemented (step 7) |
| `POST` | `/responses/{response_id}/reject` | Reject | Implemented (step 7) |
| `GET` | `/responses/{response_id}` | Status and verification result | Implemented (step 7) |

Response statuses: `pending_approval`, `approved`, `rejected`, `executed`, `verified`, `failed`, `reverted`. Actions: `temporary_rate_limit`, `stop_container`.

## Reports, audit, health and auth

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/runs/{run_id}/report` | Experiment report for one run (JSON) | Implemented (step 9) |
| `GET` | `/audit` | Audit trail: labs, runs, approvals, responses, cleanup, incidents. Filters: `object_type`, `object_id`, `action`, `limit` | Implemented (step 7) |
| `GET` | `/health` | Backend health and database check | Implemented (step 3) |
| `POST` | `/auth/login` | Dashboard login, returns a token | **Not built.** There is no login; every audit row has actor `anonymous` |

## Shared rules

- Errors: `{"error": "<short_code>", "detail": "<readable text>"}` with a suitable 4xx or 5xx code.
- IDs are UUIDs, except `scenario_id` and `rule_id`, which are names like `authentication_abuse`.
- Event objects follow `contracts/event.schema.json`. Scenario objects follow `contracts/scenario.schema.json`.
- A run is refused (`409`) if the lab is not running, the target URL is not inside the lab (`target_url_refused`), the target is not a configured lab target, another run is active, or a prerequisite fails.
