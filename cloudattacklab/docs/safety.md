# Safety controls

CloudAttackLab runs attack scenarios on purpose, so it has to be careful about where they go and what they can do. This page lists each safety control, what it guarantees, and **where in the code it is enforced**, so you can check it yourself or keep it when you change the code. The rules come from the project guide, section 13 ("Safety, isolation and operational controls").

Line numbers are those of 17 November 2026. What these controls do **not** cover is in [known limitations](known-limitations.md).

| # | Control | Enforced in |
|---|---|---|
| 1 | [Lab-only targets](#1-lab-only-targets) | `schemas/scenario.py`, `scenario_engine/engine.py`, `lab_manager/target_guard.py`, `startup.py`, `scenario_engine/service.py`, `lab_manager/service.py` |
| 2 | [Bounded parameters](#2-bounded-parameters) | `schemas/scenario.py`, `scenario_engine/runner.py`, `response_manager/service.py`, the compose files |
| 3 | [Fixed commands](#3-fixed-commands) | `lab_manager/container.py`, `lab_manager/docker_adapter.py`, `scenario_engine/runner.py`, the API schemas |
| 4 | [Approval for responses](#4-approval-for-responses) | `schemas/scenario.py`, `response_manager/service.py`, `response_manager/router.py` |
| 5 | [Protected containers](#5-protected-containers) | `lab_manager/container.py`, `lab_manager/docker_adapter.py`, `response_manager/service.py` |
| 6 | [Cleanup](#6-cleanup) | `schemas/scenario.py`, `scenario_engine/service.py`, `lab_manager/service.py`, `response_manager/revert.py` |
| 7 | [Audit log](#7-audit-log) | `audit.py` and every service that changes something |
| 8 | [No secrets in logs](#8-no-secrets-in-logs) | `shopcloud/shop/main.py`, `lab_manager/docker_adapter.py`, `scenarios/scripts/`, `startup.py` |

All backend paths are under `backend/app/`.

## 1. Lab-only targets

**Guarantee.** A scenario, a pre-flight check, a verification and a response can only send requests to the lab's own ShopCloud, and only act on the lab's own test container. A target cannot be chosen by a request.

**How it is enforced.**

- **Targets come from configuration, never from a request.** The list of targets is built once from settings: `shopcloud` → `SHOPCLOUD_URL`, and `test_container` ([`config.py`](../backend/app/config.py)). Starting a run takes only a `scenario_id` (`RunCreate`, `extra="forbid"`, in [`scenario_engine/api_schemas.py`](../backend/app/scenario_engine/api_schemas.py)); the response endpoints take no body at all ([`response_manager/router.py`](../backend/app/response_manager/router.py)).
- **Scenario files can only name a configured target.** The loader refuses any other target ([`schemas/scenario.py#L170`](../backend/app/schemas/scenario.py#L170)), and the engine refuses a container scenario that does not name a container target, or an application scenario that does ([`scenario_engine/engine.py`](../backend/app/scenario_engine/engine.py), `_check_target_kind`). A lab can only be created for a configured target (422 `unknown_target`, [`lab_manager/service.py`](../backend/app/lab_manager/service.py), `create`).
- **The target URL must point into the lab.** `check_target_url` ([`lab_manager/target_guard.py#L99`](../backend/app/lab_manager/target_guard.py#L99)) accepts `SHOPCLOUD_URL` only if it is `http`/`https` with no user name or password, and either a loopback address on exactly the port that `lab/docker-compose.lab.yml` publishes for ShopCloud (`127.0.0.1:8081`), or the name of a container that belongs to the `cloudattacklab-lab` Compose project and runs the `shopcloud` service. Any other IP address or host is refused. The check never sends anything to the URL.
- **The check runs everywhere it matters**: at backend startup, which logs an error ([`startup.py#L27`](../backend/app/startup.py#L27)); on every lab start and reset ([`lab_manager/service.py`](../backend/app/lab_manager/service.py), `_require_target_url`); before every run (409 `target_url_refused`, [`scenario_engine/service.py`](../backend/app/scenario_engine/service.py), `start_run`); and before **every single request** to the target, because every target client is wrapped in `GuardedTargetClient` ([`lab_manager/target_guard.py#L190`](../backend/app/lab_manager/target_guard.py#L190)). A refused URL gets no request and no lab token. The Lab page shows the reason.
- **The script gets the URL from the engine.** An application script receives the checked target URL as its first argument ([`scenario_engine/runner.py#L45`](../backend/app/scenario_engine/runner.py#L45)); it has no other way to learn where the lab is.
- **Docker is only driven for the lab.** The Docker adapter always uses `docker compose -f lab/docker-compose.lab.yml` for compose commands, and filters `docker events` on the lab project and the test-target label ([`lab_manager/docker_adapter.py`](../backend/app/lab_manager/docker_adapter.py)).
- **The lab is on this machine only.** Every published port is bound to `127.0.0.1` in the three compose files, and the test container has no network at all (`network_mode: none`).

**Tests:** `backend/tests/test_target_url_guard.py`, `test_scenario_engine.py` (`test_scenario_targeting_outside_the_lab_is_skipped`).

## 2. Bounded parameters

**Guarantee.** No scenario can send an unlimited number of requests, run forever, or use unlimited resources.

**How it is enforced.**

- **Scenario limits** ([`schemas/scenario.py`](../backend/app/schemas/scenario.py)): `attempts` must be a whole number from 1 to 200 (`MAX_ATTEMPTS`, [line 18](../backend/app/schemas/scenario.py#L18)); `interval_seconds` at least 0.1; `timeout_seconds` from 1 to 600; detection windows from 1 to 3600 seconds; parameters can only be flat booleans, numbers or strings (`_bounded`, [line 72](../backend/app/schemas/scenario.py#L72)). A file breaking any limit is not loaded.
- **Timeouts are enforced, not just declared.** An application script runs as a separate process killed after `timeout_seconds` (`subprocess.run(..., timeout=...)`, [`scenario_engine/runner.py#L45`](../backend/app/scenario_engine/runner.py#L45)); a container script is abandoned after the same time. Either way the run fails and cleanup runs.
- **One run per lab, one lab at a time.** A second run in the same lab is refused with `run_in_progress` (`_acquire`, [`scenario_engine/service.py#L572`](../backend/app/scenario_engine/service.py#L572)); a second lab cannot start while one is running (`lab_conflict`).
- **Response parameters are checked again** when a response is requested: `max_attempts` from 1 to 198 and `window_seconds` from 1 to 3600, whole numbers (409 `invalid_response_parameters`, [`response_manager/service.py`](../backend/app/response_manager/service.py), `_planned_action`).
- **Container resource limits** in `lab/docker-compose.lab.yml`: ShopCloud 256 MB and 0.5 CPU; the test container 32 MB, 0.1 CPU and a read-only file system; Falco 512 MB. The platform and monitoring containers have memory limits too.

## 3. Fixed commands

**Guarantee.** Nothing executes a command, a script or a file name that came from a request or from data. The only command ever run inside a container is a constant in the code.

**How it is enforced.**

- **The container scenario's command is a constant**: `MARKER_COMMAND = ("sh", "-c", "id; : cloudattacklab-scenario-marker")` ([`lab_manager/container.py#L22`](../backend/app/lab_manager/container.py#L22)). The adapter's `exec_marker` runs exactly that ([`lab_manager/docker_adapter.py#L102`](../backend/app/lab_manager/docker_adapter.py#L102)); no adapter method accepts a command.
- **A container script gets a handle, not the adapter.** `ContainerHandle` ([`scenario_engine/runner.py#L111`](../backend/app/scenario_engine/runner.py#L111)) has one method, `run_marker()`, and uses `__slots__` so nothing else can be attached to it. The script cannot choose the container either.
- **Scripts are files in one folder.** A script name must match `^[a-z][a-z0-9_]*\.py$` (no path), and `resolve_script` checks that the resolved path is inside `scenarios/scripts/` ([`scenario_engine/runner.py#L30`](../backend/app/scenario_engine/runner.py#L30)). It is run as `python -I <script> <url> <parameters>`: a list of arguments, never a shell command line, in Python's isolated mode.
- **Scenario and rule files are mounted read-only** into the backend (`docker-compose.yml`), as are the lab definition and ShopCloud's source.
- **Docker commands are fixed.** The adapter builds every Docker command from constants plus values it reads from Docker or the configuration (`up`, `stop`, `ps`, `logs`, `exec <the marker>`, `events`, `inspect`, `stop <test container>`, `up --force-recreate <service>`), always as an argument list ([`lab_manager/docker_adapter.py`](../backend/app/lab_manager/docker_adapter.py)).

## 4. Approval for responses

**Guarantee.** No response action changes anything until a person approves it, and what is executed is exactly what the scenario defines.

**How it is enforced.**

- **Approval cannot be turned off.** A scenario with `requires_approval: false` is refused by the loader (`_approval_is_mandatory`, [`schemas/scenario.py#L107`](../backend/app/schemas/scenario.py#L107)).
- **Requesting changes nothing.** `request` creates a row with status `pending_approval` and does nothing else ([`response_manager/service.py#L84`](../backend/app/response_manager/service.py#L84)). It is refused if the incident is closed (`incident_closed`) or already has an open response (`response_already_open`).
- **The action comes from the scenario, not the caller.** The request has no body; the action and its parameters are read from the scenario's `response` section (`_planned_action`), and for `stop_container` the container comes from the incident's own events.
- **Only a pending response can be approved or rejected** (`_pending`, 409 `response_not_pending`, [`response_manager/service.py#L279`](../backend/app/response_manager/service.py#L279)). So a rejected response cannot be approved later, and a response cannot be approved twice.
- **Approve re-checks before it acts** ([`response_manager/service.py#L125`](../backend/app/response_manager/service.py#L125)): the lab must be running, and if the scenario's `response` section changed after the request, approval is refused with `response_out_of_date` and must be requested again.
- **Reject changes nothing** ([`response_manager/service.py#L115`](../backend/app/response_manager/service.py#L115)).
- **Every response is verified and reversible.** After executing, the response is checked (`login_returns_429`, `container_not_running`) and the result stored; cleanup and lab reset undo it and mark it `reverted`.
- **Not covered:** there is no login, so the system cannot tell who approves. See [known limitations](known-limitations.md#no-dashboard-login).

**Tests:** `backend/tests/test_responses_api.py`, `test_response_integration.py`.

## 5. Protected containers

**Guarantee.** `stop_container` and the container scenario can only touch the lab's test container. ShopCloud, Falco and the platform's own containers (backend, dashboard, PostgreSQL), and any container of another Compose project, are refused.

**How it is enforced.**

- `check_test_container` ([`lab_manager/container.py#L65`](../backend/app/lab_manager/container.py#L65)) accepts a container only if it has the label `cloudattacklab.role=test-target` **and** belongs to the lab's Compose project (`cloudattacklab-lab`, read from the lab compose file's `name:`). Only `test_container` has that label (`lab/docker-compose.lab.yml`).
- It is checked **when the response is requested** (`_check_stoppable`, [`response_manager/service.py#L252`](../backend/app/response_manager/service.py#L252)) and **again right before** `docker stop`, inside the adapter (`stop_test_container`, [`lab_manager/docker_adapter.py#L140`](../backend/app/lab_manager/docker_adapter.py#L140)). The exec of the marker command is checked the same way (`exec_marker`).
- The November 2026 audit tried to stop ShopCloud, PostgreSQL, the backend and the dashboard directly and through edited incident evidence; every attempt was refused and nothing was stopped.
- **Not covered:** labels can be set by anyone who can create containers on this Docker engine. See [known limitations](known-limitations.md#stop_container-trusts-docker-labels).

## 6. Cleanup

**Guarantee.** Every scenario has a cleanup, it runs automatically when a run fails, and a lab reset brings everything back to the starting point.

**How it is enforced.**

- **No scenario without cleanup.** `cleanup.enabled` must be `true` and there must be at least one step, or the file is refused (`_cleanup_is_mandatory`, [`schemas/scenario.py#L119`](../backend/app/schemas/scenario.py#L119)).
- **Automatic on failure.** A failed or timed-out run runs its cleanup steps automatically (`_run_cleanup(..., trigger="auto")`, [`scenario_engine/service.py#L344`](../backend/app/scenario_engine/service.py#L344)).
- **Never during another run.** Manual cleanup and lab reset are refused with `run_in_progress` while a run is active in the lab (`refuse_while_run_active`, [`lab_manager/service.py#L39`](../backend/app/lab_manager/service.py#L39)), so they cannot break a run.
- **Reset restores the baseline, not just records.** Reset recreates every lab container (`docker compose up --force-recreate`) and calls ShopCloud's `POST /lab/reset`, which restores the seeded data in one transaction, removes every session and turns the rate limit off. ShopCloud also creates and seeds its database at start-up, so a new container always starts at the baseline.
- **Responses are marked reverted** by cleanup and reset ([`response_manager/revert.py`](../backend/app/response_manager/revert.py)), and the risk score is recomputed.
- **Not covered:** an unknown cleanup step name is logged and skipped, not refused when the file is loaded; and a run interrupted by a backend restart blocks cleanup and reset in its lab. See [known limitations](known-limitations.md#scenario-files-what-is-not-checked).

## 7. Audit log

**Guarantee.** Every action that changes the lab, a run or a response leaves a row in `audit_log`, readable through `GET /api/v1/audit`.

**How it is enforced.** `audit.record` ([`audit.py#L14`](../backend/app/audit.py#L14)) is called in the same database transaction as the change it describes. Actions recorded:

| Object | Actions |
|---|---|
| Lab | `lab.start`, `lab.stop`, `lab.reset`, `lab.status_corrected` |
| Run | `run.start` (with the scenario and its parameters), `run.finish`, `run.failure`, `cleanup` (with each step's result and the responses reverted) |
| Response | `response.requested`, `response.approved`, `response.rejected` (with who decided), `response.executed` (with what was sent), `response.verified`, `response.failed`, `response.reverted` |
| Incident | `incident.closed` |

Each row has a time, the actor, the action, the object and a JSON detail. The actor is always `anonymous`, because there is no login. The response panel and the incident timeline show these rows on the dashboard.

## 8. No secrets in logs

**Guarantee.** Passwords and tokens do not appear in ShopCloud's logs, in stored events, in the audit log or in run summaries.

**How it is enforced.**

- **ShopCloud never logs passwords or tokens.** It writes one JSON line per request with fixed fields: time, service, level, event, request id, source IP, method, path, status, user name and a message ([`shopcloud/shop/main.py#L51`](../shopcloud/shop/main.py#L51), and the interface's rule in [shopcloud-interface.md](shopcloud-interface.md)). The request body and the `Authorization` and `X-Lab-Token` headers are never written. ShopCloud stores passwords only as scrypt hashes and login tokens only as a SHA-256 hash.
- **Stored events are those log lines**, so `events.raw` holds no secrets either.
- **Scenario scripts never get a real password.** `authentication_abuse.py` uses a constant wrong password. The engine passes scripts only the target URL and the scenario's parameters, which contain no passwords.
- **The lab token is not on a command line.** The Docker adapter passes `LAB_CONTROL_TOKEN` to `docker compose` through the environment, not as an argument ([`lab_manager/docker_adapter.py#L176`](../backend/app/lab_manager/docker_adapter.py#L176)), so it does not show in process lists. The backend sends it only in the `X-Lab-Token` header to the checked lab URL.
- **Audit details and verification results hold what was sent and the status codes received**, for example `POST /lab/rate-limit {"enabled":true,"max_attempts":5,"window_seconds":60}` and `401, 401, ..., 429`, never a password or token.
- **The startup warning names only published defaults.** `check_default_credentials` ([`startup.py#L45`](../backend/app/startup.py#L45)) prints the value only when it is one of the published defaults (`lab-dev-token`, `change-me-lab-token`, `change-me-locally`), which are in this repository anyway. Your own values are never printed.
- **`.env` is git-ignored** (`.gitignore`), and `.env.example` holds only placeholders.
- The November 2026 audit searched the logs of all eight containers and the `events`, `audit_log`, `response_actions` and `scenario_runs` tables and found no secrets.

**Not covered:** the test accounts' passwords and the default tokens are published in the repository on purpose (they are test values). See [known limitations](known-limitations.md#default-credentials-are-for-local-use-only).

## Falco is privileged

Falco is the one exception to "least privilege": its container runs with `privileged: true` and mounts the host's `/proc` and `/etc` (read-only) and the Docker socket. Its modern eBPF engine loads programs into the kernel to watch system calls, which needs capabilities such as `CAP_BPF` and `CAP_PERFMON`, and it reads every process on the host. Falco only observes; nothing in the lab gives it a way to change anything. Because a privileged container is powerful, Falco is **off by default** (`FALCO_ENABLED=false`) and only runs when you turn it on.
