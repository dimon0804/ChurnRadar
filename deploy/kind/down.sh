#!/usr/bin/env bash
# Удаляет kind-кластер ChurnRadar вместе со всеми данными.
set -euo pipefail

cluster="${CLUSTER_NAME:-churn-radar}"
if [[ -d "${TOOLS_BIN:-$HOME/tools/bin}" ]]; then
  PATH="${TOOLS_BIN:-$HOME/tools/bin}:$PATH"
fi

if kind get clusters 2>/dev/null | grep -qx "$cluster"; then
  kind delete cluster --name "$cluster"
else
  echo "kind-кластер '$cluster' не найден"
fi
