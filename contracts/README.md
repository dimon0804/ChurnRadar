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

Изменение контракта — только отдельным PR с пометкой `contract:` и сообщением команде.
