#!/usr/bin/env bash
# Factory reset: remove every CloudAttackLab container and network, and DELETE
# the database. Use it to start over from nothing; it cannot be undone.
#
#   scripts/reset.sh             asks you to type "delete" first
#   scripts/reset.sh --yes       no question (for scripts)
#   scripts/reset.sh --images    also remove the images built from this repository
#
# DELETED: the database volume (normally cloudattacklab_pgdata): every lab, run, event,
#          alert, incident, response and audit record; the lab's containers
#          (ShopCloud's own data); the monitoring containers and their logs.
# KEPT:    your .env, the code, and (without --images) the built images.
#
# Exactly what it runs, scoped to this project's Compose project names:
#   docker compose -f lab/docker-compose.lab.yml --profile falco down --remove-orphans
#   docker compose --profile monitoring down -v --remove-orphans
# (each with --rmi local when --images is given: only images built from this
# repository; pulled images such as postgres or grafana are kept). No other
# project's containers, volumes or images are touched. The database volume is
# shared by every checkout of this repository on the same Docker host, because
# they all use the same project name.
#
# Not what you want?
#   - To stop everything and keep the data:      scripts/stop.sh
#   - To put only the lab back to its baseline:  Reset on the dashboard's Lab page
# Afterwards, run scripts/setup.sh to start again with an empty database.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() { sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; }

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
info ""
info "Compose projects: $PROJECT (platform, monitoring) and $LAB_PROJECT (lab)"
containers="$(docker ps -a --format '{{.Names}}' \
  --filter "label=com.docker.compose.project=$PROJECT" ; docker ps -a --format '{{.Names}}' \
  --filter "label=com.docker.compose.project=$LAB_PROJECT")"
if [ -n "$containers" ]; then
  info "Containers to remove: $(echo "$containers" | tr '\n' ' ')"
else
  info "Containers to remove: none"
fi
if db_volume_exists; then
  info "Volume to DELETE:     $DB_VOLUME (created $(docker volume inspect -f '{{.CreatedAt}}' "$DB_VOLUME"))"
else
  info "Volume to DELETE:     none ($DB_VOLUME does not exist)"
fi
[ "${#RMI[@]}" -eq 0 ] || info "With --images: the images built for $PROJECT and $LAB_PROJECT are removed too."

if ! $ASSUME_YES; then
  [ -t 0 ] || die "Not a terminal, so nothing was deleted. Pass --yes to confirm without a question."
  printf '\n    Type %sdelete%s to continue, anything else to cancel: ' "$C_BOLD" "$C_OFF"
  read -r answer || answer=""
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
