<div align="center">

# ChurnRadar

[Русский](README.md) · **English**

Churn risk analyzer for a hosting provider. It shows a marketer which customers may not renew
their services, why exactly, and how much money the company loses if nobody does anything.

[![CI](https://github.com/dimon0804/ChurnRadar/actions/workflows/ci.yml/badge.svg)](https://github.com/dimon0804/ChurnRadar/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Go 1.25](https://img.shields.io/badge/Go-1.25-00ADD8?logo=go&logoColor=white)
![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![Docker Compose](https://img.shields.io/badge/Docker%20Compose-v2-2496ED?logo=docker&logoColor=white)

Aéza case, RND hackathon (DSTU)

</div>

![Panel overview](docs/img/screens/overview.png)

The panel itself, the model texts and the docs in `docs/` are in Russian, since the product is
built for a Russian-speaking marketing team. This README covers everything needed to run and
evaluate the project.

## Contents

- [Quick start](#quick-start)
- [What to look at in the panel](#what-to-look-at-in-the-panel)
- [How it works](#how-it-works)
- [Risk model](#risk-model)
- [Quality check](#quality-check)
- [Adding a new feature](#adding-a-new-feature)
- [Case requirements](#case-requirements)
- [Metrics and monitoring](#metrics-and-monitoring)
- [Reliability](#reliability)
- [Scaling and Kubernetes](#scaling-and-kubernetes)
- [API](#api)
- [Development and tests](#development-and-tests)
- [Configuration](#configuration)
- [Security](#security)
- [Team](#team)

## Quick start

You need Docker with Compose v2 (we tested on Docker Desktop with Compose v5.1). There is no
need to create a `.env` file: every variable has a demo default. The stack with the case
sample data uses about 1.5 GB of memory, so give Docker at least 4 GB. Extended datasets with
tens of thousands of customers need more; `ingest` and `scoring` are limited to 3 GB per
container.

```bash
git clone https://github.com/dimon0804/ChurnRadar.git
cd ChurnRadar
docker compose up -d --build --wait
```

The first run builds the images, so it takes a few minutes. `--wait` returns once every service
has passed its healthcheck. Running the same command again on a stack that is already up is
safe: data stays in volumes.

If you have `make`, `make up` does the same thing.

| Address | What is there | Login |
|---|---|---|
| http://localhost:8080 | marketer panel | `admin` / `admin` (full access), `analyst` / `analyst` (read only) |
| http://localhost:8080/api/v1/docs | Swagger UI | after signing in to the panel |
| http://localhost:8080/metrics | Prometheus metrics | header `Authorization: Bearer churn-metrics-demo-token` |
| http://localhost:3000 | Grafana, dashboards "Отток" (churn) and "Платформа" (platform) | `admin` / `admin` |
| http://localhost:9090 | Prometheus | no login |

Grafana and Prometheus listen on `127.0.0.1` only. If port 8080 is taken, change it with a
variable: `HTTP_PORT=18080 PUBLIC_URL=http://localhost:18080 docker compose up -d --build --wait`.

Quick check from the console:

```bash
curl -i http://localhost:8080/api/v1/customers         # 401: no data without signing in
curl -H "Authorization: Bearer churn-metrics-demo-token" http://localhost:8080/metrics | grep churn_
```

To stop:

```bash
docker compose down        # stop, data is kept
docker compose down -v     # stop and delete data
```

Sign-in through Keycloak (OIDC) runs as a separate profile, see [docs/deploy.md](docs/deploy.md):

```bash
docker compose --profile oidc up -d --build --wait     # Keycloak on :8081
```

## What to look at in the panel

On the first start, while the database is empty, `ingest` loads the case sample
[`data/sample/data.json`](data/sample/data.json) (10 customers, 30 services, 100 transactions)
and scores it at a fixed moment, 2026-10-19 15:00 UTC, so everyone gets the same result. On
this set there are 4 high-risk customers, 2 medium and 4 without a score (they have no active
services), and the expected loss over 30 days is €2,297.83. The per-customer breakdown is in
[docs/model.md](docs/model.md#результат-на-тестовом-наборе).

Ten customers are not enough to get a feel for the panel. On the "Данные" (data) tab an
administrator can generate an extended dataset with the simulator and turn on a real-time
change stream. The same through the API:

```bash
# sign in: the session cookie goes to a file; Origin is required by the gateway CSRF guard
curl -c cr.txt -H 'Content-Type: application/json' -H 'Origin: http://localhost:8080' \
  -d '{"username":"admin","password":"admin"}' http://localhost:8080/api/v1/auth/login

# generate 2000 customers in place of the current data and turn on the change stream
curl -b cr.txt -X PUT -H 'Content-Type: application/json' -H 'Origin: http://localhost:8080' \
  -d '{"generate":{"accounts":2000,"seed":42}}' http://localhost:8080/api/v1/simulator
curl -b cr.txt -X PUT -H 'Content-Type: application/json' -H 'Origin: http://localhost:8080' \
  -d '{"running":true}' http://localhost:8080/api/v1/simulator

# turn the stream off and bring back the case data
curl -b cr.txt -X PUT -H 'Content-Type: application/json' -H 'Origin: http://localhost:8080' \
  -d '{"running":false}' http://localhost:8080/api/v1/simulator
curl -b cr.txt -X PUT -H 'Content-Type: application/json' -H 'Origin: http://localhost:8080' \
  --data-binary @data/sample/data.json http://localhost:8080/api/v1/source/snapshot
```

A snapshot without a `reference_time` field is scored at the current moment, so the numbers
after such an upload differ a little from the October 19 calculation.

The screenshots below were taken on the 2000-customer set (seed 42) with the stream on.

**Customer ranking.** Sorting by ID, email, balance, total top-ups, risk and expected loss,
filter by risk level, search by email or ID.

![Customer table](docs/img/screens/customers.png)

**Customer card.** Score and loyalty, expected loss in euros, reasons in plain words with each
reason's contribution to the score, and the score breakdown by feature.

![High-risk customer card](docs/img/screens/customer.png)

Further down the same card: money (monthly revenue, expected loss, worst case, how many days
the balance will last) and concrete actions for the marketer.

![Money and recommendations](docs/img/screens/customer-money.png)

**Model.** Feature weights, level thresholds and the forecast horizon. A change is saved right
away, creates a new settings version with change history, and scores are recalculated within
seconds. Only the `admin` role can open this page.

![Weight editor](docs/img/screens/model.png)

**Grafana.** The "Отток" dashboard is built on the business metrics from `/metrics`;
"Платформа" shows service health, RPS and latency through the gateway, SSE clients and scoring
replicas.

![Grafana: churn dashboard](docs/img/screens/grafana.png)

<details>
<summary>Login screen and the platform dashboard</summary>

![Login](docs/img/screens/login.png)

![Grafana: platform dashboard](docs/img/screens/grafana-platform.png)

</details>

## How it works

```mermaid
flowchart TB
    browser(["Browser"])

    subgraph gw["Traefik :8080"]
        traefik["routing, ForwardAuth, CSRF by Origin"]
    end

    subgraph platform["Platform"]
        auth["auth<br/>FastAPI: login, sessions, OIDC"]
        web["web<br/>React + nginx"]
        notifier["notifier x2<br/>Go, SSE"]
    end

    subgraph core["Core"]
        api["api x2<br/>FastAPI: ranking, analytics, settings, /metrics"]
        ingest["ingest<br/>snapshots and change stream"]
        scoring["scoring x2<br/>riskcore: score, reasons, money"]
        simulator["simulator<br/>extended data and live stream"]
    end

    subgraph data["Storage"]
        pg[("PostgreSQL 16<br/>source, scoring, auth")]
        redis[("Redis 7<br/>sessions, rate limits")]
        nats[["NATS JetStream<br/>stream CHURN"]]
    end

    subgraph obs["Observability"]
        prom["Prometheus"]
        graf["Grafana"]
    end

    keycloak["Keycloak<br/>oidc profile"]

    browser --> traefik
    traefik -. "session check" .-> auth
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

Every request goes through Traefik. Before each route except login and the login page assets,
the gateway asks `auth` (ForwardAuth): the session lives in an httpOnly cookie, and Redis stores
only the SHA-256 of the token. `auth` returns the user and their roles in headers, and core
services trust only those. Password login always works; Keycloak login (Authorization Code +
PKCE) is enabled by the `oidc` profile.

How data gets from the source to the screen:

1. A snapshot (`PUT /api/v1/source/snapshot`, the `data.json` format from the case) or a batch
   of changes (`POST /api/v1/source/events`) arrives at `ingest`. It writes the data to
   PostgreSQL (a snapshot is replaced atomically), keeps the status history and, in the same
   transaction, puts `churn.source.changed` into an outbox table. A background relay picks it
   up right away and sends it to JetStream.
2. `scoring` reads the event from JetStream through a shared durable consumer, so replicas split
   the stream between them. Processing is idempotent by `event_id`, and a full recalculation
   runs under `pg_advisory_lock` so two replicas never do the same work at once. After a weight
   change `api` publishes `churn.settings.changed` the same way, through its outbox, and
   everything is recalculated.
3. The result (score, reasons, money, recommendations) goes to the `scoring` schema, and
   `churn.scores.updated` goes to the bus.
4. `notifier` pushes the event to browsers over SSE (`/api/v1/stream`), and `api` uses the same
   event to drop its summary cache. The panel refetches what it needs and updates without a
   page reload.

`notifier` replicas know nothing about each other: each one listens to all events and keeps a
ring buffer of 200 for `Last-Event-ID`. If a replica dies, the browser reconnects to another one
after 3 seconds. Besides events, `scoring` rescores everyone once a minute when data is live:
the time left until services expire keeps shrinking even if nothing in the data changed.

An event is not lost even if NATS is down at the moment of writing. Before, `ingest` saved the
data first and published the event afterwards, so with the bus unavailable the recalculation
simply never happened. Now the event goes to `source.outbox` (`scoring.outbox` for `api`) in
the same transaction as the data: either both are saved or neither is. The relay sends it as
soon as the bus is back, with `Nats-Msg-Id` = `event_id`, so a resend after a crash does not
create a duplicate in JetStream. Details (in Russian) are in [docs/architecture.md](docs/architecture.md).

Each service, the threat model and failure handling are described in
[docs/architecture.md](docs/architecture.md). Event schemas: [contracts/events/](contracts/events/).

## Risk model

The model lives in the [`libs/riskcore`](libs/riskcore) library: plain Python with no
dependencies, no database and no network. `scoring` only reads data, calls the library and
stores the result, so the model is tested separately from the rest of the system.

For each active service (statuses `active`, `suspended`, `prolong_wait`) it estimates the
probability that the service will not be renewed within a 30-day horizon. Each feature returns
a signal strength from 0 to 1 and a sentence with concrete numbers, and each has a weight from
0 to 100:

| Feature | Weight | Level | When it fires |
|---|---|---|---|
| `prolong_wait` | 85 | service | the service is waiting for payment and will be deleted in 5 days |
| `autoprolong_off_expiring` | 60 | service | auto-renewal is off and the term ends within the horizon |
| `insufficient_funds` | 55 | service | auto-renewal is on, but there is not enough money for renewals |
| `churn_history` | 40 | customer | the customer has already lost some services |
| `suspended` | 35 | service | the customer switched the server off |
| `failed_payments` | 30 | service | renewal payments fail |
| `inactivity` | 20 | customer | no payments for longer than the billing period allows |
| `short_commitment` | 15 | service | short billing period: hour, week, month |
| `bonus_dependency` | 15 | customer | more bonus money than own money |
| `withdraw_pressure` | 10 | customer | part of the balance can be withdrawn |

Reasons for a service are combined with noisy-OR:

```
p_s = 1 − Π (1 − w_i · x_i)
```

A weight reads literally: 85 for "waiting for payment" means this reason alone, at full
strength, gives an 85% chance of losing the service. The result always stays between 0 and 1,
raising any weight can never lower the risk, and a new feature adds one more factor without
changing what the old weights mean. We deliberately did not train a model: the case data has
neither churn labels nor status history.

A customer's score is the expected share of their revenue we lose over the horizon:
`risk_score = 100 · Σ p_s · rev_s / Σ rev_s`. Loyalty is `100 − risk_score`. Levels: 65 and
above is high risk, 35 and above is medium, below that is low. If a customer has no active paid
services, the level is `unknown`. Thresholds, weights and the horizon (1 to 365 days) are
editable in the panel.

Money. The key number on the card is `expected_loss = Σ p_s · rev_s`: how much revenue we lose
on average over the horizon if we do nothing. A service's revenue is capped by what the customer
can actually pay; otherwise an hourly server from the sample would "bring in" thousands of euros
a month. Next to it the card shows `exposure` (the worst case, if every medium and high risk
service leaves) and how many days the balance will last.

Reason contributions are computed from the logarithms of the noisy-OR factors and add up to
exactly the customer's score, with no "other" bucket. Each reason comes with an action for the
marketer using numbers from the data, for example "Срочный звонок: service-0020 удалят через
3 дн." (urgent call: service-0020 will be deleted in 3 days). The main action `next_action` is
chosen by how much money it saves if the reason is removed.

Full description, the reasoning behind the weights and the sample breakdown:
[docs/model.md](docs/model.md).

## Quality check

The case data has no churn labels, so we tested the model on a synthetic future: generated
20,000 customers, scored them at moment T and played 30 days forward with two independent
simulations (the simulator's live stream and a renewal calendar).

![Share of churned customers by risk level](docs/img/calibration/levels.png)

- High-risk customers churn completely 3.3 times (stream) and 5.5 times (calendar) more often
  than the base average.
- Model ROC AUC is 0.765 and 0.932. That beats every simple rule such as "auto-renewal is off"
  (0.710 and 0.831).
- Ranking is solid, while the absolute `expected_loss` amounts cannot be verified on synthetic
  data: two reasonable simulation settings differ by a factor of 2.5. Use the amount to compare
  customers with each other, not as a revenue forecast.

We wrote the synthetic world ourselves, knowing the model's features, so the check confirms
that the model reads these mechanisms correctly but does not prove that real customers leave
for the same reasons. All limitations and the command to reproduce it
(`python -m tools.calibration`): [docs/calibration.md](docs/calibration.md).

## Adding a new feature

A feature is one file in [`libs/riskcore/riskcore/features/`](libs/riskcore/riskcore/features/).
No changes to the database schema, API, frontend or settings: the package discovers modules in
the folder by itself, and the `@register` decorator puts the feature into the registry.

```python
@register
class NewCustomer(AccountFeature):
    code = "new_customer"
    title = "Новый клиент"
    description = "Клиент с нами меньше 30 дней. Сила убывает к 30-му дню."
    group = "behavior"
    default_weight = 20.0

    def compute(self, ctx: AccountContext) -> Signal | None:
        ...  # strength 0..1 and an explanation with numbers, None if it did not fire

    def recommend(self, signal: Signal) -> Recommendation:
        ...  # what the marketer should do
```

After a rebuild (`docker compose up -d --build --wait`) the feature shows up in the model
settings with its default weight and takes part in scoring, the score breakdown, reasons and
recommendations. Shared tests check by themselves that its contributions add up to the score
and that raising its weight never lowers the risk. Full example with a test:
[docs/adding-feature.md](docs/adding-feature.md).

## Case requirements

| Requirement | Where it is done |
|---|---|
| Web panel with customer ranking, sorting and filters | `web/`, "Клиенты" (customers) page |
| Risk reason in plain words | `reasons[]` on the customer card; the text comes from the feature itself (`libs/riskcore/riskcore/features/`) |
| Money estimate | `expected_loss`, `exposure`, monthly revenue, summary and KPIs on the overview ([docs/model.md](docs/model.md#деньги)) |
| Editable weights that persist | "Модель" (model) page, `PATCH /api/v1/model/settings`; versions and history in PostgreSQL, survive a restart |
| Auto-refresh without reload | NATS JetStream → `scoring` → `scores.updated` → `notifier` (SSE) → panel |
| `/metrics` endpoint | `api`, Prometheus format, token-protected; Grafana dashboards in `monitoring/` |
| Authentication | `services/auth`: password (argon2id), `admin` and `analyst` roles, OIDC via Keycloak |
| Compose deployment | [`docker-compose.yml`](docker-compose.yml), one command, no `.env` |
| API documentation | Swagger UI at `/api/v1/docs`, contract [`contracts/openapi.yaml`](contracts/openapi.yaml) |
| Model and architecture description | [docs/model.md](docs/model.md), [docs/architecture.md](docs/architecture.md) |
| How to add a feature | [docs/adding-feature.md](docs/adding-feature.md) |

## Metrics and monitoring

`/metrics` returns aggregate figures for the whole customer base. Without the token the gateway
answers `401`.

```bash
curl -H "Authorization: Bearer churn-metrics-demo-token" http://localhost:8080/metrics
```

| Metric | Meaning |
|---|---|
| `churn_customers_by_risk_level{level}` | customers per level `high`, `medium`, `low`, `unknown` |
| `churn_customers_total` | total customers |
| `churn_expected_loss_eur` | expected loss over the horizon, euros |
| `churn_exposure_eur` | horizon revenue of medium and high risk services |
| `churn_revenue_at_risk_share`, `churn_revenue_at_risk_ratio` | share of horizon revenue we expect to lose (the same number, the second name follows the Prometheus convention) |
| `churn_risk_score_avg`, `churn_risk_score_max` | average and highest risk score of scored customers |
| `churn_risk_score_bucket{le}`, `churn_risk_score_customers` | risk score distribution: customers with a score up to `le` (10, 20, …, 100, `+Inf`) and how many customers are scored |
| `churn_monthly_revenue_eur` | monthly revenue from active services |
| `churn_services_at_risk`, `churn_services_total` | services at risk and all active services |
| `churn_model_version` | model settings version used in the last calculation |
| `churn_last_scored_timestamp_seconds` | time of the last recalculation |

All business metrics come from the same summary as `GET /api/v1/analytics/summary`, so they
match the panel: money matches `expected_loss` and `exposure`, risk score matches `risk_scores`.
The score distribution is a snapshot of the last recalculation, so it is a gauge with an `le`
label rather than a histogram: a bucket can shrink after a recalculation, while a Prometheus
histogram is made of counters that only grow. `histogram_quantile(0.5, churn_risk_score_bucket)`
still works and gives the median.

Every service also exposes its own metrics (`scoring_runs_total`, `scoring_duration_seconds`,
`notifier_connected_clients`, `auth_logins_total` and others), and Prometheus scrapes them
together with the PostgreSQL, Redis and NATS exporters. Alert rules are in
[`monitoring/prometheus/alerts.yml`](monitoring/prometheus/alerts.yml); Grafana dashboards are
provisioned automatically from [`monitoring/grafana/`](monitoring/grafana/).

## Reliability

We checked not only that everything works, but also what happens when something breaks. Every
scenario can be replayed on a running stack: `bash scripts/chaos.sh` (or `nats`, `api`,
`scoring` one at a time).

| What breaks | What happens | How it is checked |
|---|---|---|
| NATS is down while data is written | `ingest` and `api` put the event into an outbox in the same transaction as the data. The relay sends it once NATS is back, exactly once (`Nats-Msg-Id` = `event_id`) | `chaos.sh nats`, ingest/api/svckit integration tests |
| An `api` replica dies | Traefik routes to the other replica, the panel keeps answering | `chaos.sh api`: 20 of 20 requests in 10 s |
| A `scoring` replica dies mid-rescore | Unacked messages go to the other replica after `ack_wait` (60 s). Rescoring is atomic, a half-written one rolls back | `chaos.sh scoring` |
| `scoring` gets SIGTERM | Stops taking new batches, finishes the current one within 45 s, returns the rest with `nak` | scoring integration test |
| An event keeps failing the rescore | After 10 attempts with 1/5/30/120 s pauses it goes to `churn.dlq.scoring` and stops blocking others | scoring integration test, `scoring_dead_letters_total` |
| A database error in the worker | The worker logs it and retries. If a background task still dies, `/readyz` returns 503 and the pod restarts | scoring unit tests |
| Two `scoring` replicas rescore at once | `pg_advisory_xact_lock` with `lock_timeout`: one computes, the other waits or retries later | scoring integration test |
| The same event delivered twice | Idempotency by `event_id` (`processed_events` in the same transaction as results) | scoring integration test |
| `notifier` loses NATS | After reconnecting the panel receives `resync` and reloads everything | notifier Go tests, web test |
| `ingest` rejects a simulator batch | The simulator reloads its world from the database instead of drifting | simulator tests |
| A stuck Postgres query | Server-side `statement_timeout` and `idle_in_transaction_session_timeout` | svckit integration test |

Anything stuck shows up in Grafana on the «Outbox: в очереди» panel, and alerts fire:
`OutboxStuck`, `ScoringLagHigh`, `ScoringStale`, `ServiceDown`, `HighErrorRate`.

## Scaling and Kubernetes

Application services are stateless: everything lives in PostgreSQL, Redis and NATS. In Compose
`api`, `scoring` and `notifier` run two replicas each by default; the count is set with
`API_REPLICAS`, `SCORING_REPLICAS` and `NOTIFIER_REPLICAS`.

For Kubernetes there is a Helm chart, [`deploy/helm/churn-radar`](deploy/helm/churn-radar): a
Deployment with probes and a PodDisruptionBudget for every service, CPU-based HPA for `api` and
`scoring` (2 to 6 replicas), optional KEDA scaling on JetStream consumer lag, default-deny
NetworkPolicy, non-root containers with a read-only filesystem, and migrations as a
pre-install/pre-upgrade hook. `simulator` and `ingest` roll out with Recreate, so an update
never runs a second generator or a second seed. Redis and NATS know their memory limits
(`maxmemory`, JetStream limits in `nats.conf`, `GOMEMLIMIT`), and Postgres gets an in-memory
`/dev/shm`. Scheduled `pg_dump` is switched on with `backup.enabled` and keeps dumps on a
separate PVC. Values are checked by `values.schema.json`; Kubernetes 1.30 or newer is required.

Locally the chart runs in kind (you need kind, kubectl and helm):

```bash
make k8s-up      # three-node cluster, image build, chart install, panel on :8080
make k8s-demo    # kubectl scale, load and HPA, killing a pod under load
make k8s-down
```

CI checks the same thing on every PR: the `helm-kind` job builds the images, installs the chart
into kind, runs `helm test`, logs in through the gateway, waits until the summary has customers
and triggers a backup by hand. Locally, after `make k8s-up`, run `bash deploy/kind/smoke.sh`.

k6 load scenarios run in a container inside the Compose network and go through the gateway
with real authentication: customer ranking up to 200 RPS, a change stream into `ingest`, and
500 concurrent SSE connections. Thresholds and parameters are in
[tests/load/README.md](tests/load/README.md).

```bash
bash tests/load/run.sh customers     # or ingest, sse, all
```

More on deployment: [docs/deploy.md](docs/deploy.md), [deploy/k8s/README.md](deploy/k8s/README.md).

## API

Swagger UI is at http://localhost:8080/api/v1/docs once you are signed in to the panel. It
serves the same spec as [`contracts/openapi.yaml`](contracts/openapi.yaml), and the frontend
types are generated from it. Main resources:

| Method and path | What it does |
|---|---|
| `POST /api/v1/auth/login` | password login, sets the `cr_session` cookie |
| `GET /api/v1/customers` | ranking with sorting, level filter, search and pagination |
| `GET /api/v1/customers/{id}` | card: reasons, score breakdown, money, recommendations, services |
| `GET /api/v1/analytics/summary` | base summary, actual and forecast chart, top reasons |
| `GET/PATCH /api/v1/model/settings` | weights, thresholds and horizon (PATCH changes only the fields you send); next to it `POST …/reset` and `GET …/history` |
| `PUT /api/v1/source/snapshot` | upload a snapshot in the `data.json` format, gzip allowed |
| `POST /api/v1/source/events` | a batch of real-time changes |
| `GET /api/v1/stream` | live updates (SSE) |
| `GET/PUT /api/v1/simulator` | extended dataset generation and the demo stream |

State-changing requests with a cookie session must carry an `Origin` header from the same site.
Bus events are described with JSON Schema in [`contracts/events/`](contracts/events/); the "who
publishes, who listens" table is in [contracts/README.md](contracts/README.md).

## Development and tests

```text
.
├── services/
│   ├── auth/         login, sessions, OIDC, ForwardAuth (Python, FastAPI)
│   ├── api/          ranking, analytics, model settings, /metrics, Swagger
│   ├── ingest/       snapshot and change stream intake
│   ├── scoring/      event-driven risk recalculation
│   ├── simulator/    extended data generator and live stream
│   ├── notifier/     SSE fan-out of events (Go)
│   └── migrate/      core SQL migrations
├── libs/
│   ├── riskcore/     risk model: features, noisy-OR, money, recommendations
│   └── svckit/       shared service code: errors, auth, bus, DB, metrics
├── web/              marketer panel (React 19, Vite, TanStack Query)
├── contracts/        OpenAPI and event schemas
├── data/sample/      case sample data and its generator
├── db/core/          core database schema
├── deploy/           Traefik, Keycloak, Helm chart, kind
├── monitoring/       Prometheus (scraping, alerts) and Grafana (dashboards)
├── tests/            e2e (Playwright), load (k6)
├── tools/calibration/  model quality check on a synthetic future
├── scripts/          test runners, acceptance on a clean clone
└── docs/             architecture, model, calibration, deployment
```

Unit tests of the Python packages (riskcore, svckit, migrate, ingest, api, scoring, simulator),
one pytest run per package. You need Python 3.12 with the packages' `requirements-dev.txt`
installed:

```bash
bash scripts/test-core.sh                  # unit tests
bash scripts/test-core.sh --integration    # plus integration tests, Postgres and NATS start in docker
bash scripts/test-core.sh riskcore         # a single package
```

Everything else through `make`:

```bash
make test         # auth (in docker), notifier (build with go test), web (vitest)
make e2e          # Playwright acceptance scenario against the running stack
make acceptance   # clean clone → docker compose up → e2e → down -v
make helm-lint
```

The e2e scenario walks through what the jury will check: nothing opens without login, login
leads to an overview with numbers, sorting works, a high-risk customer has reasons and money, a
weight change updates scores without a reload and survives a restart, a new snapshot refreshes
the table by itself, `/metrics` matches the panel, Swagger opens, and an analyst cannot change
the model. The scenario changes the weights and data of the stack, so do not run it against a
demo someone is looking at.

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs on every push and PR: ruff,
eslint and tsc, hadolint, actionlint and promtool, tests of all services, image builds with a
Trivy scan, `helm lint` with kubeconform, e2e in Compose, and a chart install into kind (on PRs
and `main`). A `v*` tag publishes the images to GHCR.

## Configuration

All variables are optional; the sample with demo values is [`.env.example`](.env.example). To
change something, copy it to `.env`.

| Variable | Default | Purpose |
|---|---|---|
| `HTTP_PORT`, `PUBLIC_URL` | `8080`, `http://localhost:8080` | gateway port and external address for redirects and CSRF |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / `admin` | administrator |
| `ANALYST_USERNAME` / `ANALYST_PASSWORD` | `analyst` / `analyst` | analyst, read only |
| `METRICS_TOKEN` | `churn-metrics-demo-token` | Bearer token for `/metrics` and Prometheus |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | `churn` | database |
| `COOKIE_SECURE` | `false` | turn on behind HTTPS |
| `API_REPLICAS`, `SCORING_REPLICAS`, `NOTIFIER_REPLICAS` | `2` | replica counts |
| `SEED_REFERENCE_TIME` | `2026-10-19T15:00:00Z` | the moment the case sample is scored at on first start |
| `GRAFANA_PORT`, `PROMETHEUS_PORT` | `3000`, `9090` | observability ports |
| `OIDC_*`, `KEYCLOAK_*` | see `.env.example` | Keycloak login (`oidc` profile) |

Default passwords and tokens are for the demo only. Set your own for any other deployment.

## Security

- Every route except login is behind ForwardAuth. The gateway strips incoming `X-User-*`
  headers and sets them only from the `auth` response, so a user cannot be spoofed from outside.
- Passwords are hashed with argon2id, the session is an httpOnly cookie, and Redis keeps only a
  hash of the token. Password guessing is limited to 5 failed attempts per minute per IP and
  login pair.
- CSRF: the cookie is `SameSite=Lax`, and for state-changing requests the gateway requires an
  `Origin` from the same site.
- Roles: `admin` changes the model, uploads data and controls the simulator; `analyst` can only
  view. Permissions are checked both at the gateway and in the service.
- Application containers run as non-root, with a read-only root filesystem, `cap_drop: ALL`,
  `no-new-privileges` and CPU and memory limits.
- `/metrics` is protected by its own token; Grafana and Prometheus listen on localhost only.
- CSP `default-src 'self'` and `frame-ancestors 'none'` on the panel.

Full threat model: [docs/architecture.md](docs/architecture.md#модель-угроз).

## Team

- Dmitry ([@dimon0804](https://github.com/dimon0804))
- Nikita ([@Fr1bilder](https://github.com/Fr1bilder))
