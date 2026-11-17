# Troubleshooting

Real problems met while building and testing CloudAttackLab, and how to fix them. Each entry says what you see, why it happens and what to do. Entries marked **Seen on 17 November 2026** were reproduced on the test machine while writing this page; the others come from the project's history (the code comments, the README and the [November 2026 audit](audit-2026-11.md)).

Run every command from the `cloudattacklab/` folder, inside WSL if you use Windows.

## Contents

- [Vite does not notice saved files on a Windows drive](#vite-does-not-notice-saved-files-on-a-windows-drive)
- [Loki answers "not ready" for its first 15 seconds](#loki-answers-not-ready-for-its-first-15-seconds)
- [Containers cannot reach each other on 127.0.0.1](#containers-cannot-reach-each-other-on-127001)
- [Grafana: dashboard empty, "Datasource ... was not found", or the stack will not start](#grafana-dashboard-empty-datasource--was-not-found-or-the-stack-will-not-start)
- [Falco does not start, or the lab is "degraded" because of Falco](#falco-does-not-start-or-the-lab-is-degraded-because-of-falco)
- [The lab says "running" after a WSL or Docker restart](#the-lab-says-running-after-a-wsl-or-docker-restart)
- [A manual login gets 429 after a test](#a-manual-login-gets-429-after-a-test)
- [Other problems](#other-problems)

## Vite does not notice saved files on a Windows drive

**What you see.** You run the dashboard's development server (`npm run dev` in `frontend/`) inside WSL, with the code on a Windows drive such as `/mnt/d/...`. You save a file and the browser does not change. Restarting Vite shows the change.

**Why.** Vite watches files with Linux's inotify. Windows drives mounted in WSL (`/mnt/c`, `/mnt/d`) do not send inotify events when a file changes, so Vite is never told.

**Fix.** Already in the repository: `frontend/vite.config.js` sets

```js
watch: { usePolling: true, interval: 150 },
```

so Vite checks the files itself every 150 ms. If you copied an older config, add that line. Polling costs a little CPU; the other fix is to keep the code inside the Linux file system (for example `~/CloudAttackLabs`), where inotify works.

Two more things about the development server:

- It uses port 5173, which the dashboard container also uses (both are on the host network). Stop the container first with `docker compose stop frontend`, or Vite will pick the next free port and print it.
- It forwards `/api` to the backend at `127.0.0.1:8000` (the `proxy` in `vite.config.js`), so the backend must be running.

**Not reproduced on 17 November 2026**: Node.js is not installed inside WSL on the test machine, and the `frontend/node_modules` folder there was installed for Linux, so `npm run dev` could not be run from either side. The fix itself is in `vite.config.js`.

## Loki answers "not ready" for its first 15 seconds

**Seen on 17 November 2026.**

**What you see.** Right after `docker compose --env-file .env -f monitoring/docker-compose.monitoring.yml up -d`:

```bash
curl -s http://127.0.0.1:3100/ready
```

```
Ingester not ready: waiting for 15s after being ready
```

and runs with `TELEMETRY_SOURCE=loki` fail with "Telemetry source 'loki' is not available".

**Why.** Loki's ingester waits 15 seconds after it has started before it reports ready. This is normal Loki behaviour, not an error.

**Fix.** Wait. On the test machine it answered `ready` after about 20 seconds. Check with the `curl` above before starting a run. The backend never falls back to Docker when Loki is not available; the run fails before its script starts and nothing is sent to ShopCloud, so you can simply run it again.

Related: logs reach Loki a second or two after ShopCloud writes them. The backend waits after each run until the number of records stops growing (every 2 s, at most 20 s), and Alloy looks for new containers every 5 s instead of every minute, because a lab start or reset creates a new ShopCloud container.

## Containers cannot reach each other on 127.0.0.1

**What you see.** A service configured with `http://127.0.0.1:3100` cannot reach Loki ("connection refused"), although `curl http://127.0.0.1:3100/ready` works from your terminal.

**Why.** Inside a container on a Compose network, `127.0.0.1` is that container itself, not the machine. Containers in the same Compose project reach each other by **service name** on the project's network.

**What this project does.**

| From | To Loki | Why |
|---|---|---|
| Alloy, Grafana (same project as Loki) | `http://loki:3100` (`monitoring/config.alloy`, `monitoring/grafana/provisioning/datasources/loki.yml`) | Same Compose network, so the service name works |
| Backend | `http://127.0.0.1:3100` (`LOKI_URL`) | The backend uses the **host** network (`network_mode: host`), so for it `127.0.0.1` is the machine, where Loki's port is published |
| Your terminal | `http://127.0.0.1:3100` | The published port |

The same goes for ShopCloud: the backend reaches it at `127.0.0.1:8081` (published by the lab), not `shopcloud:8000`. If you ever move the backend off host networking, it needs a Docker network shared with the other projects and service-name URLs (`LOKI_URL=http://loki:3100`); and `SHOPCLOUD_URL` must then name a container of the lab project, which the target check accepts.

## Grafana: dashboard empty, "Datasource ... was not found", or the stack will not start

**The stack will not start, or `stop`/`ps`/`down` fails.** **Seen on 17 November 2026.**

```
error while interpolating services.grafana.environment.GF_SECURITY_ADMIN_PASSWORD: required variable GF_SECURITY_ADMIN_PASSWORD is missing a value: Set GF_SECURITY_ADMIN_PASSWORD in .env and start with --env-file .env
```

Grafana has no default password on purpose. Set `GF_SECURITY_ADMIN_PASSWORD` in the project's `.env`, and pass `--env-file .env` on **every** command for the monitoring compose file. Without it Docker Compose looks for `.env` next to the compose file, in `monitoring/`, which does not exist:

```bash
docker compose --env-file .env -f monitoring/docker-compose.monitoring.yml up -d
docker compose --env-file .env -f monitoring/docker-compose.monitoring.yml stop
```

**A panel shows "Datasource fg492mjm656v4b was not found" and "No data".** This is what the November 2026 audit found: the dashboard pointed at a datasource by an id that Grafana had made up when it was first set up by hand, and a fresh Grafana has a different one. Fixed in the repository: the Loki datasource is provisioned from `monitoring/grafana/provisioning/datasources/loki.yml` with a fixed `uid: loki`, and every panel of `monitoring/grafana/dashboards/lab-activity.json` refers to `{"type": "loki", "uid": "loki"}`. If you add a panel, use that uid, not one Grafana generates.

**You changed the dashboard in Grafana and the change is gone, or cannot be saved.** The dashboard is provisioned from a file (`monitoring/grafana/provisioning/dashboards/dashboards.yml`, with `allowUiUpdates: false` and `disableDeletion: true`). Edit `monitoring/grafana/dashboards/lab-activity.json` instead (in Grafana: Share → Export → copy the JSON, then paste it into the file), then restart Grafana:

```bash
docker compose --env-file .env -f monitoring/docker-compose.monitoring.yml restart grafana
```

(The restart command was not run on the test machine.)

**Panels show "No data".** Check, in order:

1. Loki is ready (`curl -s http://127.0.0.1:3100/ready`).
2. Alloy is running and has found the containers. Loki's `service` label lists every service it has logs for:

   ```bash
   curl -s http://127.0.0.1:3100/loki/api/v1/label/service/values
   ```

   ```
   {"status":"success","data":["alloy","backend","falco","frontend","grafana","loki","postgres","shopcloud"]}
   ```

   If `shopcloud` is missing, start or reset the lab and wait 5 seconds.
3. Something has happened in the time range you are looking at: run a scenario, and widen Grafana's time range.

## Falco does not start, or the lab is "degraded" because of Falco

**The lab is "degraded: falco is exited" right after turning Falco on.** **Seen on 17 November 2026.** You set `FALCO_ENABLED=true` and recreated the backend, but the lab was started before, without Falco. The backend now expects a Falco container. Click **Reset** on the Lab page: the lab is recreated with Falco. After about 20 seconds the page shows "Falco: on and healthy". (See [install, step 10](install.md#10-turn-falco-on-optional).)

**A container run is refused** with "pre-flight check 'falco' failed: FALCO_ENABLED is true but the Falco container is ...". Same cause, same fix: Reset or Start the lab.

**The Falco container keeps exiting.** Falco's modern eBPF engine needs two things from the machine:

1. **A kernel with BTF.** Check:

   ```bash
   ls /sys/kernel/btf/vmlinux
   ```

   On the test machine (WSL 2, kernel 6.18.33.2) the file exists. If it does not exist on yours, Falco cannot run there. Update WSL (`wsl --update` in PowerShell) or the kernel, or keep Falco off.
2. **A privileged container.** The lab's compose file sets `privileged: true` for Falco, because it loads BPF programs into the kernel and reads the host's `/proc`. Some environments do not allow privileged containers (many hosted CI runners, some Docker Desktop setups); there, keep Falco off.

Read Falco's own messages:

```bash
docker compose -f lab/docker-compose.lab.yml --profile falco logs falco
```

To turn Falco off: `FALCO_ENABLED=false` in `.env`, `docker compose up -d backend`, then Reset the lab. The container scenario still works with Docker events alone.

## The lab says "running" after a WSL or Docker restart

**Seen on 17 November 2026.**

**What you see.** You restart Windows, WSL (`wsl --shutdown`, or WSL stopping by itself) or Docker. Every container stops: none of the compose files sets a restart policy. You start the platform again with `docker compose up -d`, and the API's lab list still says `running`:

```
--- GET /labs (stored status)
followup running
```

**Why.** The lab's status is stored in the database. The backend compares it with Docker only when a single lab is read (`GET /labs/{id}`, which is what the Lab page does), not continuously and not in the list.

**Fix.** Open the Lab page (or click **Refresh**). Reading the lab corrects its status and says why:

```
--- GET /labs/{id} (checked against Docker)
stopped | Every lab container is down (shopcloud is exited, test_container is exited). Start the lab again.
```

The correction is recorded in the audit log as `lab.status_corrected`. Then click **Start**. If only some lab containers are down, the status becomes `degraded` and runs are refused until you Reset or Start.

**WSL stops by itself.** WSL shuts its Linux VM down a little while after the last WSL window or process exits, and that stops every container. Keep an Ubuntu window open while you use the lab.

**A run was in progress when it stopped.** Found by reading the code, not reproduced. A run is carried out by a thread inside the backend. If the backend stops during a run, nothing marks the run as finished: its row stays `pending` or `running`. While it does, the backend refuses a new run, a Reset and any Cleanup in that lab with `run_in_progress`, and the dashboard keeps showing the run as active. There is no button or endpoint to clear it. The way out is to mark the row as failed in the database (not run on the test machine; it changes data, so be sure no run is really active):

```bash
docker compose exec postgres psql -U cloudattacklab -d cloudattacklab -c \
  "UPDATE scenario_runs SET status = 'failed', error = 'interrupted by a backend restart' WHERE status IN ('pending', 'running');"
```

Use your own `POSTGRES_USER` and `POSTGRES_DB` if you changed them. Then Reset the lab. See [known limitations](known-limitations.md#a-run-interrupted-by-a-backend-restart-stays-active).

## A manual login gets 429 after a test

**Seen on 17 November 2026.**

**What you see.** After approving the `temporary_rate_limit` response (or after a test or evaluation that did), you try to sign in to ShopCloud yourself, with the right password, and get:

```bash
curl -s -X POST http://127.0.0.1:8081/api/login -H 'Content-Type: application/json' \
  -d '{"username":"lab_user","password":"lab-user-pass"}'
```

```
{"error":"too_many_attempts"}
```

(HTTP 429). The shop page says sign-in failed.

**Why.** The response turned ShopCloud's login rate limit on: at most 5 login attempts per source IP per 60 seconds. Every client on this machine reaches ShopCloud from the **same** source IP (the Docker gateway; see [known limitations](known-limitations.md#every-source-ip-is-the-docker-gateway-address)), so your login counts together with the scenario's and the verification's attempts. The limit stays on until something turns it off; it is not tied to a time.

You can see whether it is on (this needs the lab token):

```bash
curl -s http://127.0.0.1:8081/lab/rate-limit -H 'X-Lab-Token: lab-dev-token'
```

```
{"enabled":true,"max_attempts":5,"window_seconds":60}
```

(Use your own `LAB_CONTROL_TOKEN` if you changed it.)

**Fix.** Any of these turns it off:

- **Cleanup** on the run that the response belongs to (Scenarios page). The response becomes `reverted`.
- **Reset** on the Lab page.
- Waiting: attempts older than 60 seconds stop counting (refused attempts are not counted at all), so after a minute you can log in again. But the limit stays on, and the sixth attempt within a minute is refused again.

Do not use the shop by hand while a scenario runs either: your requests fall inside the run's time window and are counted as the run's events.

## Other problems

| What you see | Cause and fix |
|---|---|
| `curl: (7) Failed to connect` right after `docker compose up -d backend` | **Seen on 17 November 2026.** The command returns before the backend is ready. Wait a few seconds until `docker compose ps` shows the backend `(healthy)` |
| Lab page: "Target URL refused", runs refused with `target_url_refused` | `SHOPCLOUD_URL` in `.env` does not point into the lab. It must be a loopback address on the port the lab publishes (`http://127.0.0.1:8081`) or a container of the `cloudattacklab-lab` project. Fix it and run `docker compose up -d backend`. See [safety](safety.md#1-lab-only-targets) |
| `docker compose logs backend` shows "Default credential in use" | **Seen on 17 November 2026.** You are using a published default. Set `LAB_CONTROL_TOKEN` and `POSTGRES_PASSWORD` in `.env`. The database keeps its first password: changing `POSTGRES_PASSWORD` later needs `docker compose down -v`, which deletes all data |
| Lab start fails with a port error ("address already in use") | Something else uses 8081 (or 5173, 8000, 5432, 3000, 3100). Find it with `ss -ltnp` and stop it. Only one lab can run at a time |
| Start refused with `lab_conflict` | Another lab row in the database is running or starting. Stop it first (only through the API: the dashboard shows the oldest lab only) |
| A run is refused with `preflight_failed` | The message names the check: ShopCloud not healthy (Reset), a test account cannot log in (Reset restores the accounts), the test container is not running (run the last run's Cleanup, or Reset) |
| A run ends `incomplete` | The run's error says what is missing. Usually ShopCloud's logs did not arrive (with Loki: is Alloy running?) or requests got no answer. Run Cleanup, then try again |
| `docker: command not found` in PowerShell | Docker is installed inside WSL only. Run commands in the Ubuntu window |
