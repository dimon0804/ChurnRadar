#!/usr/bin/env bash
# Сценарий демонстрации в kind:
#   1) kubectl scale  2) всплеск нагрузки -> HPA добавляет реплики api
#   3) удаляем под api, панель продолжает отвечать
# DEMO_AUTO=1 — без пауз между шагами; LOAD_SECONDS — длительность нагрузки;
# KIND_HTTP_PORT — порт панели (по умолчанию определяется по kind-кластеру).
set -euo pipefail

cluster="${CLUSTER_NAME:-churn-radar}"
ns="${NAMESPACE:-churn-radar}"
release="${RELEASE:-churn-radar}"
port="${KIND_HTTP_PORT:-$(docker port "$cluster-control-plane" 30080/tcp 2>/dev/null | head -n1 | sed 's/.*://')}"
base="${BASE_URL:-http://localhost:${port:-8080}}"
load_seconds="${LOAD_SECONDS:-120}"
# образ с curl; nats-box уже загружен в узлы скриптом up.sh
load_image="${LOAD_IMAGE:-natsio/nats-box:0.17.0}"
ctx="kind-$cluster"
jar="$(mktemp)"
trap 'rm -f "$jar"; kubectl --context "$ctx" -n default delete pod cr-load --ignore-not-found --wait=false >/dev/null 2>&1 || true' EXIT

k() { kubectl --context "$ctx" "$@"; }
step() { printf '\n\033[1;32m### %s\033[0m\n' "$*"; }
pause() { [[ "${DEMO_AUTO:-0}" == "1" ]] || read -r -p "Enter — дальше..." _; }

# если вместо api работает заглушка ядра, демонстрируем на ней
api_deploy="$release-api"
k -n "$ns" get deploy "$api_deploy" >/dev/null 2>&1 || api_deploy="$release-mock-core"
api_component="${api_deploy#"$release"-}"

step "Состояние кластера"
k get nodes
k -n "$ns" get pods -o wide
k -n "$ns" get hpa,pdb
pause

step "Вход: без сессии API отвечает 401"
curl -s -o /dev/null -w 'GET /api/v1/customers без cookie -> %{http_code}\n' "$base/api/v1/customers"
curl -s -c "$jar" -o /dev/null -w 'POST /api/v1/auth/login -> %{http_code}\n' \
  -H 'Content-Type: application/json' -H "Origin: $base" \
  -d '{"username":"admin","password":"admin"}' "$base/api/v1/auth/login"
curl -s -b "$jar" -o /dev/null -w 'GET /api/v1/customers с cookie -> %{http_code}\n' "$base/api/v1/customers"
pause

step "kubectl scale: web 2 -> 4 реплики и обратно"
k -n "$ns" scale deploy "$release-web" --replicas=4
k -n "$ns" rollout status deploy "$release-web" --timeout 120s
k -n "$ns" get pods -l app.kubernetes.io/component=web -o wide
k -n "$ns" scale deploy "$release-web" --replicas=2
pause

step "Нагрузка на $api_component в течение ${load_seconds}s, наблюдаем HPA"
k -n default delete pod cr-load --ignore-not-found >/dev/null
k -n default run cr-load --image="$load_image" --image-pull-policy=IfNotPresent --restart=Never --command -- sh -c "
  gw=http://traefik.traefik.svc.cluster.local
  curl -s -c /tmp/jar -H 'Content-Type: application/json' -H \"Origin: \$gw\" \
    -d '{\"username\":\"admin\",\"password\":\"admin\"}' \$gw/api/v1/auth/login >/dev/null
  end=\$((\$(date +%s) + $load_seconds))
  worker() { while [ \$(date +%s) -lt \$end ]; do curl -s -b /tmp/jar -o /dev/null \"\$gw/api/v1/customers?page=1&page_size=100\"; done; }
  for i in \$(seq 1 24); do worker & done
  wait
" >/dev/null
end=$(( $(date +%s) + load_seconds + 60 ))
while [[ $(date +%s) -lt $end ]]; do
  k -n "$ns" get hpa "$api_deploy" --no-headers
  sleep 10
done
k -n "$ns" get pods -l "app.kubernetes.io/component=$api_component" -o wide
k -n default delete pod cr-load --ignore-not-found --wait=false >/dev/null
pause

step "Удаляем один под $api_component, запросы продолжают проходить"
victim="$(k -n "$ns" get pods -l "app.kubernetes.io/component=$api_component" -o jsonpath='{.items[0].metadata.name}')"
echo "удаляю $victim"
k -n "$ns" delete pod "$victim" --wait=false
ok=0; fail=0
for _ in $(seq 1 60); do
  code="$(curl -s -b "$jar" -o /dev/null -w '%{http_code}' "$base/api/v1/customers")"
  if [[ "$code" == "200" ]]; then ok=$((ok + 1)); else fail=$((fail + 1)); echo "ответ $code"; fi
  sleep 0.5
done
echo "успешных: $ok, ошибок: $fail"
k -n "$ns" get pods -l "app.kubernetes.io/component=$api_component" -o wide
