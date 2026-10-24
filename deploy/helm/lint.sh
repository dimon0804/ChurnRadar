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

echo "OK"
