#!/usr/bin/env bash
# Хаос-проверка поднятого compose-стенда: роняем куски системы и смотрим, что данные всё равно доходят.
#   scripts/chaos.sh            — все сценарии подряд
#   scripts/chaos.sh nats|api|scoring
# BASE_URL меняет адрес панели (по умолчанию http://localhost:8080).
# Поток симулятора на время проверки ставится на паузу и потом включается обратно. Меняет баланс клиента 5; после прогона вернуть стенд: Shift+D → «Вернуть стенд в исходное состояние».
set -euo pipefail

BASE="${BASE_URL:-http://localhost:8080}"
JAR="$(mktemp)"
CUSTOMER=5
trap 'rm -f "$JAR"' EXIT

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok() { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31m  ✗ %s\033[0m\n' "$*"; exit 1; }

req() {  # req METHOD PATH [JSON]
  curl -sS -b "$JAR" -c "$JAR" -X "$1" "$BASE$2" -H "Origin: $BASE" -H 'Content-Type: application/json' \
    ${3:+--data "$3"}
}

json() { python -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

login() {
  req POST /api/v1/auth/login '{"username":"admin","password":"admin"}' >/dev/null
  req GET "/api/v1/customers/$CUSTOMER" | json 'd["id"]' >/dev/null || fail "не вошёл в панель $BASE"
}

balance() { req GET "/api/v1/customers/$CUSTOMER" | json 'd["balance"]'; }
set_balance() {
  req POST /api/v1/source/events "{\"accounts\":[{\"id\":$CUSTOMER,\"email\":\"user-0005@example.test\",\"balance\":$1,\"bonusBalance\":0,\"bonusState\":\"locked\",\"withdrawBalance\":0,\"totalReplenished\":21978}]}"
}

db_balance() {
  docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT balance FROM source.accounts WHERE id='"$CUSTOMER"'"'
}

wait_balance() {  # панель показывает баланс из пересчитанных оценок, значит пересчёт дошёл
  for _ in $(seq "${2:-90}"); do
    [[ "$(balance)" == "$1" ]] && return 0
    sleep 1
  done
  return 1
}

scenario_nats() {
  log "NATS лежит, а данные приходят"
  docker compose stop nats >/dev/null 2>&1
  ok "NATS остановлен"
  local value=$((RANDOM + 1000))
  set_balance "$value" >/dev/null
  [[ "$(db_balance)" == "$value" ]] && ok "ingest принял изменение и сохранил в Postgres (баланс $value)"     || fail "изменение не записано"
  docker compose start nats >/dev/null 2>&1
  ok "NATS снова поднят"
  wait_balance "$value" 90 && ok "событие дошло, scoring пересчитал клиента, панель показывает баланс $value"     || fail "панель не обновилась: событие потерялось (в панели $(balance), в базе $value)"
}

scenario_api() {
  log "Падает одна из двух реплик api, панель продолжает отвечать"
  local victim; victim="$(docker compose ps -q api | head -1)"
  docker kill "$victim" >/dev/null
  ok "реплика api убита"
  local errors=0 code
  for _ in $(seq 20); do
    code="$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$BASE/api/v1/analytics/summary")"
    [[ "$code" == 200 ]] || errors=$((errors + 1))
    sleep 0.5
  done
  [[ $errors -eq 0 ]] && ok "за 10 с все 20 запросов к панели прошли" || fail "ошибок: $errors из 20"
  docker start "$victim" >/dev/null
  ok "реплика api поднята обратно"
}

scenario_scoring() {
  log "Падает реплика scoring посреди пересчёта"
  local value=$((RANDOM + 1000))
  set_balance "$value" >/dev/null
  local victim; victim="$(docker compose ps -q scoring | head -1)"
  docker kill "$victim" >/dev/null
  ok "одна реплика scoring убита"
  wait_balance "$value" 90 && ok "вторая реплика довела пересчёт, панель показывает баланс $value"     || fail "пересчёта не было"
  docker start "$victim" >/dev/null
  ok "реплика scoring поднята обратно"
}

stream_was_on=""
pause_stream() {
  stream_was_on="$(req GET /api/v1/simulator | json 'd.get("running")')"
  [[ "$stream_was_on" == "True" ]] && req PUT /api/v1/simulator '{"running":false}' >/dev/null && ok "поток симулятора на паузе"
  return 0
}
resume_stream() {
  [[ "$stream_was_on" == "True" ]] && req PUT /api/v1/simulator '{"running":true}' >/dev/null
  return 0
}

login
pause_stream
trap 'docker compose start nats >/dev/null 2>&1; resume_stream; rm -f "$JAR"' EXIT
case "${1:-all}" in
  nats) scenario_nats ;;
  api) scenario_api ;;
  scoring) scenario_scoring ;;
  all) scenario_nats; scenario_api; scenario_scoring ;;
  *) echo "сценарии: nats | api | scoring | all"; exit 2 ;;
esac
log "Готово"
