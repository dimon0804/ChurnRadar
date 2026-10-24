# Архитектура ChurnRadar

## Схема

```
                 ┌────────────────────── Traefik :8080 ──────────────────────┐
  браузер ─────► │ /api/v1/auth/*        → auth           (без ForwardAuth)   │
                 │ /api/v1/stream        → notifier (SSE) ┐                   │
                 │ /api/v1/source/*      → ingest         │ ForwardAuth       │
                 │ /api/v1/simulator     → simulator      ├─► auth            │
                 │ /api/v1/alerts        → alerts         │   /internal/verify│
                 │ /api/v1/*, /docs      → api            │                   │
                 │ /                     → web            ┘                   │
                 │ /metrics              → api  (ForwardAuth: METRICS_TOKEN)   │
                 └────────────────────────────────────────────────────────────┘

  ingest ──► NATS JetStream  churn.source.changed  ──► scoring (N реплик)
  api    ──► NATS JetStream  churn.settings.changed ──► scoring
  scoring ─► NATS JetStream  churn.scores.updated  ──► notifier (M реплик) ──SSE──► браузеры
                                                   ├─► api (сброс кэша)
                                                   └─► alerts (K реплик) ──► лента, webhook, Telegram

  PostgreSQL 16: схемы source, scoring, alerts, auth · Redis: сессии, лимиты, кэш
  Prometheus ◄── /metrics всех сервисов и экспортёров · Grafana · Authentik (OIDC и SAML, профиль sso)
```

Сервисы общаются событиями. Конверт события и схемы полезной нагрузки лежат в
`contracts/events/`, REST-контракт в `contracts/openapi.yaml`.

## Сервисы платформы

### auth

FastAPI. Пользователи в схеме `auth` PostgreSQL (миграции Alembic, отдельный одноразовый
контейнер `migrate`, в Kubernetes это pre-install/pre-upgrade hook). Пароли хешируются
argon2id. Сессия — случайный токен 256 бит в httpOnly cookie `cr_session`; в Redis лежит
только SHA-256 токена, TTL 12 часов, продлевается при каждом запросе.

Для Traefik сервис отдаёт `GET /internal/verify`: по cookie или `Authorization: Bearer`
отвечает `200` с заголовками `X-User-Id`, `X-User-Name`, `X-User-Roles` либо `401`. Для
навигации браузера (`?redirect=1`) вместо `401` отдаётся `302` на `/login?next=…`, поэтому
незалогиненный пользователь видит форму входа, а не JSON.

OIDC: Authorization Code + PKCE (S256) против любого провайдера (в демо — Authentik), адреса
из discovery, `state` одноразовый и хранится в Redis 10 минут, `nonce` сверяется с id_token,
подпись проверяется по JWKS. Обмен кода и JWKS идут по внутреннему адресу IdP с публичным
Host, чтобы `iss` совпал с тем, что видит браузер.

SAML 2.0: SP-initiated вход по HTTP-Redirect, ACS принимает HTTP-POST. Подпись ассерции
проверяет signxml сертификатом из метаданных IdP; дальше читается только подписанный фрагмент,
в ответе должна быть ровно одна ассерция. Проверяются Issuer, Audience, Recipient,
NotBefore/NotOnOrAfter и InResponseTo (ID нашего AuthnRequest лежит в Redis и забирается один
раз), ID ассерции запоминается в Redis до её истечения.

После SSO создаётся та же сессия, что и при входе по паролю. Роль — из claim `roles`, атрибута
`churn-radar-role` или групп IdP; без подходящей группы вход запрещён. Пользователь заводится
при первом входе (JIT), заблокированный в панели не войдёт и через SSO. Если IdP не отвечает,
`GET /api/v1/auth/providers` отдаёт `enabled=false`, кнопки скрыты, вход по паролю работает
всегда.

Управление пользователями (только admin): `/api/v1/auth/users` — список с поиском, создание,
правка, блокировка, сброс пароля, удаление; журнал в `auth.audit_log`. Сессии пользователя
индексируются в Redis (`cr:usess:<id>`), поэтому блокировка и смена роли закрывают их сразу.

### notifier

Go, стандартная библиотека и nats.go. Каждая реплика держит обычную core-подписку на три
subject'а, поэтому получает все события и раздаёт их своим SSE-клиентам — реплики не
координируются и масштабируются горизонтально. Кольцевой буфер последних 200 событий
обслуживает `Last-Event-ID`. Медленный клиент с переполненным буфером отключается и
переподключается сам, не тормозя остальных. При остановке реплика закрывает все потоки,
браузеры переподключаются к соседней через 3 секунды (`retry: 3000`).

### alerts

Python, FastAPI и nats-py, как scoring. Оповещения о росте риска оттока: свой durable
consumer `alerts` на `churn.scores.updated`, свой subject DLQ `churn.dlq.alerts`, своя схема
`alerts` в PostgreSQL. Ни один существующий сервис ради него не менялся: scoring публикует
событие для всех, alerts просто ещё один подписчик.

На каждую пачку событий сервис под `pg_advisory_xact_lock` сравнивает `scoring.customer_scores`
(только чтение) с `alerts.customer_state` одним запросом и решает, кому слать: переход в
высокий риск, рост ожидаемой потери клиента больше порога, рост общей потери больше порога в
процентах. В той же транзакции пишет ленту `alerts.feed`, очередь `alerts.deliveries` и
отметку `alerts.processed_events`, поэтому повтор события ничего не задваивает. Внешние каналы
(webhook, Telegram) разбирает фоновый цикл: берёт доставки через `FOR UPDATE SKIP LOCKED` с
арендой, при ошибке откладывает по backoff, после последней попытки помечает `failed`. Лента
для панели — `GET /api/v1/alerts` за ForwardAuth, как остальные API.

### web

React 19 + Vite, статика отдаётся nginx-unprivileged. Состояние сервера в TanStack
Query; события SSE инвалидируют нужные запросы, поэтому страница обновляется без
перезагрузки. Типы API генерируются из `contracts/openapi.yaml`.

## Модель угроз

| Угроза | Защита |
|---|---|
| Доступ без входа | Все маршруты, кроме входа, статики логина и SSO-редиректов (OIDC, SAML ACS), проходят ForwardAuth. `/metrics` закрыт отдельным токеном. |
| Подмена личности заголовками `X-User-*` | Gateway вычищает эти заголовки из входящего запроса и выставляет их только из ответа auth. Сервисы ядра доверяют им, потому что доступны только из внутренней сети (в Kubernetes это закреплено NetworkPolicy). |
| Перебор паролей | Не больше 5 неудачных попыток в минуту на пару IP + логин (Redis), затем `429` с `Retry-After`; плюс общий rate limit на маршрут входа в Traefik. Время ответа для несуществующего логина то же, что для неверного пароля. |
| CSRF | Cookie `SameSite=Lax`; для POST/PUT/PATCH/DELETE с cookie-сессией gateway требует `Origin`/`Referer` того же сайта. Запросы с Bearer-токеном проверку не проходят, им CSRF не грозит. |
| Кража сессии из Redis | В Redis хранится хеш токена, не сам токен. Cookie httpOnly, `Secure` включается переменной `COOKIE_SECURE` за HTTPS. |
| Повышение прав | Изменение модели, загрузка данных и симулятор требуют роли `admin`: проверяется и на gateway, и в сервисе. |
| Open redirect после входа | `next` принимается только как относительный путь. |
| XSS / clickjacking | CSP `default-src 'self'`, `frame-ancestors 'none'`, `X-Content-Type-Options`. |
| Секреты в репозитории | Только демо-значения по умолчанию и `.env.example`; в Kubernetes — Secret или `existingSecret`. |

## Масштабирование

- Все прикладные сервисы stateless: состояние в PostgreSQL, Redis и NATS.
- `scoring` — конкурирующие потребители одного durable consumer JetStream: добавление
  реплик делит поток пересчётов. В Kubernetes его масштабирует HPA по CPU, опционально
  KEDA по лагу консьюмера.
- `api` масштабируется HPA по CPU, ответы кэшируются и сбрасываются по `scores.updated`.
- `notifier` — каждая реплика независима, нагрузка распределяется балансировщиком по
  соединениям.
- `alerts` — реплики делят события через свой durable consumer, состояние клиентов меняется
  под advisory lock, доставки во внешние каналы разбираются через `SKIP LOCKED`.
- Новый потребитель событий подключается без правок в остальных: свой durable consumer на
  нужный subject, своя схема в базе. Так добавлен alerts.

## Отказоустойчивость

| Что упало | Что происходит |
|---|---|
| Реплика scoring | JetStream передоставит неподтверждённое сообщение другой реплике; обработка идемпотентна по `event_id`. |
| Реплика notifier | Браузер переподключается к другой реплике, при переподключении панель перезапрашивает данные. |
| NATS | Издатели и подписчики переподключаются сами, readiness notifier становится `503` и трафик уходит на живые реплики. События `ingest` и `api` ждут в outbox и уходят, когда шина вернётся. |
| auth | Защищённые маршруты отвечают ошибкой, а не пропускают без проверки (fail closed). |
| Redis | Новые входы невозможны, readiness auth `503`; данные панели не теряются. |
| Реплика alerts | Неподтверждённое событие передоставит JetStream, взятую доставку через минуту (аренда) заберёт соседняя реплика. |
| Webhook или Telegram недоступен | Оповещение уже в ленте, доставка повторяется по backoff, после 8 попыток — `failed`; алерт `AlertsDeliveryFailing`. |
| Перезапуск api/scoring | Коэффициенты модели в PostgreSQL, после рестарта действуют те же. |

В Kubernetes у каждого сервиса liveness/readiness/startup probes, PodDisruptionBudget и
разнесение реплик по узлам.

## Доставка событий: transactional outbox

`churn.source.changed` и `churn.settings.changed` нельзя терять: без них `scoring` не узнает,
что поменялись данные или коэффициенты. Поэтому сервис не публикует событие сам после записи,
а кладёт его в таблицу outbox той же транзакцией, что и данные. Закоммитились данные —
закоммитилось и событие, откатились — события тоже нет.

| Кто пишет | Таблица | Событие |
|---|---|---|
| `ingest` (снимок, сид, поток изменений) | `source.outbox` | `churn.source.changed` |
| `api` (PATCH и сброс настроек модели) | `scoring.outbox` | `churn.settings.changed` |

Таблица лежит в схеме, которую сервис и так пишет (миграция `db/core/002_outbox.sql`).
Отправляет события relay из `libs/svckit/svckit/outbox.py`, он работает фоном в каждой реплике:

- после коммита обработчик будит relay, и событие уходит сразу. Ещё relay сам заглядывает
  в таблицу раз в `OUTBOX_INTERVAL_S` секунд (по умолчанию 5) — на случай, если запись
  сделала другая реплика или пинок пришёлся на паузу;
- пачку он берёт через `SELECT ... FOR UPDATE SKIP LOCKED`, так что реплики не шлют одно и то
  же. Внутри пачки идёт по порядку и на первой ошибке останавливается;
- конверт тот же, что и раньше (`contracts/events/envelope.schema.json`): `event_id` и
  `occurred_at` берутся из строки outbox, а `Nats-Msg-Id` равен `event_id`. Если relay упал
  после публикации, но до отметки `published_at`, повтор отсекает дедупликация JetStream
  (окно две минуты), а дальше страхует идемпотентность `scoring` по `event_id`;
- нет NATS — пауза растёт от 0,5 до `OUTBOX_BACKOFF_MAX_S` секунд (по умолчанию 30), но как
  только соединение вернулось, relay её не досиживает;
- таблицы ещё нет (в Kubernetes миграции и сервисы стартуют одновременно) — relay ждёт, а не
  падает;
- отправленные строки старше `OUTBOX_RETENTION_S` (сутки) удаляются.

Гарантия — «хотя бы один раз» с дедупликацией по `event_id`. Метрики на `/metrics` у `ingest`
и `api`: `outbox_pending_events`, `outbox_oldest_pending_seconds`, `outbox_published_total`,
`outbox_publish_errors_total`. Если `outbox_oldest_pending_seconds` растёт, события копятся
и не уходят.

Заодно у пулов Postgres появились таймауты сессии: `DB_STATEMENT_TIMEOUT_MS` и
`DB_IDLE_IN_TRANSACTION_TIMEOUT_MS`, у `ingest` и `api` по 60 секунд. У `scoring` по 10 минут:
полный пересчёт большой базы держит транзакцию открытой, пока считают воркеры.
