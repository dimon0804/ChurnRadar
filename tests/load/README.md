# Нагрузочные тесты (k6)

Сценарии гоняются в контейнере `grafana/k6` внутри сети docker compose и идут
через шлюз (`http://traefik:8080`), то есть с ForwardAuth, как у настоящего пользователя.

| сценарий | файл | что делает | пороги |
|---|---|---|---|
| Рейтинг клиентов | `customers.js` | разгон до 200 RPS на `GET /api/v1/customers` со случайными `sort`, `order`, `page`, `page_size`, `level` | p95 < 300 мс, ошибок < 1% |
| Поток изменений | `ingest.js` | `POST /api/v1/source/events` пакетами по 5 аккаунтов, 20 пакетов/с | p95 < 500 мс, ошибок < 1% |
| Живые обновления | `sse.js` | 500 одновременных подключений к `GET /api/v1/stream` | ≥ 99% соединений удержаны до конца |

Логин делается один раз в `setup()` (`POST /api/v1/auth/login`, по умолчанию `admin/admin`),
cookie `cr_session` раздаётся всем VU. Для небезопасных запросов добавляется заголовок
`Origin` — этого требует CSRF-защита шлюза.

## Запуск

```bash
docker compose up -d --wait            # стек должен быть поднят
# для 100k аккаунтов сначала сгенерировать расширенный набор симулятором:
#   PUT /api/v1/simulator {"generate": {"accounts": 100000, "seed": 42}}

tests/load/run.sh customers
tests/load/run.sh ingest
tests/load/run.sh sse
tests/load/run.sh all
```

Параметры передаются переменными окружения:

| переменная | по умолчанию | для |
|---|---|---|
| `BASE_URL` | `http://traefik:8080` | все |
| `USERNAME` / `PASSWORD` | `admin` / `admin` | все |
| `TARGET_RPS`, `HOLD`, `MAX_PAGE` | `200`, `2m`, `20` | customers |
| `RATE`, `DURATION`, `BATCH` | `20`, `2m`, `5` | ingest |
| `CLIENTS`, `HOLD_SECONDS`, `DURATION` | `500`, `60`, `3m` | sse |
| `K6_NETWORK` | `churn-radar_default` | сеть compose |
| `K6_IMAGE` | `grafana/k6:0.57.0` | образ k6 |

Дополнительные аргументы после имени сценария уходят в `k6 run`, например
`tests/load/run.sh customers --quiet`. Сводка каждого прогона пишется в
`tests/load/results/<сценарий>-summary.json` (каталог в `.gitignore`).

Пример короткого прогона: `TARGET_RPS=50 HOLD=30s tests/load/run.sh customers`.

## Как устроен SSE-сценарий

В стандартном образе k6 нет клиента Server-Sent Events: он есть только в
сборке с расширением [xk6-sse](https://github.com/phymbert/xk6-sse), которую
надо собирать через `xk6 build --with github.com/phymbert/xk6-sse`. Чтобы тест
запускался в обычном `grafana/k6`, каждый VU делает `GET /api/v1/stream`
с таймаутом `HOLD_SECONDS`. Сервер держит поток открытым, k6 обрывает его по
таймауту (код ошибки 1050) — такое соединение считается удержанным (`sse_held`).
Быстрый `401`/`502` или обрыв со стороны сервера в успех не попадают.

k6 пишет предупреждение `request timeout` на каждое удержанное соединение — это
ожидаемо. Чтобы не засорять вывод, можно добавить `--log-output=none`.

Сколько клиентов реально подключено, видно в Grafana: дашборд «Платформа», панель
«Подключённые SSE-клиенты» (`notifier_connected_clients`, по репликам notifier).

## Что смотреть во время прогона

- Grafana → «Платформа»: RPS и p95 через шлюз, 5xx, лаг консьюмера NATS,
  длительность пересчёта, SSE-клиенты.
- Grafana → «Отток»: «Последний пересчёт» не должен уходить за 30 с при потоке ingest.
- `docker stats` — CPU и память контейнеров.

## Шаблон для docs/load-test.md

```markdown
# Нагрузочное тестирование

Дата: YYYY-MM-DD, коммит: `abcdef0`
Стенд: docker compose, N CPU / M ГБ RAM, scoring × 2, notifier × 2
Данные: 100 000 аккаунтов (симулятор, seed 42)

## Результаты

| сценарий | нагрузка | RPS факт | p50 | p95 | p99 | ошибки | порог |
|---|---|---|---|---|---|---|---|
| GET /api/v1/customers | 200 RPS, 2 мин | | | | | | p95 < 300 мс ✅/❌ |
| POST /api/v1/source/events | 20 пакетов/с × 5 | | | | | | p95 < 500 мс |
| SSE /api/v1/stream | 500 клиентов, 3 мин | — | — | — | — | удержано X% | ≥ 99% |

Полный пересчёт 100k аккаунтов: X с (`churn_scoring_duration_seconds`, p95).
Задержка от события ingest до обновления в панели: X с.

## Графики

![RPS и p95](img/load-rps-p95.png)
![Лаг NATS и длительность пересчёта](img/load-nats-scoring.png)
![SSE-клиенты](img/load-sse.png)

## Выводы

- Узкое место: ...
- Масштабирование: `docker compose up -d --scale scoring=4` / HPA в Kubernetes — ...
```
