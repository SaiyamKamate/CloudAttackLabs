# Shared helpers for the scripts in this folder. Sourced, not run.
# Every script works from the repository root, whatever folder it is started in.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LAB_COMPOSE_FILE="lab/docker-compose.lab.yml"
# Set by check_prerequisites from the compose files (their `name:`, or
# COMPOSE_PROJECT_NAME if you set one), so the scripts never guess them.
PROJECT=""; LAB_PROJECT=""; DB_VOLUME=""
CORE_SERVICES=(postgres backend frontend)
MONITORING_SERVICES=(loki alloy grafana)

# --- output ------------------------------------------------------------------
if [ -t 1 ]; then
  C_RED=$'\e[31m'; C_GREEN=$'\e[32m'; C_YELLOW=$'\e[33m'; C_BLUE=$'\e[34m'; C_BOLD=$'\e[1m'; C_OFF=$'\e[0m'
else
  C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""; C_OFF=""
fi
step() { printf '\n%s==> %s%s\n' "$C_BOLD$C_BLUE" "$*" "$C_OFF"; }
info() { printf '    %s\n' "$*"; }
ok()   { printf '    %s[ok]%s %s\n' "$C_GREEN" "$C_OFF" "$*"; }
warn() { printf '    %s[warn]%s %s\n' "$C_YELLOW" "$C_OFF" "$*" >&2; }
fail() { printf '    %s[fail]%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; }
die()  { fail "$*"; exit 1; }

# Printed when a start-up step fails.
recovery_hints() {
  cat >&2 <<EOF

    What to try next:
      scripts/status.sh                    which service is down, and why
      docker compose logs --tail=80 backend   the backend's own error (same for postgres, frontend)
      scripts/start.sh                     safe to run again; it only starts what is missing
      docs/troubleshooting.md              known problems and fixes
    Nothing was deleted: your .env and the database volume are untouched.
EOF
}

# --- settings ----------------------------------------------------------------
# A setting the way Docker Compose sees it: the shell first, then .env, then $2.
env_get() {
  local key="$1" default="${2:-}" value=""
  if [ -n "${!key:-}" ]; then printf '%s' "${!key}"; return; fi
  if [ -f .env ]; then
    value="$(grep -E "^[[:space:]]*${key}=" .env | tail -n 1 | cut -d= -f2- | sed -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/")" || true
  fi
  printf '%s' "${value:-$default}"
}

BACKEND_PORT="$(env_get BACKEND_PORT 8000)"
API="http://127.0.0.1:${BACKEND_PORT}/api/v1"
DASHBOARD_URL="http://127.0.0.1:5173"
GRAFANA_URL="http://127.0.0.1:3000"
LOKI_URL_HOST="http://127.0.0.1:3100"

# --- prerequisites -----------------------------------------------------------
require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is not installed. $2"
}

check_prerequisites() {
  case "$(uname -s)" in
    Linux) ;;
    MINGW*|MSYS*|CYGWIN*) die "This is a Windows shell. Open Ubuntu (WSL 2) and run the script there: see docs/install.md." ;;
    *) warn "Untested system '$(uname -s)'. CloudAttackLab is tested on Ubuntu in WSL 2 only." ;;
  esac
  require_cmd docker "Install Docker Engine inside Ubuntu: https://docs.docker.com/engine/install/ubuntu/"
  docker compose version >/dev/null 2>&1 || die "'docker compose' (the Compose v2 plugin) is missing. Install docker-compose-plugin."
  if ! docker info >/dev/null 2>&1; then
    if docker info 2>&1 | grep -qi "permission denied"; then
      die "Docker refused access. Run: sudo usermod -aG docker \$USER, then close and reopen the terminal."
    fi
    die "The Docker daemon is not running. Start it with: sudo service docker start"
  fi
  require_cmd curl "Install it with: sudo apt-get install -y curl"
  require_cmd python3 "Install it with: sudo apt-get install -y python3"
  PROJECT="$(compose_project_name)" || die "docker compose cannot read docker-compose.yml (run 'docker compose config' to see why)."
  LAB_PROJECT="$(compose_project_name -f "$LAB_COMPOSE_FILE")" || die "docker compose cannot read $LAB_COMPOSE_FILE."
  DB_VOLUME="${PROJECT}_pgdata"
}

# The Compose project name of the platform (or, with -f FILE, of that file).
compose_project_name() {
  docker compose "$@" config --format json 2>/dev/null \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["name"])'
}

require_env_file() {
  [ -f .env ] || die "No .env file. Run scripts/setup.sh first (it creates one), or: cp .env.example .env"
}

# --- Docker state ------------------------------------------------------------
# "running", "healthy", "unhealthy", "exited", ... or "missing", for a platform service.
service_state() {
  local id
  id="$(docker compose ps -a -q "$1" 2>/dev/null | head -n 1)"
  [ -n "$id" ] || { echo missing; return; }
  # A stopped container keeps its last health status, so only trust it while running.
  docker inspect -f '{{if and .State.Running .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" 2>/dev/null || echo missing
}

# Containers of the old, separate monitoring project (before the monitoring profile).
legacy_monitoring_hint() {
  local old
  old="$(docker ps -a -q --filter label=com.docker.compose.project=cloudattacklab-monitoring)"
  [ -n "$old" ] || return 0
  warn "Found containers of the old 'cloudattacklab-monitoring' project. Monitoring is now the"
  warn "'monitoring' profile of the main project. Remove the old ones (they hold no volumes) with:"
  warn "  docker compose -p cloudattacklab-monitoring down"
}

monitoring_requested() {
  # True when the monitoring profile is on through COMPOSE_PROFILES (shell or .env).
  case ",$(env_get COMPOSE_PROFILES)," in *,monitoring,*) return 0 ;; esac
  return 1
}

monitoring_running() {
  [ "$(service_state loki)" = running ] || [ "$(service_state grafana)" = running ]
}

db_volume_exists() {
  docker volume inspect "$DB_VOLUME" >/dev/null 2>&1
}

# A TCP port on 127.0.0.1 that something already listens on.
port_in_use() {
  if command -v ss >/dev/null 2>&1; then
    ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$1\$"
  else
    (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
  fi
}

# --- HTTP --------------------------------------------------------------------
# wait_for_url URL SECONDS LABEL [EXPECTED_TEXT]
wait_for_url() {
  local url="$1" timeout="$2" label="$3" expect="${4:-}" waited=0 body
  while :; do
    if body="$(curl -fsS --max-time 3 "$url" 2>/dev/null)"; then
      if [ -z "$expect" ] || printf '%s' "$body" | grep -q "$expect"; then
        ok "$label answers at $url"
        return 0
      fi
    fi
    if [ "$waited" -ge "$timeout" ]; then
      fail "$label did not answer at $url within ${timeout}s"
      return 1
    fi
    sleep 2; waited=$((waited + 2))
  done
}

api_get()  { curl -fsS --max-time 10 "$API$1"; }
# POST and print the body; a non-2xx answer prints the body and fails.
api_post() {
  local path="$1" body="${2:-}" timeout="${3:-30}" out code
  out="$(mktemp)"
  local args=(-sS --max-time "$timeout" -o "$out" -w '%{http_code}' -X POST -H 'Content-Type: application/json')
  if [ -n "$body" ]; then args+=(--data "$body"); fi
  code="$(curl "${args[@]}" "$API$path")" || { rm -f "$out"; return 1; }
  cat "$out"; rm -f "$out"
  [ "${code:0:1}" = 2 ]
}

# Reads JSON on stdin and prints labs as "id<TAB>name<TAB>status" lines.
#   lab_lines          one line per lab in a list
#   lab_lines one      the single lab object on stdin
#   lab_lines active   only labs that are running, degraded or starting
lab_lines() {
  python3 -c '
import json, sys
data = json.load(sys.stdin)
mode = sys.argv[1] if len(sys.argv) > 1 else "all"
for lab in ([data] if mode == "one" else data):
    if mode == "active" and lab["status"] not in ("running", "degraded", "starting"):
        continue
    print("\t".join([str(lab["id"]), lab["name"], lab["status"]]))
' "$@"
}

# The newest lab, as "id<TAB>name<TAB>status", or nothing when there is none.
latest_lab() {
  local labs
  labs="$(api_get /labs)" || return 1
  printf '%s' "$labs" | lab_lines | tail -n 1
}

# Starts a lab through the backend (it builds ShopCloud on first use; can take minutes).
start_lab() {
  local id="$1" name="$2"
  info "Starting lab '$name' (ShopCloud and the test container). The first start builds ShopCloud's image."
  if api_post "/labs/$id/start" "" 600 >/dev/null; then
    ok "Lab '$name' is running"
  else
    fail "The backend could not start lab '$name'. Open the Lab page in the dashboard for the reason, or run scripts/status.sh."
    return 1
  fi
}
