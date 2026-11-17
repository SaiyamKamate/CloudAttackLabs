# CloudAttackLab dashboard

The React (Vite) dashboard of CloudAttackLab: the pages Lab, Scenarios, Alerts and Incidents, and the run report view. How to use it is in the [user guide](../docs/user-guide.md).

| File | What it is |
|---|---|
| `src/App.jsx` | The page switcher |
| `src/pages/` | One component per page, plus `ResponsePanel.jsx` (request, approve, reject a response) and `ReportPage.jsx` |
| `src/api.js` | Every call to the backend API ([api-contract.md](../docs/api-contract.md)). Paths are relative (`/api/v1/...`) |
| `vite.config.js` | Development server: forwards `/api` to the backend on `127.0.0.1:8000`, and polls for file changes (needed on a Windows drive under WSL) |
| `nginx.conf`, `Dockerfile` | The production container: nginx serves the built files on `127.0.0.1:5173` and forwards `/api/` to the backend |

Normally the dashboard runs in its container (`docker compose up -d --build` from the project folder). For development with hot reload:

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, forwards /api to the backend on :8000
```

These three commands were not run while writing this page (no Node.js inside WSL on the test machine). Stop the dashboard container first (`docker compose stop frontend`), because it uses the same port. See [troubleshooting](../docs/troubleshooting.md#vite-does-not-notice-saved-files-on-a-windows-drive) if saved files do not show up.
