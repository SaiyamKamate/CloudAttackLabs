# CloudAttackLab backend API (v1.0 plan)

The React dashboard talks to FastAPI only through these endpoints. All paths start with `/api/v1`, all bodies are JSON, all times are UTC.

**Status:** planned. Nothing here is implemented yet. The "Step" column is the build step that adds it.

## Labs

| Method | Path | Purpose | Step |
|---|---|---|---|
| `POST` | `/labs` | Create a lab `{name, target}` | 3 |
| `GET` | `/labs` | List labs | 3 |
| `GET` | `/labs/{lab_id}` | Lab details, container status and target health | 3 |
| `POST` | `/labs/{lab_id}/start` | Start the Docker lab | 3 |
| `POST` | `/labs/{lab_id}/stop` | Stop it | 3 |
| `POST` | `/labs/{lab_id}/reset` | Restore the baseline (containers and target state) | 3 |

## Scenarios and runs

| Method | Path | Purpose | Step |
|---|---|---|---|
| `GET` | `/scenarios` | List valid scenario definitions | 4 |
| `GET` | `/scenarios/{scenario_id}` | One definition | 4 |
| `POST` | `/labs/{lab_id}/runs` | Start a run `{scenario_id}`. Checks the target and prerequisites first | 4 |
| `GET` | `/runs/{run_id}` | Run status: `pending`, `running`, `completed`, `failed`, `cleaned_up` | 4 |
| `POST` | `/runs/{run_id}/cleanup` | Run the scenario's cleanup steps | 4 |

## Events, alerts and incidents

| Method | Path | Purpose | Step |
|---|---|---|---|
| `GET` | `/events` | Normalized events. Filters: `run_id`, `event_type`, `since`, `limit` | 5 |
| `GET` | `/events/{event_id}` | One event with its raw evidence | 5 |
| `GET` | `/alerts` | Alerts. Filters: `run_id`, `rule_id` | 5 |
| `GET` | `/alerts/{alert_id}` | One alert and the events that caused it | 5 |
| `GET` | `/incidents` | Incidents | 9 |
| `GET` | `/incidents/{incident_id}` | Incident with alerts, timeline of events and risk score | 9 |

## Response actions

| Method | Path | Purpose | Step |
|---|---|---|---|
| `POST` | `/incidents/{incident_id}/responses` | Request the scenario's response action. Starts as `pending_approval` | 7 |
| `POST` | `/responses/{response_id}/approve` | Approve, execute, then verify | 7 |
| `POST` | `/responses/{response_id}/reject` | Reject | 7 |
| `GET` | `/responses/{response_id}` | Status and verification result | 7 |

## Reports, audit and auth

| Method | Path | Purpose | Step |
|---|---|---|---|
| `GET` | `/runs/{run_id}/report` | Basic experiment report for one run | 9 |
| `GET` | `/audit` | Audit trail: runs, approvals, responses, cleanup | 7 |
| `POST` | `/auth/login` | Dashboard login, returns a token | 6 |
| `GET` | `/health` | Backend health | 3 |

## Shared rules

- Errors: `{"error": "<short_code>", "detail": "<readable text>"}` with a suitable 4xx or 5xx code.
- IDs are UUIDs, except `scenario_id` and `rule_id`, which are names like `authentication_abuse`.
- Event objects follow `contracts/event.schema.json`. Scenario objects follow `contracts/scenario.schema.json`.
- A run is refused (`409`) if the lab is not running, the target is not a configured lab target, or a prerequisite fails.
