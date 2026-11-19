# CloudAttackLab dashboard

The React (Vite) dashboard of CloudAttackLab: a dark security-operations interface with a sidebar, a top bar (active lab, backend connectivity, notifications) and nine pages. How to use the platform is in the [user guide](../docs/user-guide.md).

| Page (hash route) | What it shows | Backend calls |
|---|---|---|
| Overview (`#/overview`) | Environment health, counts, run activity, alert severities, recent items | labs, runs (via audit), alerts, incidents, audit |
| Labs & Environments (`#/labs`) | Lab status, health, containers; Start / Stop / Reset (confirmed); create a lab | `/labs`, `/labs/{id}` |
| Attack Scenarios (`#/scenarios/<id>`) | Catalogue, full definition, environment checks, Run (confirmed), live run, Cleanup | `/scenarios`, `/scenarios/{id}`, `/labs/{id}/runs`, `/runs/{id}` |
| Experiment Runs (`#/runs/<id>`) | Run history with detection and cleanup outcomes; run detail with its report | audit `run.start`, `/runs/{id}`, `/runs/{id}/report` |
| Events & Telemetry (`#/events`) | Event explorer: time range, run, type, source, severity, search; raw record | `/events`, `/events/{id}` |
| Detection & Alerts (`#/alerts/<id>`) | Alerts with filters; investigation drawer with supporting events | `/alerts`, `/alerts/{id}` |
| Incidents (`#/incidents/<id>`) | Investigation workspace: attack flow, timeline, evidence, risk factors, response | `/incidents`, `/incidents/{id}`, `/runs/{id}` |
| Response Center (`#/responses`) | Every response by status; approve / reject through the shared response panel | incident details, `/responses/{id}/…`, `/audit` |
| Reports & Analytics (`#/reports/<id>`) | Outcomes per scenario, run history, each run's report (JSON download) | runs (via audit), `/runs/{id}/report` |

The backend has no list endpoint for runs or responses. The run list is rebuilt from the audit log's `run.start` rows and `GET /runs/{id}`; the response list from the incident details. Nothing shown is estimated or invented; when the backend does not provide something (PDF/CSV export, user accounts) the dashboard says so.

| Path | What it is |
|---|---|
| `src/App.jsx` | Providers, the shell (sidebar, top bar) and the hash router |
| `src/api.js` | Every call to the backend API ([api-contract.md](../docs/api-contract.md)). No other file calls `fetch` |
| `src/lib/` | Router, hooks, formatting, plain-language descriptions of backend results, and views built from several endpoints (`data.js`) |
| `src/components/ui/` | Design-system parts: tables, drawers, dialogs, toasts, badges, charts, states |
| `src/components/layout/` | Sidebar, top bar and the shared platform state (labs, backend health, current run, attention counts) |
| `src/components/domain/` | Health panel, scenario card and detail, run execution, event table and drawer, alert drawer, incident timeline, risk breakdown, `ResponsePanel` |
| `src/pages/` | One component per page; `ReportPage.jsx` is the run report |
| `src/index.css` | Design tokens and styles |
| `vite.config.js` | Development server: forwards `/api` to the backend on `127.0.0.1:8000`, and polls for file changes (needed on a Windows drive under WSL) |
| `nginx.conf`, `Dockerfile` | The production container: nginx serves the built files on `127.0.0.1:5173` and forwards `/api/` to the backend |

The only runtime dependency besides React is `lucide-react` (icons). Charts are plain SVG.

Normally the dashboard runs in its container (`docker compose up -d --build` from the project folder). For development with hot reload:

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, forwards /api to the backend on :8000
```

For the November 2026 redesign these were run with Node.js 24 on Windows (not inside WSL): `npm run dev` served the dashboard and forwarded `/api` to the backend container running in WSL. Stop the dashboard container first (`docker compose stop frontend`), because it uses the same port. See [troubleshooting](../docs/troubleshooting.md#vite-does-not-notice-saved-files-on-a-windows-drive) if saved files do not show up.
