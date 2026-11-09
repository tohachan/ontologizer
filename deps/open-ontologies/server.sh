#!/usr/bin/env bash
# Local Open Ontologies server: clone, build and run the engine in Docker.
# Usually called through the Makefile at the repo root (`make help`).
#
#   server.sh status    config, clone, server health and token check, container
#   server.sh up        start the server unless one already answers at OO_URL
#   server.sh clone     clone the engine into OO_SRC if it is not there yet
#   server.sh rebuild   rebuild the image and recreate the container (data kept)
#   server.sh down      stop and remove the container (data volume kept)
#   server.sh logs      last lines of the engine log; extra args go to compose
#
# Config: defaults.conf, then local.conf (this checkout, else the main checkout),
# then the environment. The token lives in .env at the repo root, read through
# scripts/oo; `up` writes a new one there when none exists.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../.." && pwd)
oo="$repo/scripts/oo"
if common=$(git -C "$repo" rev-parse --path-format=absolute --git-common-dir 2>/dev/null); then
  main=$(dirname "$common")
else
  main=$repo
fi

say() { printf 'oo-server: %s\n' "$*"; }
die() { printf 'oo-server: %s\n' "$*" >&2; exit 1; }

# --- config ------------------------------------------------------------------
keys="OO_REPO OO_REF OO_SRC OO_URL"
saved=()
for k in $keys; do
  if [ -n "${!k+x}" ]; then saved+=("$k=${!k}"); fi
done
# shellcheck source=defaults.conf
. "$here/defaults.conf"
local_conf=
for f in "$here/local.conf" "$main/deps/open-ontologies/local.conf"; do
  if [ -f "$f" ]; then local_conf=$f; . "$f"; break; fi
done
for kv in ${saved[@]+"${saved[@]}"}; do printf -v "${kv%%=*}" '%s' "${kv#*=}"; done

case $OO_SRC in
  /*) src=$OO_SRC ;;
  *)  src=$main/$OO_SRC ;;
esac
[ -d "$src" ] && src=$(cd "$src" && pwd)

OO_URL=${OO_URL%/}
hostport=${OO_URL#*://}; hostport=${hostport%%/*}
host=${hostport%:*}
case $hostport in *:*) port=${hostport##*:} ;; *) port=80 ;; esac

# --- probes ------------------------------------------------------------------
healthy() { curl -fsS -o /dev/null --max-time 3 "$OO_URL/health" 2>/dev/null; }

token() { (cd "$repo" && "$oo" token 2>/dev/null); }

# HTTP code of an authenticated request, or "no-token". The token goes to curl
# on stdin, not on the command line.
auth_code() {
  local t
  t=$(token) || { echo no-token; return 0; }
  printf 'Authorization: Bearer %s\n' "$t" |
    curl -s -o /dev/null -w '%{http_code}' --max-time 5 -H @- "$OO_URL/api/stats" || true
}

have_clone() { [ -e "$src/.git" ] && [ -f "$src/Cargo.toml" ]; }

need_docker() {
  command -v docker >/dev/null || die "docker is not installed"
  docker info >/dev/null 2>&1 || die "the Docker daemon is not running"
}

compose() {
  local t
  t=$(token) || t=unset   # enough for ps/logs/down; `up` ensures a real token first
  OPEN_ONTOLOGIES_TOKEN=$t \
  OPEN_ONTOLOGIES_HOST_PORT=$port \
  OO_SRC=$src \
  OO_DOCKERFILE=$here/Dockerfile.service \
    docker compose -f "$here/compose.yaml" -f "$here/compose.localhost.yaml" "$@"
}

# --- steps -------------------------------------------------------------------
ensure_local_url() {
  case $host in
    127.0.0.1|localhost|"[::1]") ;;
    *) die "OO_URL=$OO_URL is not on this machine; start that server where it runs" ;;
  esac
}

ensure_clone() {
  if have_clone; then
    say "engine clone: $src ($(git -C "$src" describe --tags --always 2>/dev/null))"
    return 0
  fi
  if [ -e "$src" ] && [ -n "$(ls -A "$src" 2>/dev/null)" ]; then
    die "$src exists but is not an engine clone; fix OO_SRC (deps/open-ontologies/local.conf)"
  fi
  say "cloning $OO_REPO${OO_REF:+ at $OO_REF} into $src"
  git -c advice.detachedHead=false clone ${OO_REF:+--branch "$OO_REF"} -- "$OO_REPO" "$src"
  src=$(cd "$src" && pwd)
}

ensure_token() {
  local file new tmp
  if token >/dev/null; then return 0; fi
  file=$(cd "$repo" && "$oo" env-file 2>/dev/null) || file=$main/.env
  new=$(openssl rand -hex 32)
  if [ -f "$file" ] && grep -q '^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}OPEN_ONTOLOGIES_TOKEN=' "$file"; then
    tmp=$(mktemp)
    awk -v t="$new" '
      /^[[:space:]]*(export[[:space:]]+)?OPEN_ONTOLOGIES_TOKEN=/ { print "OPEN_ONTOLOGIES_TOKEN=" t; next }
      { print }' "$file" > "$tmp"
    cat "$tmp" > "$file"; rm -f "$tmp"
  else
    (umask 077; printf 'OPEN_ONTOLOGIES_TOKEN=%s\n' "$new" >> "$file")
  fi
  chmod 600 "$file"
  say "wrote a new OPEN_ONTOLOGIES_TOKEN to $file"
}

build_and_start() {
  need_docker
  say "building the image (the first build compiles the engine and takes a while)"
  compose build engine
  compose up -d --wait engine
  case $(auth_code) in
    200) say "running at $OO_URL (MCP at $OO_URL/mcp), token accepted" ;;
    *)   die "the container started but $OO_URL/api/stats does not accept the token; see make oo-logs" ;;
  esac
}

# A server that answers without a container on this Docker daemon runs elsewhere (a VM has its own
# daemon and only forwards the port). Recreating the container here would start a second engine with
# an empty store instead of touching the real one.
ensure_container_here() {
  need_docker
  if healthy && [ -z "$(compose ps -a -q engine 2>/dev/null)" ]; then
    die "the server at $OO_URL has no container on this Docker daemon; run this where the server runs"
  fi
}

# --- commands ----------------------------------------------------------------
cmd_status() {
  echo "config:    defaults.conf${local_conf:+ + ${local_conf#$main/}}"
  echo "repo:      $OO_REPO${OO_REF:+ (fresh clones at $OO_REF)}"
  if have_clone; then
    echo "clone:     $src ($(git -C "$src" describe --tags --always 2>/dev/null))"
  else
    echo "clone:     $src (missing)"
  fi
  if healthy; then
    case $(auth_code) in
      200)      echo "server:    $OO_URL up, token accepted" ;;
      no-token) echo "server:    $OO_URL up, but no OPEN_ONTOLOGIES_TOKEN in .env" ;;
      *)        echo "server:    $OO_URL up, but it rejects the token from .env" ;;
    esac
  else
    echo "server:    $OO_URL not answering"
  fi
  if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
    local c
    c=$(compose ps -a --format '{{.Name}} {{.Status}}' engine 2>/dev/null | head -n1)
    if [ -n "$c" ]; then
      echo "container: $c"
    elif healthy; then
      echo "container: none on this Docker daemon; the server above runs elsewhere (AGENTS.md, Open Ontologies)"
    else
      echo "container: none"
    fi
  else
    echo "container: docker not available"
  fi
}

cmd_up() {
  ensure_local_url
  if healthy; then
    case $(auth_code) in
      200)      say "already running at $OO_URL, token accepted; nothing to do"; return 0 ;;
      no-token) die "a server answers at $OO_URL but .env has no OPEN_ONTOLOGIES_TOKEN; put that server's token there" ;;
      *)        die "a server answers at $OO_URL but rejects the token from .env; not touching it" ;;
    esac
  fi
  ensure_clone
  ensure_token
  build_and_start
}

cmd_rebuild() {
  ensure_local_url
  ensure_container_here
  ensure_clone
  ensure_token
  say "the container will be recreated; the data volume is kept"
  build_and_start
}

cmd=${1:-}; [ $# -gt 0 ] && shift
case "$cmd" in
  status)  cmd_status ;;
  up)      cmd_up ;;
  clone)   ensure_clone ;;
  rebuild) cmd_rebuild ;;
  down)    ensure_container_here; compose down ;;
  logs)    need_docker; compose logs --tail=200 "$@" engine ;;
  *) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; [ -z "$cmd" ] || exit 2 ;;
esac
