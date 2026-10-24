# Контракты

- `openapi.yaml` — REST API всех сервисов (единая спецификация, её же отдаёт `/api/v1/docs`).
- `events/` — JSON Schema событий шины NATS JetStream.

| Subject | Кто публикует | Кто слушает | SSE-событие |
|---|---|---|---|
| `churn.source.changed` | ingest | scoring, notifier | `source.updated` |
| `churn.settings.changed` | api | scoring, notifier | `settings.updated` |
| `churn.scores.updated` | scoring | notifier, api (сброс кэша) | `scores.updated` |

Стрим JetStream: `CHURN` (subjects `churn.>`), хранение file, retention limits, max_age 24h.
Обработчики идемпотентны по `event_id`.

`ingest` и `api` публикуют через transactional outbox (`source.outbox`, `scoring.outbox`):
событие записано вместе с данными и уйдёт, даже если NATS в тот момент недоступен. Доставка
«хотя бы один раз», `Nats-Msg-Id` равен `event_id`. Повтор того же события возможен, так что
идемпотентность потребителей обязательна. Формат конверта не изменился.

Кроме событий шины, notifier шлёт в SSE служебное `resync` (без `id`,
`data: {"reason": "nats_connected" | "nats_reconnected" | "unknown_last_event_id"}`).
Подписка notifier на NATS обычная (core): то, что опубликовано во время обрыва связи, до
него не дойдёт. Поэтому после восстановления связи и при неизвестном `Last-Event-ID`
клиент получает `resync` и перечитывает данные целиком.

Изменение контракта — только отдельным PR с пометкой `contract:` и сообщением команде.
