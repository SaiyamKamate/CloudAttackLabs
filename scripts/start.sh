#!/usr/bin/env bash
# Start CloudAttackLab after setup (for example after a reboot), and wait until it answers.
#
#   scripts/start.sh                 platform: database, backend, dashboard
#   scripts/start.sh --monitoring    also Loki, Alloy and Grafana
#   scripts/start.sh --lab           also start the newest lab (ShopCloud, test container)
#   scripts/start.sh --build         rebuild the images first (after `git pull`)
#
# Without --build nothing is rebuilt: images are only built when they are missing.
# Safe to run again; it only starts what is not running. Never deletes data.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; }

WITH_MONITORING=false
WITH_LAB=false
BUILD=()
for arg in "$@"; do
  case "$arg" in
    --monitoring) WITH_MONITORING=true ;;
    --lab) WITH_LAB=true ;;
    --build) BUILD=(--build) ;;
    -h|--help) usage; exit 0 ;;
    *) usage; die "Unknown option: $arg" ;;
  esac
done
monitoring_requested && WITH_MONITORING=true

step "Checking prerequisites"
check_prerequisites
require_env_file
ok "Docker is running and .env exists"

step "Starting the platform"
compose_args=()
services=("${CORE_SERVICES[@]}")
if $WITH_MONITORING; then
  compose_args+=(--profile monitoring)
  services+=("${MONITORING_SERVICES[@]}")
fi
if ! docker compose "${compose_args[@]}" up -d "${BUILD[@]}" "${services[@]}"; then
  fail "docker compose could not start the platform (the reason is just above)."
  recovery_hints; exit 1
fi

step "Waiting for the services"
healthy=true
wait_for_url "$API/health" 120 "Backend and database" '"database":"ok"' || healthy=false
wait_for_url "$DASHBOARD_URL/" 60 "Dashboard" || healthy=false
if $WITH_MONITORING; then
  wait_for_url "$LOKI_URL_HOST/ready" 90 "Loki" ready || healthy=false
  wait_for_url "$GRAFANA_URL/api/health" 90 "Grafana" || healthy=false
fi
$healthy || { recovery_hints; exit 1; }

lab="$(latest_lab || true)"
if [ -n "$lab" ]; then
  IFS=$'\t' read -r lab_id lab_name lab_status <<<"$lab"
  if $WITH_LAB && [ "$lab_status" != running ]; then
    step "Lab"
    start_lab "$lab_id" "$lab_name" || { recovery_hints; exit 1; }
    lab_status=running
  fi
fi

step "CloudAttackLab is running"
info "Dashboard:   $DASHBOARD_URL"
$WITH_MONITORING && info "Grafana:     $GRAFANA_URL"
if [ -z "$lab" ]; then
  info "Lab:         none yet. Run scripts/setup.sh, or use Create lab on the dashboard."
elif [ "$lab_status" != running ]; then
  info "Lab:         '$lab_name' is $lab_status. Start it on the Lab page, or run scripts/start.sh --lab."
else
  info "Lab:         '$lab_name' is running"
fi
