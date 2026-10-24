from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

CHANGES = Counter("simulator_changes_total", "Сгенерированные изменения", ["kind"])
BATCHES = Counter("simulator_batches_total", "Пакеты, отправленные в ingest")
RUNNING = Gauge("simulator_running", "Поток изменений включён")
GENERATE_SECONDS = Histogram(
    "simulator_generate_seconds",
    "Генерация и загрузка снимка",
    buckets=(0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)
