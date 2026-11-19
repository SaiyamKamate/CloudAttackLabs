# Adding a scenario

A scenario in CloudAttackLab is two files and usually a third:

1. a **scenario definition**, `scenarios/<id>.yaml`: what it does, to which target, with which limits, what evidence it expects, how it is detected, the optional response, and the cleanup;
2. a **script**, `scenarios/scripts/<id>.py`: the code that does it;
3. a **detection rule**, `rules/<rule id>.yaml`, unless an existing rule fits.

You do not touch the dashboard: it lists whatever valid scenarios it finds. You do not touch the telemetry pipeline, the Detection Engine, the incident view or the reports either; a new scenario reuses them. That is the point of the framework (project guide, section 6).

This page uses the three existing scenarios as examples. Read their files side by side with it:

| Scenario | Type | Definition | Script | Rule |
|---|---|---|---|---|
| Failed logins | application | [`authentication_abuse.yaml`](../scenarios/authentication_abuse.yaml) | [`authentication_abuse.py`](../scenarios/scripts/authentication_abuse.py) | [`repeated_failed_logins.yaml`](../rules/repeated_failed_logins.yaml) |
| Denied access | application | [`unauthorized_resource_access.yaml`](../scenarios/unauthorized_resource_access.yaml) | [`unauthorized_resource_access.py`](../scenarios/scripts/unauthorized_resource_access.py) | [`repeated_access_denied.yaml`](../rules/repeated_access_denied.yaml) |
| Shell in a container | container | [`suspicious_container_activity.yaml`](../scenarios/suspicious_container_activity.yaml) | [`suspicious_container_activity.py`](../scenarios/scripts/suspicious_container_activity.py) | [`suspicious_container_process.yaml`](../rules/suspicious_container_process.yaml) |

## How much work is it?

The project guide (section 6.3) gives these **planning estimates** once the framework exists. They are estimates, not measurements and not guarantees: actual effort depends on the target, what telemetry is available, the response, testing and cleanup, and a scenario that needs a new integration can take much longer.

| Scenario type | Indicative effort (project guide) |
|---|---|
| Simple application-level scenario | about 0.5–1 developer day |
| Application scenario with custom telemetry | about 1–2 developer days |
| Container-level scenario | about 2–3 developer days |
| Kubernetes-specific scenario | about 2–4 developer days, after Kubernetes support exists (it does not) |
| Cloud-specific scenario | about 3–5 developer days, after cloud credentials, logging and adapters exist (they do not) |

In this repository, "simple" means a scenario that only uses what already exists: an existing target (ShopCloud or the test container), event types ShopCloud already logs, existing pre-flight checks, cleanup steps and, if any, an existing response action. Anything else needs code in the backend (see [What needs code](#what-needs-code)).

## Observation scenarios

A scenario of type `observation` attacks nothing. Its run sends nothing to the lab: for `execution.parameters.duration_seconds` (1 to `timeout_seconds`) it collects the lab's telemetry the same way attack runs do, then checks every rule in `rules/`, each with the threshold and window of the scenario that uses it. It must have no `execution.script`, no `detection` and no `response`; it still needs `prerequisites`, `expected_telemetry` and `cleanup`. The shipped one, `scenarios/benign_baseline.yaml`, watches for 600 s while normal traffic is sent from outside, so every alert it raises is a false positive. There is no reason to write another unless you need a different length.

## Step 1: the scenario definition

Create `scenarios/<id>.yaml`. The file name (without `.yaml`) must equal its `id`. The loader (`backend/app/schemas/scenario.py`) checks every field when the file is read. A file that fails any check is **skipped and logged**, never run; the dashboard simply does not list it. Unknown fields are refused.

### Fields

| Field | Required | Rules | Example |
|---|---|---|---|
| `schema_version` | no | default `"1.0"` | `"1.0"` |
| `id` | yes | lowercase letters, digits, `_`; starts with a letter; equals the file name | `authentication_abuse` |
| `name` | yes | at least 3 characters; shown on the dashboard | `Repeated Failed Login Attempts` |
| `category` | yes | at least 3 characters | `authentication` |
| `description` | yes | at least 10 characters | |
| `version` | no | `X.Y.Z`, default `1.0.0`; recorded with every run | `1.0.0` |
| `target` | yes | must be a configured lab target: `shopcloud`, or `test_container` for a container scenario | `shopcloud` |
| `type` | yes | `application`, `container` or `observation`. A `container` scenario must target `test_container`; the others must not. An `observation` scenario runs no script and only watches (see [Observation scenarios](#observation-scenarios)) | `application` |
| `requires` | no | list of `login_endpoint`, `protected_resource`, `rate_limit_control`, `container_target`, `falco`. Descriptive: shown in the definition, not checked | `[login_endpoint, rate_limit_control]` |
| `prerequisites` | yes, at least one | list of `{check, detail}`. See [Pre-flight checks](#pre-flight-checks) | `- check: http_health` |
| `execution.script` | yes, except `observation` | a file name only (`^[a-z][a-z0-9_]*\.py$`), inside `scenarios/scripts/` | `authentication_abuse.py` |
| `execution.timeout_seconds` | no | 1–600, default 60. The run fails if the script takes longer | `60` |
| `execution.parameters` | no | flat map of `bool`, `int`, `float` or `str`. `attempts` must be 1–200; `interval_seconds` at least 0.1 | `{username: lab_user, attempts: 20, interval_seconds: 1}` |
| `expected_telemetry` | yes, at least one | list of `{source, event_type, min_count}`. A run that collects fewer ends `incomplete` | `{source: application, event_type: authentication_failure, min_count: 10}` |
| `detection.rule` | yes, except `observation` (which must have no `detection`) | the id of a file in `rules/` | `repeated_failed_logins` |
| `detection.threshold` | yes | at least 1 | `10` |
| `detection.window_seconds` | no | 1–3600, default 60 | `60` |
| `response` | no | see [The response](#the-response-optional). Leave it out if there is no action with a measurable check | |
| `cleanup.enabled` | yes | must be `true`; a scenario without cleanup is refused | `true` |
| `cleanup.steps` | yes, at least one | run in order. See [Cleanup steps](#cleanup-steps) | `[disable_rate_limit, reset_target_state]` |

`source` is one of `application`, `docker`, `falco`, `platform`. `event_type` is one of the types in the [event schema](architecture.md#the-common-event-schema). For ShopCloud events, the event type comes from ShopCloud's `event` name; see [Telemetry sources and normalization](architecture.md#telemetry-sources-and-normalization).

### Pre-flight checks

Run before every run, in order. The first one that fails refuses the run with 409 `preflight_failed`, and nothing is sent.

| Check | For | `detail` | What it does |
|---|---|---|---|
| `http_health` | application | not used | ShopCloud's `/health` answers 200 |
| `test_account` | application | the account name | That test account can log in with its password from `LAB_TEST_ACCOUNTS` (default: the two documented accounts) |
| `container_running` | container | the container target (`test_container`) | The container exists and is running |

### The response (optional)

```yaml
response:
  action: temporary_rate_limit      # or stop_container
  requires_approval: true           # must be true; false is refused
  parameters:
    max_attempts: 5
    window_seconds: 60
  verification:
    check: login_returns_429
    description: Send follow-up login attempts and confirm ShopCloud answers 429.
```

| Action | Target | Parameters | Verification check | Undone by |
|---|---|---|---|---|
| `temporary_rate_limit` | `shopcloud` | `max_attempts` (whole number, 1–198), `window_seconds` (whole number, 1–3600); otherwise 409 `invalid_response_parameters` | `login_returns_429` | `disable_rate_limit` or `reset_target_state`, or lab reset |
| `stop_container` | `test_container` | none (the container comes from the incident's events) | `container_not_running` | `recreate_test_container`, or lab reset |

`isolate_container` is accepted by the loader, but it is not implemented: requesting it gives 409 `action_not_supported`.

The project guide's rule: only add a response if it has a measurable check after it; otherwise leave `response` out. `unauthorized_resource_access` does that: it has no response, and instead names a post-run check in its parameters:

```yaml
execution:
  parameters:
    verification_check: access_remains_denied
```

After the run, the engine repeats that check once and stores the result in the run's summary. `access_remains_denied` is the only post-run check there is.

### Cleanup steps

| Step | What it does |
|---|---|
| `disable_rate_limit` | Turns ShopCloud's rate limit off (`POST /lab/rate-limit` with `enabled: false`) |
| `reset_target_state` | ShopCloud's `POST /lab/reset`: seeded data back, every session removed, rate limit off |
| `recreate_test_container` | A fresh test container (undoes `stop_container`) |

Cleanup runs when the user clicks **Cleanup**, and automatically when a run fails. **Careful:** a step name the engine does not know is not refused when the file is loaded. It is logged as a warning ("unknown cleanup step ... skipping") and skipped when cleanup runs. Check the spelling, and check the backend log after your first cleanup.

## Step 2: the script

### Application scenario

The engine runs the script as its own process, with Python's isolated mode, in `scenarios/scripts/`:

```
python -I <script> <target URL> '<execution.parameters as JSON>'
```

- The **target URL** comes from the lab configuration (`SHOPCLOUD_URL`, checked to be inside the lab). The script must send requests only there. It never chooses a host.
- Use the **standard library only** (`urllib.request`, `json`, `time`): the scripts run with the backend's Python, and `authentication_abuse.py` shows that nothing else is needed.
- Respect the parameters: `attempts`, `interval_seconds`. Their bounds are checked by the loader, so a script that reads them from its parameters stays bounded.
- Never use a real password. The engine never gives the script a password: `authentication_abuse.py` uses a fixed wrong one. `unauthorized_resource_access.py` needs to log in, so it reads the test account's password from `LAB_TEST_ACCOUNTS` itself, falling back to the documented test password.
- At the end, **print one JSON object on the last line of stdout**: the run's summary. It is stored with the run and shown on the dashboard and in the report.
- Count requests that got no answer at all in a field called `errors`. If `errors` > 0, the run ends `incomplete`.
- **Exit with code 0** when the scenario ran as intended. A non-zero exit code makes the run `failed` (and cleanup runs); the summary is still kept if you printed it. `unauthorized_resource_access.py` exits non-zero on purpose when access that should be refused was granted, with `"access_control_failed": true` in its summary.

Start from a copy of `authentication_abuse.py`; it is 70 lines.

### Container scenario

A container scenario's script is not a separate process. The engine loads it and calls its `run(container)` function, bounded by `timeout_seconds`:

```python
def run(container):
    result = container.run_marker()
    return {
        "scenario": "my_container_scenario",
        "target": container.target,
        "container": container.name,
        "command": " ".join(result.command),
        "exit_code": result.exit_code,
        "output": result.output[:500],
    }
```

The handle has exactly one action, `run_marker()`, which runs the fixed command `sh -c "id; : cloudattacklab-scenario-marker"` in the test container. The script cannot choose another command or another container, and it gets no parameters. Return a dict with `exit_code`; anything other than 0 fails the run. A container scenario that does something else than this one command needs a new, fixed action in the backend (see below).

## Step 3: the detection rule

If an existing rule counts the right events grouped the right way, name it in `detection.rule` and you are done. Otherwise add `rules/<rule id>.yaml`. The file name must equal the `id`.

```yaml
id: repeated_access_denied
event_type: authorization_denied   # which events to count
group_by:                          # one field or a list
  - username
  - raw.path
match:                             # optional: only events with these values
  raw.status: 403
severity: medium                   # info, low, medium, high, critical
title: Repeated access denied for one user
description: >
  One logged-in user was refused the same protected resource enough times...
default_threshold: 5               # fallbacks; the scenario's detection wins
default_window_seconds: 60
```

`group_by` and `match` may use `source_ip`, `username`, `resource`, or `raw.<name>` for a field of the original log record. The rule fires when one group has `threshold` events within `window_seconds`, and makes one alert per run and group. An invalid rule file is skipped and logged; a scenario whose rule is missing runs normally but raises no alert (the backend logs "detection rule ... not found").

Choose the threshold so that the scenario crosses it clearly and normal use does not: `authentication_abuse` sends 20 failed logins against a threshold of 10. Remember that all ShopCloud traffic shares one source IP (see [known limitations](known-limitations.md#every-source-ip-is-the-docker-gateway-address)), so a rule grouped by `source_ip` will also count anyone else using the shop during the run.

## Step 4: tests

Add unit tests in `backend/tests/` (they need no Docker):

- The definition loads: copy `test_loads_the_shipped_scenario` in `test_scenario_engine.py`. That test, and `test_both_scenarios_are_listed` in `test_unauthorized_resource_access.py`, compare the list of scenarios exactly, so add your id there too.
- The rule loads and fires at the threshold, not one below, and not when events are spread over more than the window: see `test_detection.py` and the denial tests in `test_unauthorized_resource_access.py` ("four denials raise no alert", "five denials raise one alert").
- Events that must not count do not count (for example "401 without a token does not count").
- If the script can fail on purpose, a test for that summary.

Then an integration test against the real lab, like `test_scenario_integration.py` or `test_unauthorized_access_integration.py`, marked `@pytest.mark.integration`. Run them:

```bash
pytest backend/tests          # unit tests, about 25 s
pytest -m integration         # starts the real lab with Docker; several minutes
```

(On the test machine, `pytest backend/tests` gave 246 passed and 8 deselected, and `pytest shopcloud/tests` 22 passed. The integration tests were not run for this guide; the November 2026 audit ran them, 8 passed.)

Finally run the scenario from the dashboard, check that it ends `completed`, that its alert has the events you expect, that its response (if any) ends `verified`, and that **Cleanup** brings the lab back: run `python3 tools/check_shopcloud.py http://127.0.0.1:8081` afterwards, which must still print `14/14 checks passed`.

## Step 5: cleanup and reset

Before you finish, check by hand that:

- every change the scenario or its response makes is undone by its cleanup steps;
- a lab **Reset** also undoes it (it recreates every container and calls ShopCloud's reset);
- a failed run leaves nothing behind (cleanup runs automatically then).

## A template

The files below are a small, complete example: wrong-password logins against the *restricted* account, detected per account rather than per IP. They are **not in the repository**. They were checked with the real loaders (the scenario and rule both load, and the three shipped scenarios still load beside them), but they were not run end to end against the lab.

`scenarios/restricted_login_abuse.yaml`:

```yaml
schema_version: "1.0"
id: restricted_login_abuse
name: Failed Logins Against the Restricted Account
category: authentication
description: >
  Sends a bounded number of wrong-password logins for the restricted test
  account and checks that the repeated failures are detected.
version: 0.1.0
target: shopcloud
type: application

requires:
  - login_endpoint

prerequisites:
  - check: http_health
  - check: test_account
    detail: lab_restricted

execution:
  script: restricted_login_abuse.py
  timeout_seconds: 60
  parameters:
    username: lab_restricted
    attempts: 12
    interval_seconds: 1

expected_telemetry:
  - source: application
    event_type: authentication_failure
    min_count: 6

detection:
  rule: repeated_failed_logins_per_user
  threshold: 6
  window_seconds: 60

cleanup:
  enabled: true
  steps:
    - reset_target_state
```

`rules/repeated_failed_logins_per_user.yaml`:

```yaml
id: repeated_failed_logins_per_user
event_type: authentication_failure
group_by: username
severity: medium
title: Repeated failed logins for one account
description: >
  One account had enough failed logins within the window to look like someone
  guessing its password.
default_threshold: 6
default_window_seconds: 60
```

`scenarios/scripts/restricted_login_abuse.py`: a copy of `authentication_abuse.py` with `"scenario": "restricted_login_abuse"` in its summary. It already reads `username`, `attempts` and `interval_seconds` from its parameters.

The backend reads `scenarios/` and `rules/` from read-only mounts each time it needs them, so a new file is picked up without restarting it.

## What needs code

These are not configuration; they need changes in `backend/app`, with tests:

| You want | Where |
|---|---|
| A new target (another application) | `config.py` (`lab_targets`), `lab/docker-compose.lab.yml`, `lab_manager/target_guard.py`, and a target client like `lab_manager/target.py` |
| A new pre-flight check | `PrerequisiteCheck` in `schemas/scenario.py` and `scenario_engine/preflight.py` |
| A new kind of ShopCloud event | ShopCloud's log, the interface ([shopcloud-interface.md](shopcloud-interface.md)) and `EVENT_TYPE_MAP` in `telemetry/normalize.py` |
| A new telemetry source | a `TelemetrySource` in `backend/app/telemetry/` and its normalizer |
| A new response action | `ResponseActionType` in `schemas/scenario.py`, `response_manager/service.py`, a check in `response_manager/verification.py`, and how it is reverted (`response_manager/revert.py`, cleanup) |
| A new post-run check | `CHECKS` in `scenario_engine/verification.py` |
| A new cleanup step | `_cleanup_step` in `scenario_engine/service.py` |
| A new container action | a new fixed command in `lab_manager/container.py`, an adapter method and a handle method. Never a command taken from a file or a request |

Whatever you add must keep the safety rules in [safety.md](safety.md): lab-only targets, bounded parameters, fixed commands, approval for responses, and cleanup.
