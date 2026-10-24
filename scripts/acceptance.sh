#!/usr/bin/env bash
# Приёмка на чистом клоне: clone → docker compose up → e2e → down -v.
#   scripts/acceptance.sh [git-url] [branch]
# HTTP_PORT меняет порт панели, KEEP=1 оставляет стенд поднятым после прогона.
set -euo pipefail

REPO_URL="${1:-$(git config --get remote.origin.url)}"
BRANCH="${2:-$(git rev-parse --abbrev-ref HEAD)}"
HTTP_PORT="${HTTP_PORT:-8080}"
PROJECT="churn-acceptance"
WORKDIR="$(mktemp -d)"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

cleanup() {
  local code=$?
  if [[ "${KEEP:-0}" != "1" ]]; then
    log "Останавливаю стенд"
    (cd "$WORKDIR/repo" && docker compose -p "$PROJECT" $COMPOSE_FILES --profile sso down -v --remove-orphans) >/dev/null 2>&1 || true
    rm -rf "$WORKDIR"
  fi
  if [[ $code -eq 0 ]]; then log "Приёмка пройдена"; else log "Приёмка НЕ пройдена (код $code)"; fi
}
COMPOSE_FILES="-f docker-compose.yml"
trap cleanup EXIT

log "Клонирую $REPO_URL ($BRANCH) в $WORKDIR"
git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$WORKDIR/repo"
cd "$WORKDIR/repo"

if [[ ! -f services/api/Dockerfile ]]; then
  COMPOSE_FILES="-f docker-compose.yml -f deploy/compose/docker-compose.mock.yml"
  log "services/api ещё нет — поднимаю с mock-core"
fi

log "docker compose up -d (без .env и правок)"
started=$(date +%s)
HTTP_PORT="$HTTP_PORT" PUBLIC_URL="http://localhost:$HTTP_PORT" \
  docker compose -p "$PROJECT" $COMPOSE_FILES up -d --build --wait --wait-timeout 300
log "Стенд готов за $(( $(date +%s) - started )) с"

log "Проверяю, что без входа ничего не открывается"
code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$HTTP_PORT/api/v1/customers")
[[ "$code" == "401" ]] || { echo "ожидался 401, получен $code"; exit 1; }

log "e2e-сценарий"
cd tests/e2e
npm ci --no-audit --no-fund
npx playwright install chromium
BASE_URL="http://localhost:$HTTP_PORT" COMPOSE_PROJECT="$PROJECT" COMPOSE_FILES="$COMPOSE_FILES" \
  COMPOSE_DIR="$WORKDIR/repo" npx playwright test
