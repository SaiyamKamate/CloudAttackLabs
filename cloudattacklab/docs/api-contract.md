# CloudAttackLab backend API (v1)

The React dashboard talks to the backend only through these endpoints, all through `frontend/src/api.js`. This page was checked against the running backend's OpenAPI description (`GET /openapi.json`) and the code on 17 November 2026: it lists every route the backend has, and nothing else.

- Base URL: `http://127.0.0.1:8000/api/v1`. The dashboard calls the same paths on its own origin (`http://127.0.0.1:5173/api/v1/...`) and nginx forwards them.
- Bodies are JSON. Times are UTC, ISO 8601.
- IDs are UUIDs, except `scenario_id` and `rule_id`, which are names like `authentication_abuse`.
- There is no authentication. Every request is accepted from anyone who can reach `127.0.0.1:8000`, and every audit row has actor `anonymous`. A dashboard login (`POST /auth/login`) was planned but is **not built**; that path does not exist (404).
- The OpenAPI description itself is served at `http://127.0.0.1:8000/openapi.json`. (FastAPI's interactive pages `/docs` and `/redoc` are also served by default; they are not part of this contract.)

## Errors

Every error has this body:

```json
{"error": "<short_code>", "detail": "<readable text>"}
```

| Status | When | `error` codes |
|---|---|---|
| 404 | The id does not exist | `lab_not_found`, `scenario_not_found`, `run_not_found`, `event_not_found`, `alert_not_found`, `incident_not_found`, `response_not_found`; `not_found` for an unknown path |
| 405 | Wrong method on a known path | `method_not_allowed` |
| 409 | The request is valid but the state does not allow it | see each endpoint |
| 422 | A malformed id, body or query (`validation_error`); an unknown lab target on create (`unknown_target`) | |
| 500 | A scenario names a script that is missing or outside `scenarios/scripts/` | `script_not_found`, `script_outside_scripts_dir` |
| 502 | Docker failed while starting, stopping, resetting or reading the lab | `lab_start_failed`, `lab_stop_failed`, `lab_reset_failed`, `lab_runtime_error` |
| 503 | The backend cannot reach PostgreSQL | `database_unavailable` |

## Health

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/health` | | `200 {"status": "ok", "database": "ok"}`, or 503 `database_unavailable` |

## Labs

| Method | Path | Body | Returns | 409 errors |
|---|---|---|---|---|
| `POST` | `/labs` | `{"name": "...", "target": "shopcloud"}`. `name` 1–100 characters; `target` defaults to `shopcloud` | `201` Lab | `lab_name_taken`. An unknown target is 422 `unknown_target` |
| `GET` | `/labs` | | `200` list of Lab, oldest first, with their **stored** status | |
| `GET` | `/labs/{lab_id}` | | `200` LabDetail. Reading a lab compares its stored status with Docker and corrects it (`running`, `degraded`, `stopped`); the correction is audited | |
| `POST` | `/labs/{lab_id}/start` | | `200` Lab, once the lab's containers are healthy | `target_url_refused`, `lab_conflict` (another lab is running or starting), `lab_busy` (another lab operation is in progress) |
| `POST` | `/labs/{lab_id}/stop` | | `200` Lab | `lab_not_running` (lab is `created` or `stopped`), `lab_busy` |
| `POST` | `/labs/{lab_id}/reset` | | `200` Lab. Recreates every lab container, calls ShopCloud's `/lab/reset`, reverts applied responses. Allowed on a `running` or `degraded` lab | `lab_not_running` (any other status), `run_in_progress` (a run is active in this lab), `target_url_refused`, `lab_busy` |

**Lab:** `id`, `name`, `target`, `environment` (`docker`), `status`, `created_at`, `updated_at`.

**LabDetail:** Lab plus

| Field | Meaning |
|---|---|
| `status_detail` | Why the lab is `degraded` or `stopped` (names the containers that are down), or a note; `null` if all is well |
| `active_run` | `{id, scenario_id, status}` of the run in progress, or `null` |
| `containers` | List of `{service, name, container_id, state, health}` |
| `target_health` | `{reachable, healthy, status_code, body, error}` from ShopCloud's `/health` |
| `target_url` | `{url, allowed, error}`: the configured `SHOPCLOUD_URL` and whether it points into the lab |
| `telemetry` | `{source, loki_url, reachable, error}`: `docker` or `loki`; Loki's reachability is checked only for `loki` |
| `falco` | `{enabled, state, health, healthy}` |

Lab statuses: `created`, `starting`, `running`, `degraded` (some lab container is down; stored when a read finds it), `stopped`, `error`.

## Scenarios and runs

| Method | Path | Body | Returns | 409 errors |
|---|---|---|---|---|
| `GET` | `/scenarios` | | `200` list of `{id, name, category, description, version, target, type}`, one per **valid** file in `scenarios/`; `type` is `application`, `container` or `observation` | |
| `GET` | `/scenarios/{scenario_id}` | | `200` the full scenario definition (format: `contracts/scenario.schema.json`) | |
| `POST` | `/labs/{lab_id}/runs` | `{"scenario_id": "..."}`, nothing else | `201` Run with status `pending`. The run continues in the background | `lab_not_running`, `target_mismatch`, `unknown_target`, `target_url_refused`, `run_in_progress`, `preflight_failed` (names the check) |
| `GET` | `/runs/{run_id}` | | `200` Run | |
| `POST` | `/runs/{run_id}/cleanup` | | `200` Run, now `cleaned_up` | `run_in_progress` (this run, or another run in the same lab, is active), `unknown_target` |

**Run:** `id`, `lab_id`, `scenario_id`, `scenario_version`, `status`, `parameters`, `started_at`, `finished_at`, `error`, `summary` (the script's JSON summary, plus `verification` when the scenario names a post-run check).

Run statuses: `pending`, `running`, `completed`, `incomplete`, `failed`, `cleaned_up`. `completed` means the script finished, every `expected_telemetry` item of the scenario was collected and the script reported no request errors; otherwise a finished run is `incomplete`. `error` says why a run is `failed` or `incomplete`.

## Events and alerts

| Method | Path | Query | Returns |
|---|---|---|---|
| `GET` | `/events` | `run_id`, `event_type`, `since` (date-time), `limit` (1–1000, default 100) | `200` list of Event, oldest first |
| `GET` | `/events/{event_id}` | | `200` Event plus `raw`: the original record |
| `GET` | `/alerts` | `run_id`, `rule_id` | `200` list of Alert, oldest first |
| `GET` | `/alerts/{alert_id}` | | `200` Alert plus `events`: the events that caused it, oldest first |

**Event:** the fields of the common event schema (`contracts/event.schema.json`; see [architecture](architecture.md#the-common-event-schema)): `event_id`, `timestamp`, `source`, `event_type`, `severity`, `source_ip`, `resource`, `message`, `raw_event_reference`, `schema_version`, `scenario_id`, `lab_id`, `run_id`, `username`, `container_id`, `rule_id`, `correlation_key`.

**Alert:** `id`, `rule_id`, `severity`, `title`, `description`, `lab_id`, `run_id`, `incident_id`, `event_count`, `first_event_at`, `last_event_at`, `created_at`.

## Incidents

| Method | Path | Query | Returns | 409 errors |
|---|---|---|---|---|
| `GET` | `/incidents` | `run_id` | `200` list of Incident, oldest first (the dashboard shows them newest first) | |
| `GET` | `/incidents/{incident_id}` | | `200` Incident plus `alerts`, `responses` and `timeline` | |
| `POST` | `/incidents/{incident_id}/close` | no body | `200` Incident, now `closed` | `incident_closed` (already closed), `response_in_progress` (a response is still pending approval) |

**Incident:** `id`, `lab_id`, `run_id`, `title`, `status` (`open` or `closed`), `created_at`, `correlation_key`, `risk_score` (0–100), `risk_explanation`.

**Timeline entry:** `time`, `type` (one of `scenario_started`, `event`, `alert_fired`, `scenario_finished`, `response_requested`, `response_approved`, `response_rejected`, `response_executed`, `response_verified`, `response_failed`, `response_reverted`, `cleanup`, `incident_closed`), `description`, `evidence` = `{kind, id, href, raw_event_reference, source, rule_id}`, where `kind` is `run`, `event`, `alert`, `response` or `audit` and `href` is the API path of the evidence.

## Response actions

| Method | Path | Body | Returns | 409 errors |
|---|---|---|---|---|
| `POST` | `/incidents/{incident_id}/responses` | none: the action, its parameters and its target come from the scenario | `201` Response with status `pending_approval`. Nothing is changed yet | `incident_closed`, `response_already_open`, `no_response_defined`, `no_scenario_run`, `action_not_supported`, `invalid_response_parameters`, `unknown_target`, `no_lab`, `lab_not_running`, `target_refused` (the container is not the lab's test container), `no_container`, `several_containers`, `container_not_found`, `container_not_running`, `no_lab_adapter` |
| `POST` | `/responses/{response_id}/approve` | none | `200` Response after executing and verifying: `verified` or `failed` | `response_not_pending`, `response_out_of_date` (the scenario's response changed since the request), `lab_not_running`, `target_refused`, and the request-time checks again |
| `POST` | `/responses/{response_id}/reject` | none | `200` Response, `rejected`. Nothing is changed | `response_not_pending` |
| `GET` | `/responses/{response_id}` | | `200` Response | |

**Response:** `id`, `incident_id`, `action`, `parameters`, `status`, `requested_at`, `decided_at`, `decided_by`, `executed_at`, `verified_at`, `verification_result` (what was sent and what came back).

Response statuses: `pending_approval`, `approved`, `rejected`, `executed`, `verified`, `failed`, `reverted`. Actions: `temporary_rate_limit` and `stop_container`. (`isolate_container` is accepted in a scenario file but gives `action_not_supported`.)

## Reports and audit

| Method | Path | Query | Returns |
|---|---|---|---|
| `GET` | `/runs/{run_id}/report` | | `200` the run report (JSON): `report_version`, `generated_at`, `lab`, `scenario`, `run`, `script_summary`, `telemetry` (expected and collected), `detection` (`rule`, `threshold`, `window_seconds` of an attack scenario's rule, null for an observation run, and `rules`: every rule the run was checked against, with `threshold`, `window_seconds` and `taken_from`), `alerts` (with `threshold_crossed_at` and `detection_latency_seconds`, the time from the threshold-crossing event to the alert), `incidents`, `responses`, `post_run_verification`, `cleanup` |
| `GET` | `/audit` | `object_type`, `object_id`, `action`, `limit` (1–1000, default 100) | `200` list of `{id, timestamp, actor, action, object_type, object_id, detail}`, **newest first** |

Audit actions: `lab.start`, `lab.stop`, `lab.reset`, `lab.status_corrected`, `run.start`, `run.finish`, `run.failure`, `cleanup`, `response.requested`, `response.approved`, `response.rejected`, `response.executed`, `response.verified`, `response.failed`, `response.reverted`, `incident.closed`.

## History

The endpoints were added in this order (build steps from the README's history): health and labs in step 3; scenarios and runs in step 4; events and alerts in step 5; responses and audit in step 7; incidents and the run report in step 9; `POST /incidents/{id}/close`, the `incomplete` run status, `degraded` labs, `active_run` and `target_url` in the fixes for the [November 2026 audit](audit-2026-11.md).
