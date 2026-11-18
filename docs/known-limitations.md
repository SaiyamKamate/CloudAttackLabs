# Known limitations

What CloudAttackLab does not do, or does only in a simple way. Each item says why, and what to keep in mind when you show or extend the lab. This list was checked against the code and against the [evaluation of 17 November 2026](evaluation-results.md) on the same day.

## Scope: what is not built

These are outside version 1.0, as planned in the project guide (section 2, "Version 1.0 scope", and section 16, "Future roadmap"):

| Not built | Status in the project guide |
|---|---|
| Kubernetes | Deferred. `LabAdapter` (`backend/app/lab_manager/adapter.py`) is the interface a Kubernetes adapter would implement |
| AWS, Azure, GCP | Deferred to later releases |
| Machine learning | Out of scope. Detection is rule-based and the risk score is a fixed formula |
| Prometheus | Optional, "add if time permits". Not added |
| Dashboard login and users | Not built (see [No dashboard login](#no-dashboard-login)). A `users` table exists but nothing uses it |
| `isolate_container` response | Accepted in a scenario file, but requesting it gives 409 `action_not_supported` |
| PDF export and sharing of reports | Not built. Reports are JSON, shown on a page and downloadable |
| AI-assisted incident analysis | Not built; planned for version 2. Incidents are explained by the fixed risk-score lines and the evidence timeline only. Nothing in the code calls a language model or any AI service |
| Attack-chain visualization | Not built; planned for version 2. Each incident has a chronological evidence timeline (run, events, alerts, responses, audit entries), but no graph linking the steps of an attack |
| Other target applications | Only ShopCloud (and the test container) can be targeted. Adding one needs code (see [adding a scenario](adding-a-scenario.md#what-needs-code)) |

## Only tested on Windows 11 with WSL 2

Everything, including the evaluation, was run on one laptop: Windows 11, Docker Engine inside WSL 2 (Ubuntu 26.04, kernel 6.18). The project guide also names Linux and macOS; neither has been tried. Docker Desktop has not been tried either, and it needs host networking turned on (see [Docker Compose is the only supported environment](#docker-compose-is-the-only-supported-environment)).

## Events are matched to a run by time

A run's events are the target's log records (and the test container's Docker events) whose timestamps fall between the run's start and the moment its script finished, plus `TELEMETRY_SINCE_GRACE_SECONDS` (3 s). Nothing in a ShopCloud request says which run sent it, so any other request to ShopCloud in that window, for example someone browsing the shop page during a demo run, is stored as one of the run's events.

The evaluation saw this happen: in each of 3 `authentication_abuse` runs with benign traffic running alongside, one benign failed login was linked to the attack alert (21 events instead of 20). Matching by time puts the benign failed login in the run, and because every request appears to come from the Docker gateway address (next section), the rule cannot tell it from the attack's own failed logins.

What limits it: one run per lab at a time; cleanup of other runs and lab reset are refused while a run is active; the window ends when the script finishes; and ShopCloud's records of the platform's own calls (`lab_reset`, `rate_limit_changed`) are never stored as a run's events. Do not use the shop by hand while a scenario runs.

## Telemetry is collected only during runs

The other side of the same design: outside a run, nothing is collected, stored or checked by the rules. CloudAttackLab is a lab for running scenarios, not a monitoring system for the shop.

To see what the rules make of normal traffic, run the `benign_baseline` scenario while the traffic is sent: it is an observation run, which sends nothing, collects for 600 s like any run and then checks every rule. In the evaluation, 10 minutes of benign traffic with near-misses (9 failed logins in 60 s, 4 denials) stored 329 events and raised 1 alert, a false positive from `suspicious_container_process` for an ordinary `date` in the test container (see [thresholds](#thresholds-and-benign-traffic-are-the-projects-own) and the [evaluation](evaluation-results.md#false-positives)).

## Every source IP is the Docker gateway address

Every ShopCloud event shows the same `source_ip`, the lab network's gateway (for example `172.22.0.1`; the exact address depends on your Docker networks). The backend runs with host networking and reaches ShopCloud on the published port `127.0.0.1:8081`. Docker forwards that port into the container through its port proxy (or NAT), so the connection ShopCloud sees comes from the gateway of the `cloudattacklab-lab` network, not from a real client address. ShopCloud deliberately ignores `X-Forwarded-For`, so the address cannot be spoofed either.

As a result, "one source IP" in `repeated_failed_logins` is always this address: the scenario's requests, the response verification and any browsing of the shop all look like one client. The rule still fires correctly for the scenario; it just cannot tell clients apart. It also means the `temporary_rate_limit` response blocks every client on this machine, including you (see [troubleshooting](troubleshooting.md#a-manual-login-gets-429-after-a-test)).

## Detection runs once, at the end of a run

Rules are evaluated once, when the scenario's script has finished, over all of the run's events. There is no streaming detection. The report's `detection_latency_seconds`, the time from the threshold-crossing event to the alert, therefore mostly measures how much of the run was left after the threshold was crossed (median 10.8 s for `authentication_abuse` with Docker logs). Measured from the run's last event instead, the alert follows in about 0.1 s with Docker logs, about 2.4 s for the container scenario (Docker events are polled until they settle) and about 4 s with Loki (the wait for late records). Neither is the latency of a real-time detector. See the [evaluation](evaluation-results.md#detection-latency).

## Thresholds and benign traffic are the project's own

The thresholds (10 failed logins in 60 s, 5 denials in 60 s, 1 process in the test container) were chosen for these scenarios, and the scenarios were written to cross them. The 100% detection rate in the evaluation shows that the pipeline works end to end; it says little about how well the rules separate attacks from normal use. The benign baseline shows the two count rules hold one below their thresholds, and that `suspicious_container_process` flags every process in the test container, an operator's `date` included: 1 of the 3 rules raised a false positive in that session. The benign traffic used in the evaluation is a script, not recorded user behaviour. See the evaluation's [threats to validity](evaluation-results.md#threats-to-validity).

## `stop_container` trusts Docker labels

The `stop_container` response, and the container scenario's exec, accept a container only if it has the label `cloudattacklab.role=test-target` and the Compose project label `com.docker.compose.project=cloudattacklab-lab`. Labels are set by whoever creates the container, so anyone who can create containers on this Docker engine could label another container that way. That person already has full control of Docker (and the host), so this is not a way in for anyone who did not have it, but the check is a guard against mistakes, not a security boundary.

## The container scenario can run one command only

The container scenario runs exactly one fixed command (`sh -c "id; : cloudattacklab-scenario-marker"`). This is a safety choice: no command is taken from a file or a request. A different container behaviour needs a new fixed action in the backend.

## No dashboard login

The dashboard and the API have no login (`POST /auth/login` in the project's original API plan is not built). Anyone who can reach `127.0.0.1:5173` or `127.0.0.1:8000` on this machine can start runs and approve responses, and every audit row has actor `anonymous`. Every port is bound to `127.0.0.1`, so that means "anyone with a session on this machine". Approval is therefore a deliberate second step, not a check of who approves.

## Default credentials are for local use only

The defaults in `.env.example`, `docker-compose.yml` and the backend (`LAB_CONTROL_TOKEN=lab-dev-token` or `change-me-lab-token`, `POSTGRES_PASSWORD=change-me-locally`, the test accounts `lab_user` / `lab_restricted` and their passwords) are published in this repository. They are acceptable only on `127.0.0.1`. The backend logs a warning at startup while the default lab token or database password is in use. Grafana has no default: it needs `GF_SECURITY_ADMIN_PASSWORD` in `.env`.

## Falco needs a privileged container and a kernel with BTF

Falco is optional (`FALCO_ENABLED=false` by default). Its modern eBPF engine loads BPF programs into the host kernel, so its container must be privileged, and the kernel must expose BTF (`/sys/kernel/btf/vmlinux`). Where either is missing (many hosted CI runners, some Docker Desktop setups), keep Falco off; the container scenario then uses Docker events only. See [safety](safety.md#falco-is-privileged).

## Docker Compose is the only supported environment

The lab, the platform and the monitoring stack are Docker Compose projects. The backend and the dashboard use host networking and the backend mounts the Docker socket, so it needs a Linux Docker Engine (native or inside WSL 2). Mounting the Docker socket gives the backend full control of Docker; the backend limits itself to the commands listed in [safety](safety.md#3-fixed-commands), but the access itself is not limited. There is no Kubernetes or cloud adapter.

## Nothing restarts by itself

None of the compose files sets a restart policy. After a reboot, a WSL shutdown or a Docker restart, every container is stopped and stays stopped; start the platform and the lab again by hand. The stored lab status is corrected when the Lab page is opened (see below).

## A run interrupted by a backend restart stays active

A run is carried out by a thread inside the backend process. If the backend stops during a run (a restart, a crash, WSL shutting down), the run's row stays `pending` or `running`. The backend then refuses a new run, a lab reset and any cleanup in that lab with `run_in_progress`, and there is no button or endpoint to clear it. This was found by reading the code, not reproduced. The workaround is a database update; see [troubleshooting](troubleshooting.md#the-lab-says-running-after-a-wsl-or-docker-restart).

## The dashboard is basic

- It works on the first lab ever created only. The API can create and run more labs (one running at a time), but the dashboard does not show them.
- Pages do not refresh by themselves, except a run in progress. Use **Refresh**.
- The Alerts list shows every alert ever raised, oldest first, with no filter or paging.

## Scenario files: what is not checked

- The `requires` list (capabilities such as `login_endpoint`) is descriptive. It is validated against a fixed list of names but not compared with what the target offers.
- A cleanup step with an unknown name is accepted when the file is loaded, logged as a warning and skipped when cleanup runs. Only the three known steps do anything.
- A scenario whose detection rule is missing still runs; it just raises no alert, and the backend logs a warning.

## Smaller notes

- **Target URL check.** `SHOPCLOUD_URL` must be a loopback address on the port the lab compose file publishes (`127.0.0.1:8081`), or a container of the `cloudattacklab-lab` project. If some other program on this machine listens on `127.0.0.1:8081` while the lab is down, the check cannot tell; lab start would then fail, because Docker cannot publish the port.
- **Lab stop during a run** is not blocked (only cleanup and reset are). The run then fails or ends `incomplete`, and says why.
- **Lab status** is compared with Docker when a lab is read (`GET /labs/{id}`, the Lab page), not continuously. `GET /labs` shows the stored status.
- **Single backend process.** "One run per lab" and "one lab operation at a time" use in-process locks plus database checks; running several backend processes against one database is not supported.
- **The risk score** is a fixed, explained formula for comparing incidents inside this lab. It does not predict financial loss and is not a general measure of risk.
- **The event source `platform`** and the event types `scenario_started`, `scenario_finished` and `response_executed` are defined in the event schema but never stored as events; the timeline builds those entries from the run and the audit log.
