# ShopCloud

A small web shop (products, login, orders) that CloudAttackLab uses as its attack target. It has its own web page, API and SQLite database, and no CloudAttackLab code.

ShopCloud can run two ways. Both build from this folder, and both can run at the same time:

| | Standalone (this README) | Bundled lab |
|---|---|---|
| Started by | You, with `shopcloud/docker-compose.yml` | CloudAttackLab's Lab Manager (`lab/docker-compose.lab.yml`) |
| URL | <http://127.0.0.1:8090> | <http://127.0.0.1:8081> |
| Compose project | `shopcloud` | `cloudattacklab-lab` |
| Runs without CloudAttackLab | Yes | No |
| Data | Kept in the `shop-data` volume across restarts | Reset to the seed data at every start |
| Connected to CloudAttackLab by | URL, in **Target Management** (signed integration API) | Configuration (`SHOPCLOUD_URL`) and the Docker socket |
| Integration API (`/integration/v1`) | On (needs `INTEGRATION_KEY`) | Off |

Before the presentation, ShopCloud lives in this repository. Moving it to its own repository is planned afterwards (see [the integration plan](../docs/architecture/PRESENTATION_INTEGRATION_PLAN.md), section 4.4).

## Run it standalone

You need Docker with Compose v2. On this project's Windows machines Docker runs inside WSL, so run these commands in the WSL terminal (see the [installation guide](../docs/install.md)).

1. **Create `.env` in this folder.** It is git-ignored; never commit it.

   ```bash
   cd shopcloud
   cp .env.example .env
   ```

   Open `.env` and fill in the two secrets. Generate each one with:

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   | Setting | Used for | Rule |
   |---|---|---|
   | `INTEGRATION_KEY` | Signing every `/integration/v1` request and answer | At least 32 characters |
   | `LAB_CONTROL_TOKEN` | The `X-Lab-Token` header on `/lab/rate-limit` and `/lab/reset` | Your own value |
   | `INTEGRATION_INSTANCE_ID` | The name CloudAttackLab checks on every telemetry record | Default `shopcloud-standalone` |

   Compose refuses to start while either secret is empty.

2. **Start it.**

   ```bash
   docker compose up -d --build
   ```

3. **Check it.**

   ```bash
   curl http://127.0.0.1:8090/health
   ```

   It answers `{"status":"ok"}`. Open <http://127.0.0.1:8090> in a browser and log in as `lab_user` with password `lab-user-pass`.

The port is published on `127.0.0.1` only, so ShopCloud is reachable from this machine and nowhere else.

## Connect it to CloudAttackLab

Start CloudAttackLab as usual, open the dashboard at <http://127.0.0.1:5173> and go to **Target Management**. Register `http://127.0.0.1:8090` and paste `INTEGRATION_KEY` and `LAB_CONTROL_TOKEN` from your `.env`. Then verify the connection, verify the telemetry, and authorize an experiment.

The [user guide](../docs/user-guide.md#13-connect-an-independent-shopcloud-target-management) walks through each step.

## Stop, restart and reset

| Goal | Command (in `shopcloud/`) |
|---|---|
| Stop, keeping the data | `docker compose stop` (or `docker compose down`) |
| Start again | `docker compose up -d` |
| Restore the seed data | CloudAttackLab's run cleanup does this through `POST /lab/reset`; or delete the volume (next row) |
| Remove everything, data included | `docker compose down -v` |
| Read its security log | `docker compose logs shopcloud-standalone` |

## What ShopCloud logs

Every request to `/`, `/api/*` and `/lab/*` writes one JSON line to stdout: time, event, request ID, source IP, method, path, status, user name and a message. Passwords and tokens are never logged. The format and the event names are in [the interface](../docs/shopcloud-interface.md#5-log-format).

The same records are kept in a buffer of the last 10,000 events. CloudAttackLab reads that buffer through the signed event feed. Requests to `/integration/*` are not logged, so CloudAttackLab reading the feed adds nothing to the security log.

## Integration API

Off unless `INTEGRATION_KEY` is set: then every `/integration` path answers 404, as before.

| Endpoint | Purpose |
|---|---|
| `POST /integration/v1/handshake` | Proves that this is ShopCloud and that it holds the key; returns the instance, version and capabilities |
| `POST /integration/v1/telemetry/test` | Writes one `integration_test` security event carrying the caller's nonce |
| `GET /integration/v1/events?since=&after_seq=&limit=` | The buffered security events, oldest first, at most 1,000 per page |

Every request must be signed with HMAC-SHA256 and be no more than 60 seconds old, and each nonce is accepted only once. Every answer is signed too. Section 6 of [the interface](../docs/shopcloud-interface.md#6-integration-api-v1) has the exact format.

## Development

Run the tests from the repository root:

```bash
python -m pytest shopcloud/tests
```

Run ShopCloud without Docker, from this folder:

```bash
SHOP_DB_PATH=./data/shop.db INTEGRATION_KEY=<a key of at least 32 characters> uvicorn --factory shop.main:create_app --port 8000
```

| Setting | Default | Meaning |
|---|---|---|
| `SHOP_DB_PATH` | `/app/data/shop.db` | The SQLite database file |
| `SHOP_RESET_ON_START` | `true` | Restore the seed data at every start. The standalone Compose file sets `false` |
| `LAB_CONTROL_TOKEN` | `lab-dev-token` | The token for `/lab/*` |
| `INTEGRATION_KEY` | empty (integration off) | The key for `/integration/v1/*` |
| `INTEGRATION_INSTANCE_ID` | random per start | The instance name in the handshake and on every event |
