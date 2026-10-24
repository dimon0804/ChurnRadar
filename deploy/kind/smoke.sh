#!/usr/bin/env bash
# Дымовая проверка развёрнутого в kind релиза через шлюз Traefik:
#   все Deployment доступны -> вход admin -> сводка содержит клиентов -> рейтинг не пустой.
# Переменные: BASE_URL (http://localhost:8080), NAMESPACE (churn-radar), RELEASE (churn-radar),
#   ADMIN_USERNAME/ADMIN_PASSWORD (admin/admin), SEED_TIMEOUT — сколько секунд ждать посев (480).
set -euo pipefail

base="${BASE_URL:-http://localhost:8080}"
namespace="${NAMESPACE:-churn-radar}"
release="${RELEASE:-churn-radar}"
user="${ADMIN_USERNAME:-admin}"
password="${ADMIN_PASSWORD:-admin}"
seed_timeout="${SEED_TIMEOUT:-480}"

log() { printf '==> %s\n' "$*"; }

log "Deployment релиза доступны"
kubectl -n "$namespace" wait deploy -l "app.kubernetes.io/instance=$release" \
  --for=condition=Available --timeout=5m
kubectl -n "$namespace" get pods -o wide

jar="$(mktemp)"
trap 'rm -f "$jar"' EXIT

log "без сессии API отвечает 401"
code="$(curl -s -o /dev/null -w '%{http_code}' "$base/api/v1/customers")"
test "$code" = 401 || { echo "ожидался 401, получен $code" >&2; exit 1; }

log "вход $user"
# CSRF-защита auth требует Origin своего сайта для запросов с cookie
for attempt in $(seq 1 30); do
  code="$(curl -s -o /dev/null -w '%{http_code}' -c "$jar" \
    -H "Origin: $base" -H 'Content-Type: application/json' \
    -d "{\"username\":\"$user\",\"password\":\"$password\"}" \
    "$base/api/v1/auth/login")"
  [ "$code" = 200 ] && break
  echo "login: $code, попытка $attempt"
  sleep 5
done
test "$code" = 200 || { echo "вход не удался: $code" >&2; exit 1; }

log "ждём посев и первую оценку (до ${seed_timeout}s)"
deadline=$(( $(date +%s) + seed_timeout ))
total=0
while :; do
  summary="$(curl -fsS -b "$jar" "$base/api/v1/analytics/summary" || true)"
  total="$(printf '%s' "$summary" | jq -r '.customers_total // 0' 2>/dev/null || echo 0)"
  [ "${total:-0}" -gt 0 ] && break
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "за ${seed_timeout}s клиенты в сводке не появились: $summary" >&2
    exit 1
  fi
  sleep 10
done
log "в сводке клиентов: $total"

log "рейтинг клиентов не пустой"
customers="$(curl -fsS -b "$jar" "$base/api/v1/customers?page_size=5")"
printf '%s' "$customers" | jq -e '.items | length > 0' >/dev/null \
  || { echo "пустой рейтинг: $customers" >&2; exit 1; }
printf '%s' "$customers" | jq -c '{total, first: .items[0].id}'
log "OK"
