#!/usr/bin/env bash
# Show what is running and whether it answers: containers, health endpoints and the lab.
#
#   scripts/status.sh
#
# Read-only. Exits 0 when the platform (database, backend, dashboard) is healthy, 1 otherwise.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

case "${1:-}" in
  "") ;;
  -h|--help) sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) die "Unknown option: $1 (scripts/status.sh takes none)" ;;
esac

check_prerequisites
[ -f .env ] || warn "No .env file: run scripts/setup.sh."

show() {  # show SERVICE ROLE
  local state; state="$(service_state "$1")"
  case "$state" in
    healthy|running) ok "$(printf '%-9s %-10s %s' "$1" "$state" "$2")" ;;
    missing) info "$(printf '[--] %-9s %-10s %s' "$1" "not created" "$2")" ;;
    *) fail "$(printf '%-9s %-10s %s' "$1" "$state" "$2")" ;;
  esac
}

step "Platform (basic mode)"
show postgres "database (volume $DB_VOLUME: $(db_volume_exists && echo present || echo absent))"
show backend  "API on $API"
show frontend "dashboard on $DASHBOARD_URL"

step "Monitoring (optional, profile 'monitoring')"
show loki    "logs on $LOKI_URL_HOST"
show alloy   "ships container logs to Loki"
show grafana "dashboards on $GRAFANA_URL"
legacy_monitoring_hint

step "Health checks"
platform_ok=true
if body="$(curl -fsS --max-time 3 "$API/health" 2>/dev/null)"; then
  ok "Backend: $body"
else
  fail "Backend does not answer at $API/health"; platform_ok=false
fi
if curl -fsS --max-time 3 -o /dev/null "$DASHBOARD_URL/" 2>/dev/null; then
  ok "Dashboard answers at $DASHBOARD_URL"
else
  fail "Dashboard does not answer at $DASHBOARD_URL"; platform_ok=false
fi
if monitoring_running; then
  ready="$(curl -sS --max-time 3 "$LOKI_URL_HOST/ready" 2>/dev/null | head -n 1)"
  [ "$ready" = ready ] && ok "Loki: ready" || warn "Loki: ${ready:-no answer} (it needs about 20 s after starting)"
fi

step "Lab"
if $platform_ok || curl -fsS --max-time 3 "$API/health" >/dev/null 2>&1; then
  lab="$(latest_lab || true)"
  if [ -z "$lab" ]; then
    info "No lab yet. Create one with scripts/setup.sh or on the dashboard's Lab page."
  else
    IFS=$'\t' read -r lab_id lab_name _ <<<"$lab"
    # Reading one lab makes the backend compare its stored status with Docker.
    api_get "/labs/$lab_id" | python3 -c '
import json, sys
lab = json.load(sys.stdin)
detail = " (%s)" % lab["status_detail"] if lab.get("status_detail") else ""
print("    Lab %r: %s%s" % (lab["name"], lab["status"], detail))
for c in lab.get("containers") or []:
    print(("      %-15s %s %s" % (c["service"], c["state"], c.get("health") or "")).rstrip())
target = lab.get("target_health") or {}
print("    ShopCloud: %s" % ("healthy" if target.get("healthy") else (target.get("error") or "not healthy")))
falco = lab.get("falco") or {}
source = (lab.get("telemetry") or {}).get("source", "?")
print("    Telemetry source: %s    Falco: %s" % (source, "on" if falco.get("enabled") else "off"))
' || warn "Could not read lab '$lab_name' from the backend."
  fi
else
  info "Unknown: the backend is not answering."
fi

if $platform_ok; then
  printf '\n%sThe platform is up.%s Dashboard: %s\n' "$C_GREEN" "$C_OFF" "$DASHBOARD_URL"
else
  printf '\n%sThe platform is not fully up.%s Start it with scripts/start.sh, or see:\n' "$C_RED" "$C_OFF"
  info "docker compose logs --tail=80 backend"
  info "docs/troubleshooting.md"
  exit 1
fi
