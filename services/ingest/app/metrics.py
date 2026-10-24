"""Метрики Prometheus сервиса ingest."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram
from svckit.metrics import http_duration_histogram
from svckit.outbox import OutboxMetrics

registry = CollectorRegistry(auto_describe=True)

snapshots_total = Counter(
    "ingest_snapshots_total", "Принятые снимки исходных данных", ["kind"], registry=registry
)
change_batches_total = Counter(
    "ingest_change_batches_total", "Принятые пакеты изменений", registry=registry
)
records_total = Counter(
    "ingest_records_total", "Записанные записи исходных данных", ["entity"], registry=registry
)
status_changes_total = Counter(
    "ingest_status_changes_total", "Записи в истории статусов услуг", registry=registry
)
# отправка churn.source.changed из source.outbox: глубина очереди, отправлено, ошибки
outbox = OutboxMetrics.create(registry)
write_duration = Histogram(
    "ingest_write_duration_seconds", "Время записи в БД", ["operation"], registry=registry,
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
parse_duration = Histogram(
    "ingest_parse_duration_seconds", "Разбор и проверка тела снимка", registry=registry,
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
http_duration = http_duration_histogram(registry)
