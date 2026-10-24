from __future__ import annotations

from prometheus_client import Counter, Gauge
from svckit.metrics import http_duration_histogram

# channel: feed | webhook | telegram | email
SENT = Counter("alerts_sent_total", "Успешно доставленные сообщения", ["channel"])
FAILED = Counter("alerts_failed_total", "Неудачные попытки доставки (каждая попытка)", ["channel"])
DROPPED = Counter("alerts_dropped_total", "Доставки, брошенные после всех попыток", ["channel"])
RAISED = Counter("alerts_raised_total", "Сработавшие критические события (по подписчикам и журналу)", ["kind"])
SUPPRESSED = Counter("alerts_suppressed_total", "События, придержанные анти-спамом", ["kind"])
NOTIFICATIONS = Counter(
    "alerts_notifications_total", "Персональные уведомления, поставленные в канал", ["event", "channel"]
)
SUBSCRIBERS = Gauge("alerts_subscribers", "Пользователи с настройками уведомлений")
TELEGRAM_LINKED = Gauge("alerts_telegram_linked", "Пользователи с привязанным Telegram")
BOT_LEADER = Gauge("alerts_bot_leader", "1 — эта реплика ведёт опрос Telegram (getUpdates)")
BOT_UPDATES = Counter("alerts_bot_updates_total", "Сообщения боту", ["command"])
BOT_POLL_ERRORS = Counter("alerts_bot_poll_errors_total", "Ошибки опроса Telegram getUpdates")
PENDING = Gauge("alerts_delivery_pending", "Доставки в очереди")
EVENTS = Counter("alerts_events_total", "Полученные события scores.updated", ["result"])
ERRORS = Counter("alerts_processing_errors_total", "Ошибки обработки", ["stage"])
RETRIES = Counter("alerts_retries_total", "События, возвращённые на повторную доставку")
DEAD_LETTERS = Counter("alerts_dead_letters_total", "События, ушедшие в DLQ")
LAST_EVENT = Gauge("alerts_last_event_timestamp_seconds", "Время последнего обработанного события")
HTTP_DURATION = http_duration_histogram()

for _channel in ("feed", "webhook", "telegram", "email"):
    SENT.labels(channel=_channel)
    FAILED.labels(channel=_channel)
    DROPPED.labels(channel=_channel)
