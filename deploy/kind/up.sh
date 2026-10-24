#!/usr/bin/env bash
# Поднимает ChurnRadar в локальном kind-кластере:
#   кластер -> metrics-server -> Traefik -> сборка образов -> kind load -> helm upgrade --install
# Переменные окружения:
#   CLUSTER_NAME (churn-radar), NAMESPACE (churn-radar), RELEASE (churn-radar)
#   KIND_HTTP_PORT (8080) — порт панели на хосте, задаётся при создании кластера
#   SKIP_BUILD=1     — не пересобирать образы
#   MOCK_CORE=1|0    — принудительно включить/выключить заглушку ядра
#   HELM_EXTRA_ARGS  — дополнительные аргументы для helm upgrade
#   HELM_TIMEOUT     — сколько ждать готовности релиза (15m)
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
here="$root/deploy/kind"
chart="$root/deploy/helm/churn-radar"

cluster="${CLUSTER_NAME:-churn-radar}"
namespace="${NAMESPACE:-churn-radar}"
release="${RELEASE:-churn-radar}"
host_port="${KIND_HTTP_PORT:-8080}"
tag="dev"
traefik_chart_version="${TRAEFIK_CHART_VERSION:-42.1.3}"
metrics_server_version="${METRICS_SERVER_VERSION:-v0.9.0}"

# локально установленные бинарники (helm/kind/kubeconform) часто лежат в ~/tools/bin
if [[ -d "${TOOLS_BIN:-$HOME/tools/bin}" ]]; then
  PATH="${TOOLS_BIN:-$HOME/tools/bin}:$PATH"
fi

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mОшибка:\033[0m %s\n' "$*" >&2; exit 1; }

# сетевые шаги (pull базовых образов, загрузка в узлы) повторяем при сбоях
retry() {
  local attempt
  for attempt in 1 2 3 4; do
    "$@" && return 0
    log "попытка $attempt не удалась: $*"
    sleep $((attempt * 5))
  done
  return 1
}

for bin in docker kind kubectl helm; do
  command -v "$bin" >/dev/null 2>&1 || die "не найден '$bin' в PATH"
done
docker info >/dev/null 2>&1 || die "Docker не запущен"

# --- кластер ---------------------------------------------------------------
if kind get clusters 2>/dev/null | grep -qx "$cluster"; then
  log "kind-кластер '$cluster' уже существует"
  existing_port="$(docker port "$cluster-control-plane" 30080/tcp 2>/dev/null | head -n1 | sed 's/.*://')"
  if [[ -n "$existing_port" && "$existing_port" != "$host_port" ]]; then
    log "Внимание: кластер создан с портом $existing_port; чтобы сменить порт, выполните down.sh"
  fi
else
  log "Создаю kind-кластер '$cluster' (панель на порту $host_port)"
  cfg="$(mktemp)"
  sed "s/hostPort: 8080/hostPort: $host_port/" "$here/cluster.yaml" > "$cfg"
  kind create cluster --name "$cluster" --config "$cfg" --wait 120s
  rm -f "$cfg"
fi
ctx="kind-$cluster"
kubectl config use-context "$ctx" >/dev/null
k() { kubectl --context "$ctx" "$@"; }

# --- metrics-server (нужен HPA) ---------------------------------------------
if ! k -n kube-system get deploy metrics-server >/dev/null 2>&1; then
  log "Устанавливаю metrics-server $metrics_server_version"
  k apply -f "https://github.com/kubernetes-sigs/metrics-server/releases/download/${metrics_server_version}/components.yaml"
  k -n kube-system patch deploy metrics-server --type=json \
    -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
fi

# --- Traefik (ставит CRD traefik.io до установки нашего чарта) ---------------
log "Устанавливаю Traefik (чарт $traefik_chart_version)"
helm repo add traefik https://traefik.github.io/charts >/dev/null 2>&1 || true
retry helm repo update traefik >/dev/null
helm upgrade --install traefik traefik/traefik \
  --kube-context "$ctx" --namespace traefik --create-namespace \
  --version "$traefik_chart_version" -f "$here/traefik-values.yaml" --wait --timeout 5m
k wait --for=condition=Established --timeout=60s crd/ingressroutes.traefik.io crd/middlewares.traefik.io >/dev/null

# --- образы ------------------------------------------------------------------
images=()
build() { # name context dockerfile
  local name="$1" context="$2" dockerfile="$3"
  local image="churn-radar-$name:$tag"
  if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
    log "Собираю $image"
    retry docker build -t "$image" -f "$root/$dockerfile" "$root/$context"
  else
    docker image inspect "$image" >/dev/null 2>&1 || die "нет образа $image (SKIP_BUILD=1)"
  fi
  images+=("$image")
}

[[ -f "$root/services/auth/Dockerfile" ]] || die "нет services/auth/Dockerfile"
[[ -f "$root/services/notifier/Dockerfile" ]] || die "нет services/notifier/Dockerfile"
[[ -f "$root/web/Dockerfile" ]] || die "нет web/Dockerfile"

build auth services/auth services/auth/Dockerfile
build notifier services/notifier services/notifier/Dockerfile
build web . web/Dockerfile

helm_args=()
core_missing=0
# сервисы ядра собираются из корня: им нужны libs/, contracts/ и data/
for svc in api ingest simulator; do
  if [[ -f "$root/services/$svc/Dockerfile" ]]; then
    build "$svc" . "services/$svc/Dockerfile"
  else
    log "services/$svc/Dockerfile не найден"
    core_missing=1
  fi
done
if [[ -f "$root/services/scoring/Dockerfile" ]]; then
  build scoring . services/scoring/Dockerfile
  build migrate . services/migrate/Dockerfile
else
  log "services/scoring/Dockerfile не найден — scoring не разворачивается"
  helm_args+=(--set scoring.enabled=false)
fi
if [[ -f "$root/services/alerts/Dockerfile" ]]; then
  build alerts . services/alerts/Dockerfile
else
  helm_args+=(--set alerts.enabled=false)
fi

mock_core="${MOCK_CORE:-$core_missing}"
if [[ "$mock_core" == "1" ]]; then
  [[ -f "$root/tests/mock-core/Dockerfile" ]] || die "нужна заглушка ядра, но нет tests/mock-core/Dockerfile"
  log "Включаю mockCore вместо api/ingest/simulator"
  build mock-core . tests/mock-core/Dockerfile
  helm_args+=(--set mockCore.enabled=true)
else
  helm_args+=(--set mockCore.enabled=false)
fi

log "Загружаю образы в kind: ${images[*]}"
retry kind load docker-image --name "$cluster" "${images[@]}"

# Сторонние образы, уже скачанные на хост, отдаём узлам сразу, чтобы не тянуть
# их из Docker Hub трижды. Многоплатформенные образы kind загружает только
# через архив с одной платформой.
case "$(docker info --format '{{.Architecture}}')" in
  aarch64|arm64) platform=linux/arm64 ;;
  *) platform=linux/amd64 ;;
esac
archive_dir="$(mktemp -d)"
for image in postgres:16-alpine redis:7-alpine nats:2.10-alpine natsio/nats-box:0.17.0; do
  docker image inspect "$image" >/dev/null 2>&1 || continue
  archive="$archive_dir/$(echo "$image" | tr '/:' '__').tar"
  if docker save --platform "$platform" -o "$archive" "$image" 2>/dev/null \
    && retry kind load image-archive --name "$cluster" "$archive" >/dev/null 2>&1; then
    log "Предзагружен $image"
  fi
done
rm -rf "$archive_dir"

# --- чарт ----------------------------------------------------------------------
log "helm upgrade --install $release"
existed=0
helm status "$release" --kube-context "$ctx" --namespace "$namespace" >/dev/null 2>&1 && existed=1
# shellcheck disable=SC2086
helm upgrade --install "$release" "$chart" \
  --kube-context "$ctx" --namespace "$namespace" --create-namespace \
  -f "$chart/values-kind.yaml" "${helm_args[@]}" ${HELM_EXTRA_ARGS:-} \
  --wait --timeout "${HELM_TIMEOUT:-15m}"

# тег :dev не меняется, поэтому после пересборки поды нужно перезапустить
if [[ "$existed" == "1" && "${SKIP_BUILD:-0}" != "1" ]]; then
  log "Перезапускаю деплойменты с новыми образами"
  k -n "$namespace" rollout restart deploy -l "app.kubernetes.io/instance=$release" >/dev/null
  for d in $(k -n "$namespace" get deploy -l "app.kubernetes.io/instance=$release" -o name); do
    k -n "$namespace" rollout status "$d" --timeout 5m
  done
fi

k -n "$namespace" get pods -o wide
echo
actual_port="$(docker port "$cluster-control-plane" 30080/tcp 2>/dev/null | head -n1 | sed 's/.*://')"
log "Готово: http://localhost:${actual_port:-$host_port} (логин admin / admin)"
