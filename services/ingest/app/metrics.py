"""Метрики Prometheus сервиса ingest."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from svckit.audit import make_counter
from svckit.metrics import http_duration_histogram
from svckit.outbox import OutboxMetrics

registry = CollectorRegistry(auto_describe=True)

# записи журнала действий (audit.events), которые не удалось сохранить
audit_errors = make_counter(registry)

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

# --- коннекторы (pull) --------------------------------------------------------------------
connector_runs_total = Counter(
    "connector_runs_total", "Запуски коннекторов по результату", ["source", "result"], registry=registry
)
connector_records_total = Counter(
    "connector_records_total", "Записи, принятые коннектором из источника", ["source"], registry=registry
)
connector_http_retries_total = Counter(
    "connector_http_retries_total", "Повторные HTTP-запросы коннектора к источнику", ["source"], registry=registry
)
connector_run_duration = Histogram(
    "connector_run_duration_seconds", "Длительность запуска коннектора", ["source"], registry=registry,
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 900),
)
# Значения из source.connectors: их обновляет каждая реплика ingest, поэтому в правилах — max by (source).
connector_last_success_timestamp = Gauge(
    "connector_last_success_timestamp",
    "Время последнего успешного запуска (unix, с); до первого успеха — момент включения",
    ["source"], registry=registry,
)
connector_enabled = Gauge("connector_enabled", "Коннектор включён (1) или выключен (0)", ["source"], registry=registry)
connector_interval_seconds = Gauge(
    "connector_interval_seconds", "Расписание коннектора: интервал между запусками", ["source"], registry=registry
)
connector_leader = Gauge(
    "connector_leader", "Эта реплика ingest сейчас исполняет коннекторы (1) или ждёт (0)", registry=registry
)
