#!/usr/bin/env bash
# The visualizer container (docs/visualizer.md, Deployment). Usually called through the Makefile.
#
#   viz.sh up       build the image and start the visualizer beside the engine
#   viz.sh down     stop and remove the visualizer's container (the engine is not touched)
#   viz.sh status   container, address and whether it answers
#   viz.sh logs     last lines of its log; extra args go to compose
#
# Works the same from the main checkout and from a worktree: the generated files go under the main
# checkout's var/visualizer/, the owners come from the hub's registry. The engine is reached as
# `engine` when its container runs on this Docker daemon, else through host.docker.internal (a VM
# whose daemon has no engine). VIZ_STORE_URL overrides that; VIZ_PORT (default 8090) is the port
# on 127.0.0.1.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../.." && pwd)
if common=$(git -C "$repo" rev-parse --path-format=absolute --git-common-dir 2>/dev/null); then
  main=$(dirname "$common")
else
  main=$repo
fi
out=$main/var/visualizer
port=${VIZ_PORT:-8090}
project=open-ontologies

say() { printf 'viz: %s\n' "$*"; }
die() { printf 'viz: %s\n' "$*" >&2; exit 1; }

need_docker() {
  command -v docker >/dev/null || die "docker is not installed"
  docker info >/dev/null 2>&1 || die "the Docker daemon is not running"
}

container_of() {
  docker ps -aq --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=$1"
}

oo_url() {
  # The hub's store.url (its ontologizer.local.yaml may override it), as the kit itself reads it.
  (cd "$repo" && uv run --quiet python -c \
    'from pathlib import Path; from ontologizer.core.config import read_hub; print((read_hub(Path(".").resolve()).get("store") or {}).get("url") or "http://127.0.0.1:8080")')
}

store_url() {
  if [ -n "${VIZ_STORE_URL:-}" ]; then echo "$VIZ_STORE_URL"; return; fi
  if [ -n "$(docker ps -q --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=engine")" ]; then
    echo "http://engine:8080"; return
  fi
  local u hp
  u=$(oo_url)
  curl -fsS -o /dev/null --max-time 3 "$u/health" 2>/dev/null ||
    die "no engine container on this Docker daemon and nothing answers at $u; start the engine first (make oo-status)"
  hp=${u#*://}; hp=${hp%%/*}
  echo "http://host.docker.internal:${hp##*:}"
}

compose() {
  local files=(-f "$here/compose.yaml")
  [ -f "$out/compose.mounts.yaml" ] && files+=(-f "$out/compose.mounts.yaml")
  COMPOSE_IGNORE_ORPHANS=1 VIZ_CONTEXT=$repo VIZ_PORT=$port VIZ_UID=$(id -u) VIZ_GID=$(id -g) \
    docker compose --project-directory "$here" "${files[@]}" "$@"
}

cmd_up() {
  need_docker
  local token url
  token=$(cd "$repo" && scripts/oo token 2>/dev/null) || die "no OPEN_ONTOLOGIES_TOKEN (see make oo-status)"
  url=$(store_url)
  (cd "$repo" && uv run --quiet python -m ontologizer.adapters.visualization.deploy \
      --hub "$repo" --out "$out" --store-url "$url")
  say "engine for the container: $url"
  OPEN_ONTOLOGIES_TOKEN=$token compose up -d --build --no-deps --wait visualizer
  say "running at http://127.0.0.1:$port/ (only the visualizer's container was created or replaced)"
}

cmd_down() {
  need_docker
  if [ -z "$(container_of visualizer)" ]; then say "not running"; return 0; fi
  compose rm --stop --force visualizer
}

cmd_status() {
  need_docker
  local c
  c=$(docker ps -a --filter "label=com.docker.compose.project=$project" \
        --filter "label=com.docker.compose.service=visualizer" --format '{{.Names}} {{.Status}}' | head -n1)
  echo "container: ${c:-none on this Docker daemon}"
  echo "address:   http://127.0.0.1:$port/"
  if curl -fsS -o /dev/null --max-time 3 "http://127.0.0.1:$port/visualizer/api/health" 2>/dev/null; then
    echo "answers:   yes ($(curl -fsS --max-time 3 "http://127.0.0.1:$port/visualizer/api/health"))"
  else
    echo "answers:   no"
  fi
  echo "generated: $out"
}

cmd=${1:-}; [ $# -gt 0 ] && shift
case "$cmd" in
  up)     cmd_up ;;
  down)   cmd_down ;;
  status) cmd_status ;;
  logs)   need_docker; compose logs --tail=200 "$@" visualizer ;;
  *) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; [ -z "$cmd" ] || exit 2 ;;
esac
