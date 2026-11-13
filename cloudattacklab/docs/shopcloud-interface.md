# ShopCloud interface for CloudAttackLab (v1.0)

This is what CloudAttackLab needs from ShopCloud. Anything not listed here (pages, products, cart, database, framework) is up to whoever builds ShopCloud.

A fake ShopCloud that follows this page is in `mock_shopcloud/`. To test the real one against this page:

```
python tools/check_shopcloud.py http://127.0.0.1:8081 --lab-token lab-dev-token
```

## 1. Docker

| Item | Value |
|---|---|
| Build | A `Dockerfile` in the ShopCloud folder; no manual steps after `docker compose up` |
| Compose service name | `shopcloud` |
| Port inside the container | `8000` |
| Environment variable | `LAB_CONTROL_TOKEN` (the platform sets it) |
| Logs | Written to **stdout**, one JSON object per line (section 5) |
| Data | Synthetic test data only; state returns to baseline after `/lab/reset` |

## 2. Test accounts

These must exist at baseline.

| Username | Password | Role | Used by |
|---|---|---|---|
| `lab_user` | `lab-user-pass` | `user` | Scenario 1 (login target), normal access |
| `lab_restricted` | `lab-restricted-pass` | `restricted` | Scenario 3 (denied access) |

## 3. Application endpoints

All bodies are JSON.

| Endpoint | Request | Responses |
|---|---|---|
| `GET /health` | none | `200 {"status": "ok"}` |
| `POST /api/login` | `{"username": "...", "password": "..."}` | `200 {"token": "...", "username": "...", "role": "..."}` · `401 {"error": "invalid_credentials"}` · `429 {"error": "too_many_attempts"}` when the rate limit blocks it |
| `GET /api/orders` | Header `Authorization: Bearer <token>` | `200 {"orders": [...]}` for role `user` · `403 {"error": "forbidden"}` for role `restricted` · `401 {"error": "not_authenticated"}` with no or a bad token |

## 4. Lab control endpoints

The platform uses these for response actions and cleanup. Every request must carry the header `X-Lab-Token: <LAB_CONTROL_TOKEN>`; otherwise return `403`.

| Endpoint | Request | Behaviour |
|---|---|---|
| `GET /lab/rate-limit` | none | `200` with the current settings |
| `POST /lab/rate-limit` | `{"enabled": true, "max_attempts": 5, "window_seconds": 60}` | Saves the settings and returns them. `422` if they are invalid |
| `POST /lab/reset` | none | Restores the baseline: rate limit off, all tokens cleared, test data restored. `200 {"status": "reset"}` |

Rate limit rule: it is **off at baseline**. When on, each source IP may make `max_attempts` login attempts per `window_seconds`. Further attempts get `429` until the window passes.

## 5. Log format

One JSON object per line on stdout, for every request to `/api/*` and `/lab/*`.

```json
{"timestamp": "2026-11-13T14:00:00.123Z", "service": "shopcloud", "level": "warning", "event": "login_failed", "request_id": "6f1c...", "source_ip": "172.20.0.5", "method": "POST", "path": "/api/login", "status": 401, "username": "lab_user", "message": "Invalid username or password"}
```

| Field | Rule |
|---|---|
| `timestamp` | UTC, ISO 8601, ending in `Z` |
| `service` | Always `shopcloud` |
| `level` | `info` or `warning` |
| `event` | One of the names below |
| `request_id` | Unique per request (a UUID). The platform uses it as the evidence reference |
| `source_ip` | Client IP of the request |
| `method`, `path`, `status` | The HTTP request and the response code |
| `username` | The account involved, or `null` |
| `message` | Short human-readable text |

**Never log passwords or tokens.**

| `event` | When | CloudAttackLab normalizes it to |
|---|---|---|
| `login_success` | Login returned 200 | `authentication_success` |
| `login_failed` | Login returned 401 | `authentication_failure` |
| `login_rate_limited` | Login returned 429 | `rate_limit_triggered` |
| `access_granted` | Protected resource returned 200 | `resource_access` |
| `access_denied` | Protected resource or lab endpoint returned 401/403 | `authorization_denied` |
| `rate_limit_changed` | Rate limit settings changed | `config_changed` |
| `lab_reset` | Baseline restored | `config_changed` |

## 6. Changes

If either side needs to change something here, change this file first and tell the other person. Both the fake ShopCloud and `tools/check_shopcloud.py` are then updated to match.
