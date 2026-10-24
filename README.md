<div align="center">

# ChurnRadar

**Русский** · [English](README.en.md)

Анализатор риска оттока для хостинга. Показывает маркетологу, кто из клиентов может
не продлить услуги, почему именно и сколько денег компания потеряет, если ничего не делать.

[![CI](https://github.com/dimon0804/ChurnRadar/actions/workflows/ci.yml/badge.svg)](https://github.com/dimon0804/ChurnRadar/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Go 1.25](https://img.shields.io/badge/Go-1.25-00ADD8?logo=go&logoColor=white)
![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![Docker Compose](https://img.shields.io/badge/Docker%20Compose-v2-2496ED?logo=docker&logoColor=white)

Кейс компании Aéza, хакатон РНД (ДГТУ)

</div>

![Обзор панели](docs/img/screens/overview.png)

## Содержание

- [Быстрый старт](#быстрый-старт)
- [Что посмотреть в панели](#что-посмотреть-в-панели)
- [Как это работает](#как-это-работает)
- [Модель риска](#модель-риска)
- [Проверка качества](#проверка-качества)
- [Как добавить новый признак](#как-добавить-новый-признак)
- [Требования кейса](#требования-кейса)
- [Метрики и мониторинг](#метрики-и-мониторинг)
- [Надёжность](#надёжность)
- [Масштабирование и Kubernetes](#масштабирование-и-kubernetes)
- [API](#api)
- [Разработка и тесты](#разработка-и-тесты)
- [Конфигурация](#конфигурация)
- [Безопасность](#безопасность)
- [Команда](#команда)

## Быстрый старт

Нужен Docker с Compose v2 (проверяли на Docker Desktop с Compose v5.1). Файл `.env`
создавать не нужно: у каждой переменной есть демо-значение. Стенд с тестовым набором
кейса занимает около 1,5 ГБ памяти, так что Docker стоит выделить хотя бы 4 ГБ. Для
расширенных наборов на десятки тысяч клиентов памяти нужно больше, у `ingest` и `scoring`
лимит 3 ГБ на контейнер.

```bash
git clone https://github.com/dimon0804/ChurnRadar.git
cd ChurnRadar
docker compose up -d --build --wait
```

Первый запуск собирает образы, поэтому занимает несколько минут. `--wait` возвращает
управление, когда все сервисы прошли healthcheck. Повторный запуск той же команды на
уже поднятом стенде безопасен: данные в volume сохраняются.

Если установлен `make`, то же самое делает `make up`.

| Адрес | Что там | Вход |
|---|---|---|
| http://localhost:8080 | панель маркетолога | `admin` / `admin` (всё), `analyst` / `analyst` (только просмотр) |
| http://localhost:8080/api/v1/docs | Swagger UI | после входа в панель |
| http://localhost:8080/metrics | метрики Prometheus | заголовок `Authorization: Bearer churn-metrics-demo-token` |
| http://localhost:3000 | Grafana, дашборды «Отток» и «Платформа» | `admin` / `admin` |
| http://localhost:9090 | Prometheus | без входа |

Grafana и Prometheus слушают только `127.0.0.1`. Если порт 8080 занят, поменяйте его
переменной: `HTTP_PORT=18080 PUBLIC_URL=http://localhost:18080 docker compose up -d --build --wait`.

Быстрая проверка из консоли:

```bash
curl -i http://localhost:8080/api/v1/customers         # 401: без входа данных нет
curl -H "Authorization: Bearer churn-metrics-demo-token" http://localhost:8080/metrics | grep churn_
```

Остановить:

```bash
docker compose down        # остановить, данные остаются
docker compose down -v     # остановить и удалить данные
```

Вход через Keycloak (OIDC) поднимается отдельным профилем, подробнее в
[docs/deploy.md](docs/deploy.md):

```bash
docker compose --profile oidc up -d --build --wait     # Keycloak на :8081
```

## Что посмотреть в панели

При первом старте, пока база пуста, `ingest` загружает тестовый набор кейса [`data/sample/data.json`](data/sample/data.json)
(10 клиентов, 30 услуг, 100 транзакций) и считает его на зафиксированный момент
2026-10-19 15:00 UTC, чтобы результат совпадал у всех. На этом наборе получается 4
клиента высокого риска, 2 среднего и 4 без оценки (у них нет действующих услуг), ожидаемая
потеря за 30 дней €2 297,83. Разбор по каждому клиенту есть в
[docs/model.md](docs/model.md#результат-на-тестовом-наборе).

Десяти клиентов мало, чтобы почувствовать панель. Во вкладке «Данные» администратор может
сгенерировать расширенный набор симулятором и включить поток изменений в реальном времени.
То же через API:

```bash
# вход: cookie сессии сохраняется в файл; Origin нужен из-за CSRF-защиты шлюза
curl -c cr.txt -H 'Content-Type: application/json' -H 'Origin: http://localhost:8080' \
  -d '{"username":"admin","password":"admin"}' http://localhost:8080/api/v1/auth/login

# сгенерировать 2000 клиентов вместо текущих данных и включить поток изменений
curl -b cr.txt -X PUT -H 'Content-Type: application/json' -H 'Origin: http://localhost:8080' \
  -d '{"generate":{"accounts":2000,"seed":42}}' http://localhost:8080/api/v1/simulator
curl -b cr.txt -X PUT -H 'Content-Type: application/json' -H 'Origin: http://localhost:8080' \
  -d '{"running":true}' http://localhost:8080/api/v1/simulator

# выключить поток и вернуть данные кейса
curl -b cr.txt -X PUT -H 'Content-Type: application/json' -H 'Origin: http://localhost:8080' \
  -d '{"running":false}' http://localhost:8080/api/v1/simulator
curl -b cr.txt -X PUT -H 'Content-Type: application/json' -H 'Origin: http://localhost:8080' \
  --data-binary @data/sample/data.json http://localhost:8080/api/v1/source/snapshot
```

Снимок без поля `reference_time` считается на текущий момент, поэтому цифры после такой
загрузки немного отличаются от расчёта на 19 октября.

Скриншоты ниже сняты на наборе из 2000 клиентов (seed 42) с включённым потоком.

**Рейтинг клиентов.** Сортировка по ID, email, балансу, пополнениям, риску и ожидаемой
потере, фильтр по уровню риска, поиск по email или ID.

![Таблица клиентов](docs/img/screens/customers.png)

**Карточка клиента.** Score и лояльность, ожидаемая потеря в евро, причины простыми словами
с вкладом каждой в score, разложение оценки по признакам.

![Карточка клиента высокого риска](docs/img/screens/customer.png)

Ниже в той же карточке деньги (выручка в месяц, ожидаемая потеря, худший случай, на сколько
дней хватит баланса) и конкретные действия для маркетолога.

![Деньги и рекомендации](docs/img/screens/customer-money.png)

**Модель.** Коэффициенты признаков, пороги уровней и горизонт прогноза. Правка сохраняется
сразу, создаёт новую версию настроек с историей изменений, и оценки пересчитываются за
секунды. Раздел доступен только роли `admin`.

Если хочется сначала прикинуть последствия, есть переключатель «Что если». В этом режиме
ползунки ничего не сохраняют, а панель сверху показывает, какой станет ожидаемая потеря,
доля выручки под риском и число клиентов на каждом уровне, плюс список тех, кто перейдёт
в высокий риск или выйдет из него. «Применить» сохраняет как обычно, «Отменить» всё
возвращает. Считает scoring тем же кодом, что и настоящий пересчёт, поэтому после
«Применить» цифры совпадают. На базе больше 2000 клиентов считается выборка, и панель
прямо пишет, что итог приблизительный (порог задаёт `PREVIEW_SAMPLE_SIZE` у scoring).

![Редактор коэффициентов](docs/img/screens/model.png)

**Grafana.** Дашборд «Отток» строится по бизнес-метрикам из `/metrics`, «Платформа»
показывает здоровье сервисов, RPS и задержки через шлюз, SSE-клиентов и реплики scoring.

![Grafana: дашборд «Отток»](docs/img/screens/grafana.png)

<details>
<summary>Экран входа и дашборд «Платформа»</summary>

![Вход](docs/img/screens/login.png)

![Grafana: дашборд «Платформа»](docs/img/screens/grafana-platform.png)

</details>

## Как это работает

```mermaid
flowchart TB
    browser(["Браузер"])

    subgraph gw["Traefik :8080"]
        traefik["маршрутизация, ForwardAuth, CSRF по Origin"]
    end

    subgraph platform["Платформа"]
        auth["auth<br/>FastAPI: вход, сессии, OIDC"]
        web["web<br/>React + nginx"]
        notifier["notifier x2<br/>Go, SSE"]
    end

    subgraph core["Ядро"]
        api["api x2<br/>FastAPI: рейтинг, аналитика, настройки, /metrics"]
        ingest["ingest<br/>снимки и поток изменений"]
        scoring["scoring x2<br/>riskcore: score, причины, деньги"]
        simulator["simulator<br/>расширенные данные и живой поток"]
    end

    subgraph data["Хранилища"]
        pg[("PostgreSQL 16<br/>source, scoring, auth")]
        redis[("Redis 7<br/>сессии, лимиты")]
        nats[["NATS JetStream<br/>стрим CHURN"]]
    end

    subgraph obs["Наблюдаемость"]
        prom["Prometheus"]
        graf["Grafana"]
    end

    keycloak["Keycloak<br/>профиль oidc"]

    browser --> traefik
    traefik -. "проверка сессии" .-> auth
    traefik --> web
    traefik --> api
    traefik --> ingest
    traefik --> simulator
    traefik -- "/api/v1/stream" --> notifier
    auth --> redis
    auth -.-> keycloak
    simulator --> ingest
    ingest --> pg
    ingest -- "source.changed" --> nats
    api -- "settings.changed" --> nats
    nats --> scoring
    scoring --> pg
    scoring -- "scores.updated" --> nats
    nats --> notifier
    nats --> api
    api --> pg
    prom -. "/metrics" .-> api
    graf --> prom
```

Все запросы идут через Traefik. Перед каждым маршрутом, кроме входа и статики страницы
логина, шлюз спрашивает `auth` (ForwardAuth): сессия живёт в httpOnly cookie, в Redis лежит
только SHA-256 токена. `auth` возвращает пользователя и его роли заголовками, и сервисы ядра
доверяют только им. Вход по паролю работает всегда, вход через Keycloak (Authorization Code +
PKCE) включается профилем `oidc`.

Путь данных от источника до экрана:

1. Снимок (`PUT /api/v1/source/snapshot`, формат `data.json` из кейса) или пакет изменений
   (`POST /api/v1/source/events`) приходит в `ingest`. Тот пишет данные в PostgreSQL
   (снимок заменяется атомарно), ведёт историю статусов и той же транзакцией кладёт
   `churn.source.changed` в outbox. Оттуда событие сразу забирает фоновый relay и отправляет
   в JetStream.
2. `scoring` читает событие из JetStream через общий durable consumer, поэтому реплики делят
   поток между собой. Обработка идемпотентна по `event_id`, а полный пересчёт идёт под
   `pg_advisory_lock`, чтобы две реплики не считали одно и то же одновременно. После правки
   коэффициентов `api` так же, через outbox, публикует `churn.settings.changed`, и всё
   пересчитывается.
3. Результат (score, причины, деньги, рекомендации) ложится в схему `scoring`, а в шину уходит
   `churn.scores.updated`.
4. `notifier` раздаёт событие браузерам по SSE (`/api/v1/stream`), `api` по нему же сбрасывает
   кэш сводки. Панель перезапрашивает нужные данные и обновляется без перезагрузки страницы.

Реплики `notifier` друг о друге не знают: каждая слушает все события и держит кольцевой буфер
на 200 штук для `Last-Event-ID`. Если реплика упала, браузер через 3 секунды подключается к
соседней. Кроме событий, `scoring` раз в минуту пересчитывает всех, когда данные живут в
реальном времени: срок до окончания услуг уменьшается, даже если в данных ничего не менялось.

Событие не теряется, даже если NATS в момент записи лежит. Раньше `ingest` сначала сохранял
данные, а потом публиковал событие, и при недоступной шине пересчёт просто не случался. Теперь
событие пишется в таблицу `source.outbox` (у `api` — `scoring.outbox`) в одной транзакции с
данными: сохраняется либо и то и другое, либо ничего. Relay отправляет его, как только шина
вернётся, с `Nats-Msg-Id` = `event_id`, поэтому повторная отправка после сбоя не даёт дубля
в JetStream. Подробности в [docs/architecture.md](docs/architecture.md#доставка-событий-transactional-outbox).

Подробно про каждый сервис, модель угроз и отказоустойчивость: [docs/architecture.md](docs/architecture.md).
Схемы событий: [contracts/events/](contracts/events/).

## Модель риска

Модель лежит в библиотеке [`libs/riskcore`](libs/riskcore): чистый Python без зависимостей,
без базы и сети. `scoring` только читает данные, вызывает её и сохраняет результат, поэтому
модель тестируется отдельно от всей системы.

Для каждой действующей услуги (статусы `active`, `suspended`, `prolong_wait`) считается
вероятность, что её не продлят в горизонте 30 дней. Каждый признак выдаёт силу сигнала от 0
до 1 и фразу с конкретными цифрами, у каждого есть коэффициент от 0 до 100:

| Признак | Вес | Уровень | Когда срабатывает |
|---|---|---|---|
| `prolong_wait` | 85 | услуга | услуга ждёт оплаты и через 5 дней будет удалена |
| `autoprolong_off_expiring` | 60 | услуга | автопродление выключено, а срок кончается в горизонте |
| `insufficient_funds` | 55 | услуга | автопродление включено, но денег не хватит на продления |
| `churn_history` | 40 | клиент | клиент уже лишился части услуг |
| `suspended` | 35 | услуга | клиент сам выключил сервер |
| `failed_payments` | 30 | услуга | платежи за продление не проходят |
| `inactivity` | 20 | клиент | платежей нет дольше, чем положено по периоду оплаты |
| `short_commitment` | 15 | услуга | короткий период оплаты: час, неделя, месяц |
| `bonus_dependency` | 15 | клиент | бонусов больше, чем своих денег |
| `withdraw_pressure` | 10 | клиент | часть баланса можно вывести |

Причины по услуге складываются через noisy-OR:

```
p_s = 1 − Π (1 − w_i · x_i)
```

Вес читается буквально: 85 у «ждёт оплаты» значит, что одна эта причина в полную силу даёт
85% вероятности потерять услугу. Результат всегда между 0 и 1, повышение любого веса не может
уменьшить риск, а новый признак добавляет множитель и не сдвигает смысл старых весов.
Обучаемую модель мы не брали сознательно: в данных кейса нет меток ухода и истории статусов.

Score клиента равен ожидаемой доле его выручки, которую мы потеряем за горизонт:
`risk_score = 100 · Σ p_s · rev_s / Σ rev_s`. Лояльность считается как `100 − risk_score`.
Уровни: от 65 высокий риск, от 35 средний, ниже низкий. Если у клиента нет действующих
платных услуг, уровень `unknown`. Пороги, веса и горизонт (от 1 до 365 дней) меняются в
панели.

Деньги. Главное число в карточке `expected_loss = Σ p_s · rev_s`: сколько выручки мы в
среднем потеряем за горизонт, если ничего не делать. Выручка услуги ограничена тем, что
клиент реально способен заплатить, иначе почасовой сервер из тестового набора «приносил» бы
тысячи евро в месяц. Рядом показываются `exposure` (худший случай, если уйдут все услуги
среднего и высокого риска) и на сколько дней хватит баланса.

Вклады причин считаются через логарифмы множителей noisy-OR и в сумме дают ровно score
клиента, без «прочего». У каждой причины есть действие для маркетолога с цифрами из данных,
например «Срочный звонок: service-0020 удалят через 3 дн.». Главное действие `next_action`
выбирается по тому, сколько денег оно сбережёт, если причину устранить.

Полное описание, обоснование весов и разбор тестового набора: [docs/model.md](docs/model.md).

## Проверка качества

Меток ухода в данных кейса нет, поэтому мы проверили модель на синтетическом будущем:
сгенерировали 20 000 клиентов, посчитали риск на момент T и прокрутили 30 дней двумя
независимыми симуляциями (живой поток симулятора и календарь продлений).

![Доля ушедших клиентов по уровням риска](docs/img/calibration/levels.png)

- Клиенты высокого риска уходят полностью в 3,3 раза (поток) и в 5,5 раза (календарь) чаще
  среднего по базе.
- ROC AUC модели 0,765 и 0,932. Это выше любого простого правила вроде «автопродление
  выключено» (0,710 и 0,831).
- Ранжирование надёжное, а абсолютные суммы `expected_loss` на синтетике проверить нельзя:
  две разумные настройки симуляции расходятся в 2,5 раза. Сумму стоит использовать для
  сравнения клиентов между собой, а не как прогноз выручки.

Синтетический мир писали мы же, зная признаки модели, поэтому проверка подтверждает, что
модель правильно читает эти механизмы, но не доказывает, что реальные клиенты уходят по тем
же причинам. Все ограничения и команда для воспроизведения (`python -m tools.calibration`):
[docs/calibration.md](docs/calibration.md).

## Как добавить новый признак

Признак занимает один файл в [`libs/riskcore/riskcore/features/`](libs/riskcore/riskcore/features/).
Схему базы, API, фронтенд и настройки трогать не нужно: пакет сам находит модули в папке, а
декоратор `@register` кладёт признак в реестр.

```python
@register
class NewCustomer(AccountFeature):
    code = "new_customer"
    title = "Новый клиент"
    description = "Клиент с нами меньше 30 дней. Сила убывает к 30-му дню."
    group = "behavior"
    default_weight = 20.0

    def compute(self, ctx: AccountContext) -> Signal | None:
        ...  # сила 0..1 и объяснение с цифрами, None если не сработал

    def recommend(self, signal: Signal) -> Recommendation:
        ...  # что сделать маркетологу
```

После пересборки (`docker compose up -d --build --wait`) признак появится в настройках модели
с весом по умолчанию, войдёт в расчёт, в разложение score, в причины и рекомендации. Общие
тесты сами проверят, что его вклады сходятся в score и что повышение веса не уменьшает риск.
Полный пример с тестом: [docs/adding-feature.md](docs/adding-feature.md).

## Требования кейса

| Требование | Где сделано |
|---|---|
| Веб-панель с рейтингом клиентов, сортировкой и фильтрами | `web/`, страница «Клиенты» |
| Причина риска простыми словами | `reasons[]` в карточке клиента, текст формирует сам признак (`libs/riskcore/riskcore/features/`) |
| Денежная оценка | `expected_loss`, `exposure`, выручка в месяц, сводка и KPI на обзоре ([docs/model.md](docs/model.md#деньги)) |
| Редактируемые коэффициенты с сохранением | страница «Модель», `PATCH /api/v1/model/settings`; версии и история в PostgreSQL, переживают рестарт |
| Автообновление без перезагрузки | NATS JetStream → `scoring` → `scores.updated` → `notifier` (SSE) → панель |
| Эндпоинт `/metrics` | `api`, формат Prometheus, закрыт токеном; дашборды Grafana в `monitoring/` |
| Аутентификация | `services/auth`: пароль (argon2id), роли `admin` и `analyst`, OIDC через Keycloak |
| Запуск через Compose | [`docker-compose.yml`](docker-compose.yml), одна команда без `.env` |
| Документация API | Swagger UI на `/api/v1/docs`, контракт [`contracts/openapi.yaml`](contracts/openapi.yaml) |
| Описание модели и архитектуры | [docs/model.md](docs/model.md), [docs/architecture.md](docs/architecture.md) |
| Как добавить признак | [docs/adding-feature.md](docs/adding-feature.md) |

## Метрики и мониторинг

`/metrics` отдаёт агрегированные показатели по всей базе. Без токена шлюз отвечает `401`.

```bash
curl -H "Authorization: Bearer churn-metrics-demo-token" http://localhost:8080/metrics
```

| Метрика | Смысл |
|---|---|
| `churn_customers_by_risk_level{level}` | клиентов по уровням `high`, `medium`, `low`, `unknown` |
| `churn_customers_total` | клиентов всего |
| `churn_expected_loss_eur` | ожидаемая потеря за горизонт, евро |
| `churn_exposure_eur` | выручка услуг среднего и высокого риска за горизонт |
| `churn_revenue_at_risk_share`, `churn_revenue_at_risk_ratio` | доля выручки горизонта, которую ожидаем потерять (одно и то же число, второе имя по соглашению Prometheus) |
| `churn_risk_score_avg`, `churn_risk_score_max` | средний и наибольший risk-score оценённых клиентов |
| `churn_risk_score_bucket{le}`, `churn_risk_score_customers` | распределение risk-score: клиентов со score не выше `le` (10, 20, …, 100, `+Inf`) и сколько клиентов оценено |
| `churn_monthly_revenue_eur` | месячная выручка с действующих услуг |
| `churn_services_at_risk`, `churn_services_total` | услуги под риском и все действующие |
| `churn_model_version` | версия настроек модели последнего расчёта |
| `churn_last_scored_timestamp_seconds` | время последнего пересчёта |

Все бизнес-метрики берутся из той же сводки, что `GET /api/v1/analytics/summary`, поэтому
совпадают с панелью: деньги — с `expected_loss` и `exposure`, risk-score — с `risk_scores`.
Распределение score — снимок последнего пересчёта, поэтому это gauge с меткой `le`, а не
histogram: число клиентов в корзине после пересчёта может уменьшиться, а histogram в Prometheus
состоит из растущих счётчиков. `histogram_quantile(0.5, churn_risk_score_bucket)` при этом
работает и даёт медиану.

Свои метрики есть у каждого сервиса (`scoring_runs_total`, `scoring_duration_seconds`,
`notifier_connected_clients`, `auth_logins_total` и другие), Prometheus собирает их вместе
с экспортёрами PostgreSQL, Redis и NATS. Правила алертов лежат в
[`monitoring/prometheus/alerts.yml`](monitoring/prometheus/alerts.yml), дашборды Grafana
подключаются автоматически из [`monitoring/grafana/`](monitoring/grafana/).

## Надёжность

Мы проверяли не только что всё работает, но и что будет, когда что-то упадёт. Каждый сценарий
можно повторить на поднятом стенде: `bash scripts/chaos.sh` (или `nats`, `api`, `scoring` по
отдельности).

| Что ломается | Что происходит | Чем проверено |
|---|---|---|
| NATS недоступен в момент записи | `ingest` и `api` пишут событие в outbox в той же транзакции, что и данные. Relay отправит его, как только NATS вернётся, ровно один раз (`Nats-Msg-Id` = `event_id`) | `chaos.sh nats`, интеграционные тесты ingest, api, svckit |
| Падает реплика `api` | Traefik уводит запросы на вторую реплику, панель отвечает без ошибок | `chaos.sh api`: 20 из 20 запросов за 10 с |
| Падает реплика `scoring` посреди пересчёта | Неподтверждённые сообщения через `ack_wait` (60 с) забирает вторая реплика. Пересчёт атомарный, недописанный откатывается | `chaos.sh scoring` |
| `scoring` получил SIGTERM | Новых пачек не берёт, текущую дорабатывает до 45 с, остальное возвращает в очередь через `nak` | интеграционный тест scoring |
| Событие стабильно валит пересчёт | После 10 попыток с паузами 1/5/30/120 с уходит в `churn.dlq.scoring` и не тормозит остальные | интеграционный тест scoring, метрика `scoring_dead_letters_total` |
| Ошибка БД в воркере | Воркер пишет ошибку в лог и повторяет. Если фоновая задача всё же умерла, `/readyz` отдаёт 503 и под перезапускается | юнит-тесты scoring |
| Две реплики `scoring` считают одновременно | `pg_advisory_xact_lock` с `lock_timeout`: считает одна, вторая ждёт или повторяет позже | интеграционный тест scoring |
| Повтор одного и того же события | Идемпотентность по `event_id` (`processed_events` в одной транзакции с результатами) | интеграционный тест scoring |
| Обрыв NATS у `notifier` | После переподключения панели приходит `resync`, и она перечитывает данные целиком | Go-тесты notifier, тест web |
| `ingest` не принял пакет симулятора | Симулятор перечитывает мир из базы и не расходится с ней | тесты simulator |
| Зависший запрос к Postgres | `statement_timeout` и `idle_in_transaction_session_timeout` на сервере | интеграционный тест svckit |

Если что-то застряло, это видно в Grafana на панели «Outbox: в очереди». Срабатывают и алерты:
`OutboxStuck`, `ScoringLagHigh`, `ScoringStale`, `ServiceDown`, `HighErrorRate`.

## Масштабирование и Kubernetes

Прикладные сервисы не хранят состояние: всё лежит в PostgreSQL, Redis и NATS. В Compose
по умолчанию работают по две реплики `api`, `scoring` и `notifier`, число задаётся
переменными `API_REPLICAS`, `SCORING_REPLICAS`, `NOTIFIER_REPLICAS`.

Для Kubernetes есть Helm-чарт [`deploy/helm/churn-radar`](deploy/helm/churn-radar): Deployment
с probes и PodDisruptionBudget на каждый сервис, HPA по CPU для `api` и `scoring` (от 2 до 6
реплик), опционально KEDA по лагу консьюмера JetStream, NetworkPolicy с запретом по
умолчанию, контейнеры без root и с read-only ФС, миграции как pre-install/pre-upgrade hook.
`simulator` и `ingest` обновляются через Recreate, чтобы на время выката не появлялось
второго генератора или второго посева. Redis и NATS знают свои лимиты памяти (`maxmemory`,
лимиты JetStream в `nats.conf`, `GOMEMLIMIT`), у Postgres `/dev/shm` в памяти. `pg_dump`
по расписанию включается через `backup.enabled` и складывает дампы в отдельный PVC. Значения
проверяет `values.schema.json`, нужен Kubernetes 1.30 или новее.

Локально чарт поднимается в kind (нужны kind, kubectl и helm):

```bash
make k8s-up      # кластер из трёх узлов, сборка образов, установка чарта, панель на :8080
make k8s-demo    # kubectl scale, нагрузка и HPA, удаление пода под нагрузкой
make k8s-down
```

То же самое каждый PR проверяет в CI: job `helm-kind` собирает образы, ставит чарт в kind,
гоняет `helm test`, логинится через шлюз, ждёт, пока в сводке появятся клиенты, и вручную
запускает бэкап. Локально после `make k8s-up` можно прогнать `bash deploy/kind/smoke.sh`.

Нагрузочные сценарии k6 запускаются в контейнере внутри сети Compose и идут через шлюз с
настоящей авторизацией: рейтинг клиентов до 200 RPS, поток изменений в `ingest`, 500
одновременных SSE-подключений. Пороги и параметры в [tests/load/README.md](tests/load/README.md).

```bash
bash tests/load/run.sh customers     # или ingest, sse, all
```

Подробнее о развёртывании: [docs/deploy.md](docs/deploy.md), [deploy/k8s/README.md](deploy/k8s/README.md).

## API

Swagger UI открывается по адресу http://localhost:8080/api/v1/docs после входа в панель.
Это та же спецификация, что лежит в [`contracts/openapi.yaml`](contracts/openapi.yaml), типы
фронтенда генерируются из неё. Основные ресурсы:

| Метод и путь | Что делает |
|---|---|
| `POST /api/v1/auth/login` | вход по паролю, ставит cookie `cr_session` |
| `GET /api/v1/customers` | рейтинг с сортировкой, фильтром по уровню, поиском и пагинацией |
| `GET /api/v1/customers/{id}` | карточка: причины, разложение score, деньги, рекомендации, услуги |
| `GET /api/v1/analytics/summary` | сводка по базе, график факта и прогноза, главные причины |
| `GET/PATCH /api/v1/model/settings` | коэффициенты, пороги и горизонт (PATCH меняет только переданные поля); рядом `POST …/reset` и `GET …/history` |
| `POST /api/v1/model/settings/preview` | «что если»: то же тело, что у PATCH, ответ «было → станет», ничего не сохраняет |
| `PUT /api/v1/source/snapshot` | загрузить снимок в формате `data.json`, можно gzip |
| `POST /api/v1/source/events` | пакет изменений в реальном времени |
| `GET /api/v1/stream` | живые обновления (SSE) |
| `GET/PUT /api/v1/simulator` | генерация расширенного набора и демо-поток |

Изменяющие запросы с cookie-сессией должны приходить с заголовком `Origin` того же сайта.
События шины описаны JSON Schema в [`contracts/events/`](contracts/events/), таблица
«кто публикует, кто слушает» в [contracts/README.md](contracts/README.md).

## Разработка и тесты

```text
.
├── services/
│   ├── auth/         вход, сессии, OIDC, ForwardAuth (Python, FastAPI)
│   ├── api/          рейтинг, аналитика, настройки модели, /metrics, Swagger
│   ├── ingest/       приём снимков и потока изменений
│   ├── scoring/      пересчёт риска по событиям
│   ├── simulator/    генератор расширенных данных и живой поток
│   ├── notifier/     SSE-рассылка событий (Go)
│   └── migrate/      SQL-миграции ядра
├── libs/
│   ├── riskcore/     модель риска: признаки, noisy-OR, деньги, рекомендации
│   └── svckit/       общий код сервисов: ошибки, авторизация, шина, БД, метрики
├── web/              панель маркетолога (React 19, Vite, TanStack Query)
├── contracts/        OpenAPI и схемы событий
├── data/sample/      тестовый набор кейса и его генератор
├── db/core/          схема базы ядра
├── deploy/           Traefik, Keycloak, Helm-чарт, kind
├── monitoring/       Prometheus (сбор, алерты) и Grafana (дашборды)
├── tests/            e2e (Playwright), нагрузка (k6)
├── tools/calibration/  проверка качества модели на синтетическом будущем
├── scripts/          запуск тестов, приёмка на чистом клоне
└── docs/             архитектура, модель, калибровка, развёртывание
```

Юнит-тесты Python-пакетов (riskcore, svckit, migrate, ingest, api, scoring, simulator), каждый
пакет отдельным запуском pytest. Нужен Python 3.12 с зависимостями из `requirements-dev.txt`
пакетов:

```bash
bash scripts/test-core.sh                  # юнит-тесты
bash scripts/test-core.sh --integration    # плюс интеграционные, Postgres и NATS поднимаются в docker
bash scripts/test-core.sh riskcore         # только один пакет
```

Остальное через `make`:

```bash
make test         # auth (в docker), notifier (сборка с go test), web (vitest)
make e2e          # Playwright-сценарий приёмки против запущенного стенда
make acceptance   # чистый клон → docker compose up → e2e → down -v
make helm-lint
```

e2e-сценарий проходит то, что будет проверять жюри: без входа ничего не открывается, вход
ведёт на обзор с цифрами, сортировки работают, у клиента высокого риска есть причины и деньги,
правка коэффициента меняет оценки без перезагрузки и переживает рестарт, новый снимок сам
обновляет таблицу, `/metrics` совпадает с панелью, Swagger открывается, аналитик не может
менять модель. Сценарий меняет веса и данные стенда, так что запускайте его не на демо, которое
сейчас смотрят.

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) на каждый push и PR гоняет ruff,
eslint и tsc, hadolint, actionlint и promtool, тесты всех сервисов, сборку образов со сканом
Trivy, `helm lint` с kubeconform, e2e в Compose и установку чарта в kind (на PR и в `main`).
По тегу `v*` образы публикуются в GHCR.

## Конфигурация

Все переменные необязательны, образец с демо-значениями в [`.env.example`](.env.example).
Чтобы поменять, скопируйте его в `.env`.

| Переменная | По умолчанию | Зачем |
|---|---|---|
| `HTTP_PORT`, `PUBLIC_URL` | `8080`, `http://localhost:8080` | порт шлюза и внешний адрес для редиректов и CSRF |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / `admin` | администратор |
| `ANALYST_USERNAME` / `ANALYST_PASSWORD` | `analyst` / `analyst` | аналитик, только просмотр |
| `METRICS_TOKEN` | `churn-metrics-demo-token` | Bearer-токен для `/metrics` и Prometheus |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | `churn` | база |
| `COOKIE_SECURE` | `false` | включить за HTTPS |
| `API_REPLICAS`, `SCORING_REPLICAS`, `NOTIFIER_REPLICAS` | `2` | число реплик |
| `SEED_REFERENCE_TIME` | `2026-10-19T15:00:00Z` | на какой момент считать набор кейса при первом старте |
| `GRAFANA_PORT`, `PROMETHEUS_PORT` | `3000`, `9090` | порты наблюдаемости |
| `OIDC_*`, `KEYCLOAK_*` | см. `.env.example` | вход через Keycloak (профиль `oidc`) |

Пароли и токены по умолчанию годятся только для демо. Для любого другого запуска задайте свои.

## Безопасность

- Все маршруты, кроме входа, закрыты ForwardAuth. Шлюз вычищает входящие заголовки
  `X-User-*` и ставит их только из ответа `auth`, подделать пользователя снаружи нельзя.
- Пароли хешируются argon2id, сессия хранится в httpOnly cookie, в Redis лежит только хеш
  токена. Перебор паролей ограничен: 5 неудачных попыток в минуту на пару IP + логин.
- CSRF: cookie `SameSite=Lax`, а для изменяющих запросов шлюз требует `Origin` того же сайта.
- Роли: `admin` меняет модель, загружает данные и управляет симулятором, `analyst` только
  смотрит. Права проверяются и на шлюзе, и в сервисе.
- Контейнеры приложений работают без root, с read-only корневой ФС, `cap_drop: ALL`,
  `no-new-privileges` и лимитами CPU и памяти.
- `/metrics` закрыт отдельным токеном, Grafana и Prometheus слушают только localhost.
- CSP `default-src 'self'` и `frame-ancestors 'none'` на панели.

Полная модель угроз: [docs/architecture.md](docs/architecture.md#модель-угроз).

## Команда

- Дмитрий ([@dimon0804](https://github.com/dimon0804))
- Никита ([@Fr1bilder](https://github.com/Fr1bilder))
