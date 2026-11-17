# Known limitations

What CloudAttackLab does not do, or does only in a simple way. Each item says why, and what to keep in mind when you show or extend the lab.

## Events are matched to a run by time

A run's events are the target's log records (and the test container's Docker events) whose timestamps fall between the run's start and the moment its script finished, plus `TELEMETRY_SINCE_GRACE_SECONDS` (3 s). Nothing in a ShopCloud request says which run sent it, so any other request to ShopCloud in that window, for example someone browsing the shop page during a demo run, is stored as one of the run's events.

What limits it: one run per lab at a time; cleanup of other runs and lab reset are refused while a run is active; the window ends when the script finishes; and ShopCloud's records of the platform's own calls (`lab_reset`, `rate_limit_changed`) are never stored as a run's events. Do not use the shop by hand while a scenario runs.

## `stop_container` trusts Docker labels

The `stop_container` response, and the container scenario's exec, accept a container only if it has the label `cloudattacklab.role=test-target` and the Compose project label `com.docker.compose.project=cloudattacklab-lab`. Labels are set by whoever creates the container, so anyone who can create containers on this Docker engine could label another container that way. That person already has full control of Docker (and the host), so this is not a way in for anyone who did not have it, but the check is a guard against mistakes, not a security boundary.

## Every source IP is the Docker gateway address

Every ShopCloud event shows the same `source_ip`, the lab network's gateway (for example `172.21.0.1`). The backend runs with host networking and reaches ShopCloud on the published port `127.0.0.1:8081`. Docker forwards that port into the container through its port proxy (or NAT), so the connection ShopCloud sees comes from the gateway of the `cloudattacklab-lab` network, not from a real client address. ShopCloud deliberately ignores `X-Forwarded-For`, so the address cannot be spoofed either.

As a result, "one source IP" in `repeated_failed_logins` is always this address: the scenario's requests, the response verification and any browsing of the shop all look like one client. The rule still fires correctly for the scenario; it just cannot tell clients apart.

## No dashboard login

The dashboard and the API have no login (`POST /auth/login` in `docs/api-contract.md` is not built). Anyone who can reach `127.0.0.1:5173` or `127.0.0.1:8000` on this machine can start runs and approve responses, and every audit row has actor `anonymous`. Every port is bound to `127.0.0.1`, so that means "anyone with a session on this machine".

## Default credentials are for local use only

The defaults in `.env.example`, `docker-compose.yml` and the backend (`LAB_CONTROL_TOKEN=lab-dev-token` or `change-me-lab-token`, `POSTGRES_PASSWORD=change-me-locally`, the test accounts `lab_user` / `lab_restricted`) are published in this repository. They are acceptable only on `127.0.0.1`. The backend logs a warning at startup while the default lab token or database password is in use. Grafana has no default: it needs `GF_SECURITY_ADMIN_PASSWORD` in `.env`.

## Falco needs a privileged container and a kernel with BTF

Falco is optional (`FALCO_ENABLED=false` by default). Its modern eBPF engine loads BPF programs into the host kernel, so its container must be privileged, and the kernel must expose BTF (`/sys/kernel/btf/vmlinux`). Where either is missing (many hosted CI runners, some Docker Desktop setups), keep Falco off; the container scenario then uses Docker events only.

## Docker Compose is the only supported environment

The lab, the platform and the monitoring stack are Docker Compose projects. The backend and the dashboard use host networking and the backend mounts the Docker socket, so it needs a Linux Docker Engine (native or inside WSL 2). There is no Kubernetes or cloud adapter: `LabAdapter` (`backend/app/lab_manager/adapter.py`) is the interface one would implement.

## Smaller notes

- **Target URL check.** `SHOPCLOUD_URL` must be a loopback address on the port the lab compose file publishes (`127.0.0.1:8081`), or a container of the `cloudattacklab-lab` project. If some other program on this machine listens on `127.0.0.1:8081` while the lab is down, the check cannot tell; lab start would then fail, because Docker cannot publish the port.
- **Lab stop during a run** is not blocked (only cleanup and reset are). The run then fails or ends `incomplete`, and says why.
- **Lab status** is compared with Docker when a lab is read (`GET /labs/{id}`, the Lab page), not continuously. `GET /labs` shows the stored status.
- **Single backend process.** "One run per lab" and "one lab operation at a time" use in-process locks plus database checks; running several backend processes against one database is not supported.
