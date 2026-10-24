#!/usr/bin/env bash
# helm lint + проверка отрендеренных манифестов kubeconform (строгий режим).
# Переменные: HELM, KUBECONFORM — пути к бинарникам; KUBE_VERSION — версия схем k8s.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chart="$here/churn-radar"
helm_bin="${HELM:-helm}"
kubeconform_bin="${KUBECONFORM:-kubeconform}"
kube_version="${KUBE_VERSION:-1.33.0}"
crd_catalog='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

render() {
  local name="$1"; shift
  echo "==> helm template ($name)" >&2
  "$helm_bin" template churn-radar "$chart" --namespace churn-radar --kube-version "$kube_version" "$@"
}

validate() {
  "$kubeconform_bin" -strict -summary -kubernetes-version "$kube_version" \
    -schema-location default -schema-location "$crd_catalog" \
    -ignore-missing-schemas -
}

echo "==> helm lint"
"$helm_bin" lint "$chart" --strict
"$helm_bin" lint "$chart" --strict -f "$chart/values-kind.yaml"

render default | validate
render kind -f "$chart/values-kind.yaml" | validate
render mock-core -f "$chart/values-kind.yaml" --set mockCore.enabled=true | validate
render full --set keda.enabled=true --set serviceMonitor.enabled=true --set nats.streams.enabled=true \
  --set postgresql.enabled=false --set postgresql.external.url=postgresql://u:p@db.example:5432/churn | validate
# повторная установка (upgrade) рендерит миграции как pre-upgrade хук
render upgrade --is-upgrade | validate
# бэкап во внешнюю БД с готовым PVC, PriorityClass для всех подов
render backup-external --set backup.enabled=true --set backup.persistence.existingClaim=pg-backups \
  --set backup.timeZone=Europe/Moscow --set global.priorityClassName=churn-radar-high \
  --set postgresql.enabled=false --set postgresql.external.url=postgresql://u:p@db.example:5432/churn | validate

# values.schema.json должна отклонять заведомо неверные значения
for bad in "ingest.strategy.type=BlueGreen" "backup.retention=0" "redis.maxmemoryPolicy=lru"; do
  if "$helm_bin" lint "$chart" --set "$bad" >/dev/null 2>&1; then
    echo "schema пропустила неверное значение: $bad" >&2
    exit 1
  fi
done
echo "==> schema отклоняет неверные значения"

echo "OK"
