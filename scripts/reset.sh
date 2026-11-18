#!/usr/bin/env bash
# Factory reset: remove every CloudAttackLab container and network, and DELETE
# the database. Use it to start over from nothing; it cannot be undone.
#
#   scripts/reset.sh             asks you to type "delete" first
#   scripts/reset.sh --yes       no question (for scripts)
#   scripts/reset.sh --images    also remove the images built from this repository
#
# DELETED: the database volume (cloudattacklab_pgdata): every lab, run, event,
#          alert, incident, response and audit record; the lab's containers
#          (ShopCloud's own data); the monitoring containers and their logs.
# KEPT:    your .env, the code, and (without --images) the built images.
#
# Not what you want?
#   - To stop everything and keep the data:      scripts/stop.sh
#   - To put only the lab back to its baseline:  Reset on the dashboard's Lab page
# Afterwards, run scripts/setup.sh to start again with an empty database.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() { sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; }

ASSUME_YES=false
RMI=()
for arg in "$@"; do
  case "$arg" in
    --yes) ASSUME_YES=true ;;
    --images) RMI=(--rmi local) ;;
    -h|--help) usage; exit 0 ;;
    *) usage; die "Unknown option: $arg" ;;
  esac
done

check_prerequisites

step "This deletes data"
usage | sed -n '8,11p' | sed 's/^/    /'
if db_volume_exists; then
  info ""
  info "Database volume found: $DB_VOLUME (created $(docker volume inspect -f '{{.CreatedAt}}' "$DB_VOLUME"))"
fi
[ "${#RMI[@]}" -eq 0 ] || info "With --images: the backend, frontend, ShopCloud and Falco images are removed too."

if ! $ASSUME_YES; then
  [ -t 0 ] || die "Not a terminal, so nothing was deleted. Pass --yes to confirm without a question."
  printf '\n    Type %sdelete%s to continue, anything else to cancel: ' "$C_BOLD" "$C_OFF"
  read -r answer
  [ "$answer" = delete ] || { info "Cancelled. Nothing was changed."; exit 0; }
fi

step "Removing the lab"
docker compose -f "$LAB_COMPOSE_FILE" --profile falco down --remove-orphans "${RMI[@]}" || die "Could not remove the lab (see above). Nothing else was removed."
ok "Lab removed"

step "Removing the platform, the monitoring stack and the database"
docker compose --profile monitoring down -v --remove-orphans "${RMI[@]}" || die "docker compose down failed (see above)."
if db_volume_exists; then
  die "The volume $DB_VOLUME is still there (is a container outside this project using it?)."
fi
ok "Removed. The database is gone; .env is kept."

step "Done"
info "Start again with an empty database: scripts/setup.sh"
info "Your .env still holds the same passwords; the new database is created with POSTGRES_PASSWORD from it."
