#!/usr/bin/env bash
# First-time setup, safe to run again at any time.
#
#   scripts/setup.sh                 basic mode: database, backend, dashboard, and a lab
#   scripts/setup.sh --monitoring    also Loki, Alloy and Grafana
#   scripts/setup.sh --no-lab        do not create or start a lab
#
# What it does, in order:
#   1. Checks Docker, Docker Compose, curl and python3, and that the ports are free.
#   2. Creates .env from .env.example with random passwords, ONLY if there is no
#      .env yet. An existing .env is never changed.
#   3. Builds the images (Docker's cache makes later runs fast) and starts the platform.
#   4. Waits until the backend, database and dashboard answer.
#   5. Creates a lab named "default" if there is none, and starts it.
# It never deletes containers, volumes or data.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; }

WITH_MONITORING=false
WITH_LAB=true
for arg in "$@"; do
  case "$arg" in
    --monitoring) WITH_MONITORING=true ;;
    --no-lab) WITH_LAB=false ;;
    -h|--help) usage; exit 0 ;;
    *) usage; die "Unknown option: $arg" ;;
  esac
done
monitoring_requested && WITH_MONITORING=true

# --- 1. prerequisites ----------------------------------------------------------
step "Checking prerequisites"
check_prerequisites
ok "$(docker --version)"
ok "$(docker compose version)"
legacy_monitoring_hint

# port:service. A port held by our own running service (a second run) is fine.
ports=("5173:frontend" "${BACKEND_PORT}:backend" "5432:postgres" "8081:shopcloud")
$WITH_MONITORING && ports+=("3000:grafana" "3100:loki" "12345:alloy")
busy=0
for entry in "${ports[@]}"; do
  port="${entry%%:*}"; service="${entry#*:}"
  port_in_use "$port" || continue
  if [ "$service" = shopcloud ]; then
    ours="$(docker ps -q --filter "label=com.docker.compose.project=$LAB_PROJECT" --filter label=com.docker.compose.service=shopcloud)"
  else
    ours="$(docker compose ps -q --status running "$service" 2>/dev/null)"
  fi
  if [ -z "$ours" ]; then
    fail "Port $port (needed by $service) is already used by another program."
    busy=1
  fi
done
[ "$busy" -eq 0 ] || die "Free the ports above (or stop the program using them) and run setup again."
ok "Ports are free"

# --- 2. .env ------------------------------------------------------------------
step "Settings (.env)"
random_secret() { head -c 18 /dev/urandom | base64 | tr -d '/+=' | cut -c1-24; }
set_value() {  # set_value KEY VALUE  (in .env, on its existing line)
  local key="$1" value="$2"
  sed -i "s|^${key}=.*|${key}=${value}|" .env
}

if [ -f .env ]; then
  ok ".env already exists; it is kept as it is"
  missing="$(comm -23 \
    <(grep -oE '^[A-Z_]+=' .env.example | sort -u) \
    <(grep -oE '^[[:space:]]*[A-Z_]+=' .env | tr -d ' \t' | sort -u) | tr -d '=' | tr '\n' ' ')"
  [ -z "$missing" ] || info "Not set in your .env (their defaults are used): $missing"
else
  cp .env.example .env
  chmod 600 .env
  set_value LAB_CONTROL_TOKEN "$(random_secret)"
  set_value GF_SECURITY_ADMIN_PASSWORD "$(random_secret)"
  if db_volume_exists; then
    # The database keeps the password it was created with: a new random one
    # would lock the backend out. Keep the published default and say so.
    warn "A database volume ($DB_VOLUME) already exists, so POSTGRES_PASSWORD keeps the"
    warn "value it was most likely created with (change-me-locally). See docs/install.md."
  else
    set_value POSTGRES_PASSWORD "$(random_secret)"
  fi
  ok "Created .env with random passwords (Grafana's admin password is in it: GF_SECURITY_ADMIN_PASSWORD)"
fi

# --- 3. build and start -------------------------------------------------------
step "Building and starting the platform"
compose_args=()
services=("${CORE_SERVICES[@]}")
if $WITH_MONITORING; then
  compose_args+=(--profile monitoring)
  services+=("${MONITORING_SERVICES[@]}")
fi
info "The first build downloads images and can take several minutes."
if ! docker compose "${compose_args[@]}" up -d --build "${services[@]}"; then
  fail "docker compose could not build or start the platform (the reason is just above)."
  recovery_hints; exit 1
fi

# --- 4. health ----------------------------------------------------------------
step "Waiting for the services"
healthy=true
wait_for_url "$API/health" 120 "Backend and database" '"database":"ok"' || healthy=false
wait_for_url "$DASHBOARD_URL/" 60 "Dashboard" || healthy=false
if $WITH_MONITORING; then
  wait_for_url "$LOKI_URL_HOST/ready" 90 "Loki" ready || healthy=false
  wait_for_url "$GRAFANA_URL/api/health" 90 "Grafana" || healthy=false
fi
if ! $healthy; then
  recovery_hints; exit 1
fi

# --- 5. lab -------------------------------------------------------------------
if $WITH_LAB; then
  step "Lab"
  lab="$(latest_lab)" || die "Could not list labs from the backend."
  if [ -z "$lab" ]; then
    created="$(api_post /labs '{"name": "default", "target": "shopcloud"}')" || die "Could not create a lab: $created"
    lab="$(printf '%s' "$created" | lab_lines one)"
    ok "Created lab 'default'"
  fi
  IFS=$'\t' read -r lab_id lab_name lab_status <<<"$lab"
  case "$lab_status" in
    running) ok "Lab '$lab_name' is already running" ;;
    *) start_lab "$lab_id" "$lab_name" || { recovery_hints; exit 1; } ;;
  esac
fi

# --- done ---------------------------------------------------------------------
step "CloudAttackLab is ready"
info "Dashboard:   $DASHBOARD_URL"
info "API:         $API  (health: $API/health)"
$WITH_MONITORING && info "Grafana:     $GRAFANA_URL  (user admin, password GF_SECURITY_ADMIN_PASSWORD in .env)"
$WITH_LAB || info "Lab:         not started (--no-lab). Create and start one on the dashboard's Lab page."
info "Next:        run a scenario from the Scenarios page (docs/user-guide.md)"
info "Status:      scripts/status.sh     Stop: scripts/stop.sh (keeps all data)"
