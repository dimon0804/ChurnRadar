#!/usr/bin/env bash
# Запуск k6-сценариев в контейнере grafana/k6 внутри сети docker compose.
#   tests/load/run.sh customers|ingest|sse|all [доп. аргументы k6]
# Переменные: BASE_URL (по умолчанию http://traefik:8080), K6_NETWORK, K6_IMAGE,
# а также параметры сценариев (TARGET_RPS, RATE, CLIENTS, HOLD_SECONDS ...).
set -euo pipefail

cd "$(dirname "$0")"
DIR="$(pwd)"
NETWORK="${K6_NETWORK:-churn-radar_default}"
IMAGE="${K6_IMAGE:-grafana/k6:0.57.0}"
BASE_URL="${BASE_URL:-http://traefik:8080}"

# Git Bash на Windows иначе переписывает пути внутри контейнера
export MSYS_NO_PATHCONV=1

usage() {
  echo "usage: $0 customers|ingest|sse|all [k6 args]" >&2
  exit 2
}

[ $# -ge 1 ] || usage
target="$1"
shift

if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  echo "network $NETWORK not found: start the stack with 'docker compose up -d' first" >&2
  exit 1
fi

mkdir -p results
chmod 777 results 2>/dev/null || true

run_one() {
  local name="$1"
  shift
  [ -f "$DIR/$name.js" ] || usage
  echo "==> k6 $name against $BASE_URL"
  local env_args=()
  for var in BASE_URL USERNAME PASSWORD TARGET_RPS HOLD MAX_PAGE RATE DURATION BATCH CLIENTS HOLD_SECONDS; do
    if [ -n "${!var:-}" ]; then env_args+=(-e "$var=${!var}"); fi
  done
  docker run --rm -i \
    --network "$NETWORK" \
    --ulimit nofile=65536:65536 \
    -v "$DIR:/scripts" \
    -w /scripts \
    "${env_args[@]}" \
    "$IMAGE" run \
    --summary-export "/scripts/results/$name-summary.json" \
    "$@" "/scripts/$name.js"
}

case "$target" in
  customers|ingest|sse) run_one "$target" "$@" ;;
  all)
    status=0
    for s in customers ingest sse; do run_one "$s" "$@" || status=$?; done
    exit "$status"
    ;;
  *) usage ;;
esac
