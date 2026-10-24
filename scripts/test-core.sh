#!/usr/bin/env bash
# Тесты Python-пакетов ядра: каждый пакет отдельным запуском pytest
# (у пакетов свои tests/ и app/, в одном процессе они конфликтуют).
#
#   scripts/test-core.sh                 юнит-тесты всех пакетов
#   scripts/test-core.sh --integration   плюс интеграционные: Postgres и NATS поднимаются в docker
#   scripts/test-core.sh --lint          ruff и mypy для libs/riskcore и libs/svckit
#   scripts/test-core.sh ingest api      только перечисленные пакеты (имя каталога)
#
# Интерпретатор: $PYTHON, иначе .venv в корне репозитория, иначе python3/python.
# Если TEST_DATABASE_URL и TEST_NATS_URL уже заданы, --integration берёт их и docker не трогает.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACKAGES=(libs/riskcore libs/svckit services/migrate services/ingest services/api services/scoring services/simulator services/alerts)

integration=0
lint=0
selected=()
for arg in "$@"; do
  case "$arg" in
    --integration) integration=1 ;;
    --lint) lint=1 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) selected+=("$arg") ;;
  esac
done

if [[ -n "${PYTHON:-}" ]]; then
  py="$PYTHON"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  py="$ROOT/.venv/bin/python"
elif [[ -x "$ROOT/.venv/Scripts/python.exe" ]]; then
  py="$ROOT/.venv/Scripts/python.exe"
elif command -v python3 >/dev/null 2>&1 && python3 -c 'import sys' >/dev/null 2>&1; then
  py=python3
else
  py=python
fi

# Пути для интерпретатора: под Windows (Git Bash) питону нужны C:/... и разделитель ';'.
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else echo "$1"; fi; }
sep="$("$py" -c 'import os; print(os.pathsep)')"
# libs берём из исходников, даже если в site-packages стоит старая установленная копия
export PYTHONPATH="$(native "$ROOT/libs/riskcore")${sep}$(native "$ROOT/libs/svckit")"
export PYTHONDONTWRITEBYTECODE=1

containers=()
cleanup() {
  if ((${#containers[@]})); then
    docker rm -f "${containers[@]}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if ((integration)) && [[ -z "${TEST_DATABASE_URL:-}" || -z "${TEST_NATS_URL:-}" ]]; then
  pg_port="${TEST_PG_PORT:-55432}"
  nats_port="${TEST_NATS_PORT:-54222}"
  suffix="$$"
  echo ">> Postgres :$pg_port и NATS :$nats_port в docker"
  containers+=("churn-test-pg-$suffix" "churn-test-nats-$suffix")
  docker run -d --rm --name "churn-test-pg-$suffix" -p "127.0.0.1:$pg_port:5432" \
    -e POSTGRES_USER=churn -e POSTGRES_PASSWORD=churn -e POSTGRES_DB=churn postgres:16-alpine >/dev/null
  docker run -d --rm --name "churn-test-nats-$suffix" -p "127.0.0.1:$nats_port:4222" nats:2.10-alpine -js >/dev/null
  for _ in $(seq 1 60); do
    if docker exec "churn-test-pg-$suffix" pg_isready -U churn -d churn -h 127.0.0.1 >/dev/null 2>&1; then break; fi
    sleep 1
  done
  export TEST_DATABASE_URL="postgresql://churn:churn@127.0.0.1:$pg_port/churn"
  export TEST_NATS_URL="nats://127.0.0.1:$nats_port"
fi

if ((!integration)); then
  # без инфраструктуры интеграционные тесты пропускаются, а не ждут таймаутов подключения
  export TEST_DATABASE_URL="${TEST_DATABASE_URL:-postgresql://churn:churn@127.0.0.1:1/churn}"
  export TEST_NATS_URL="${TEST_NATS_URL:-nats://127.0.0.1:1}"
fi

failed=()
for pkg in "${PACKAGES[@]}"; do
  name="$(basename "$pkg")"
  if ((${#selected[@]})) && [[ ! " ${selected[*]} " == *" $name "* ]]; then
    continue
  fi
  echo
  echo "=== $pkg"
  marker=()
  ((integration)) || marker=(-m "not integration")
  if ! (cd "$ROOT/$pkg" && "$py" -m pytest -q -p no:cacheprovider "${marker[@]}"); then
    failed+=("$pkg")
  fi
done

if ((lint)); then
  for pkg in libs/riskcore libs/svckit; do
    echo
    echo "=== lint $pkg"
    if ! (cd "$ROOT/$pkg" && "$py" -m ruff check . && "$py" -m ruff format --check . \
          && "$py" -m mypy "$(basename "$pkg")"); then
      failed+=("lint:$pkg")
    fi
  done
fi

echo
if ((${#failed[@]})); then
  echo "Упали: ${failed[*]}"
  exit 1
fi
echo "Все пакеты зелёные"
