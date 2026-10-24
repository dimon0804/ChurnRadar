from __future__ import annotations

from prometheus_client import Counter, Gauge
from svckit.metrics import http_duration_histogram

# channel: feed | webhook | telegram
SENT = Counter("alerts_sent_total", "Успешно доставленные сообщения с оповещениями", ["channel"])
FAILED = Counter("alerts_failed_total", "Неудачные попытки доставки (каждая попытка)", ["channel"])
DROPPED = Counter("alerts_dropped_total", "Доставки, брошенные после всех попыток", ["channel"])
RAISED = Counter("alerts_raised_total", "Созданные оповещения", ["kind"])
SUPPRESSED = Counter("alerts_suppressed_total", "Оповещения, придержанные анти-спамом", ["kind"])
PENDING = Gauge("alerts_delivery_pending", "Доставки в очереди")
EVENTS = Counter("alerts_events_total", "Полученные события scores.updated", ["result"])
ERRORS = Counter("alerts_processing_errors_total", "Ошибки обработки", ["stage"])
RETRIES = Counter("alerts_retries_total", "События, возвращённые на повторную доставку")
DEAD_LETTERS = Counter("alerts_dead_letters_total", "События, ушедшие в DLQ")
LAST_EVENT = Gauge("alerts_last_event_timestamp_seconds", "Время последнего обработанного события")
HTTP_DURATION = http_duration_histogram()

for _channel in ("feed", "webhook", "telegram"):
    SENT.labels(channel=_channel)
    FAILED.labels(channel=_channel)
    DROPPED.labels(channel=_channel)
