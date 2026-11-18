#!/usr/bin/env bash
# Stop every CloudAttackLab container: the lab, the monitoring stack and the platform.
#
#   scripts/stop.sh
#
# Keeps everything: containers are stopped, not removed, and the database volume,
# your .env and the built images stay. Start again with scripts/start.sh.
#
# The lab is stopped through the backend when it answers, so the dashboard's lab
# status stays right; otherwise directly with Docker Compose (the backend then
# corrects the status the next time the lab is read).

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

case "${1:-}" in
  "") ;;
  -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) die "Unknown option: $1 (scripts/stop.sh takes none)" ;;
esac

step "Checking prerequisites"
check_prerequisites
ok "Docker is running"

step "Stopping the lab"
stopped_by_api=false
if curl -fsS --max-time 3 "$API/health" >/dev/null 2>&1; then
  labs="$(api_get /labs | lab_lines active)" || labs=""
  stopped_by_api=true
  while IFS=$'\t' read -r id name _; do
    [ -n "$id" ] || continue
    if api_post "/labs/$id/stop" "" 120 >/dev/null; then
      ok "Lab '$name' stopped through the backend"
    else
      warn "The backend could not stop lab '$name'; stopping its containers directly."
      stopped_by_api=false
    fi
  done <<<"$labs"
fi
# Also catches containers left by a lab the backend no longer knows about, and Falco.
docker compose -f "$LAB_COMPOSE_FILE" --profile falco stop >/dev/null 2>&1 || true
$stopped_by_api || info "Stopped the lab's containers directly (the backend was not reachable)."
ok "No lab container is running"

step "Stopping the platform and the monitoring stack"
if docker compose --profile monitoring stop; then
  ok "Stopped. Data kept: database volume, .env, images."
else
  die "docker compose stop failed (the reason is just above)."
fi

left="$(docker ps --format '{{.Names}}' | grep -E "^(${PROJECT}|${LAB_PROJECT})-" || true)"
if [ -n "$left" ]; then
  warn "Still running: $(echo "$left" | tr '\n' ' ')"
  exit 1
fi
ok "Nothing from CloudAttackLab is running. Start again with scripts/start.sh"
