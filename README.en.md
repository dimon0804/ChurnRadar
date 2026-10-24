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
- [Data sources (connectors)](#data-sources-connectors)
- [Notifications](#notifications)
- [Users and SSO](#users-and-sso)
- [Risk model](#risk-model)
- [Quality check](#quality-check)
- [Marketing metrics](#marketing-metrics)
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

Sign-in through Authentik (SSO over OIDC and SAML) runs as a separate profile, see
[Users and SSO](#users-and-sso):

```bash
docker compose --profile sso up -d --build --wait      # Authentik on :9000
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

**Who to contact today.** A block on the overview: the five customers where the most money
can be saved. They are ordered by expected loss, not by score, so a high-risk customer worth €6
ends up below one worth €1,385. Each row has the ready action from the customer card and how
much it would save. The hosting company talks to customers by email, so the main button is
"Написать" (write): it opens an email from the template for the main risk reason, already
filled in with the customer's service, dates and amounts. Edit it if needed and send. On the
demo stand Mailpit catches every email, see http://localhost:8025.

What happens to the customer next:

- **Email sent**: the customer leaves the list for a week (`MAIL_FOLLOWUP_DAYS`) while we wait
  for an answer. No answer in that time brings them back marked "no reply, send a reminder".
- **Resolved**: the customer answered or the issue is closed. They come back only if their
  risk grows.
- **Snoozed**: until the chosen date.

If the expected loss or risk level grows after a mark, the customer comes back early. Replies
go to the employee's mailbox when the gateway passes it in `X-User-Email`; otherwise they go to
`SMTP_REPLY_TO` or `SMTP_FROM`.

"Написать всем" emails the whole queue, and in the customer table you can tick customers and
press "Написать выбранным". Before sending you see how many emails will go out, who is skipped
because they already got one in the last 7 days, and what the first email looks like. Emails
go out in the background, no faster than five per second (`MAIL_RATE_PER_S`), with progress
and a final "sent X, errors Y" right there. At the bottom of the block a "helped" counter shows
how many customers had lower risk or a higher balance after the email; the same numbers are in
`/metrics` (`churn_emails_*`). Analysts can mark and email customers too; only admins edit the
templates on the "Письма" (emails) page. "CSV" in the block downloads the whole queue.

**Customer ranking.** Sorting by ID, email, balance, total top-ups, risk and expected loss,
filter by risk level, search by email or ID. "Export CSV" saves the table with the same filters
and sorting: UTF-8 with BOM and `;` as the separator, so it opens straight away in Excel with
Russian locale.

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

To try a change before committing to it, turn on the "Что если" (what if) switch. In this mode
the sliders save nothing; the bar on top shows what the expected loss, the share of revenue at
risk and the number of customers per level would become, and a list shows who would move into
or out of high risk. "Применить" saves as usual, "Отменить" puts everything back. scoring
computes the preview with the same code as the real rescoring, so the numbers match after you
apply. Above 2000 customers it scores a sample, and the panel says plainly that the totals are
approximate (the threshold is `PREVIEW_SAMPLE_SIZE` on scoring). scoring keeps the snapshot and
the "before" side in memory until the next rescoring, so on 100 thousand customers the first
answer takes a few seconds and every further slider move about a second. If a full rescoring
of a large base is running at that moment, the preview may not fit into `PREVIEW_TIMEOUT_S`
(20 s); the panel then offers to retry.

![Weight editor](docs/img/screens/model.png)

**Model quality.** The answer to "how far can we trust the model": how many times more often
customers of each risk level churn, the ROC curve and a comparison with simple rules, precision
and recall of the "high" threshold, forecast versus outcome, runs with other seeds and an honest
list of limitations. The numbers come from the synthetic-future check (see
[Quality check](#quality-check)). Every signed-in user can open the page, and every chart has
a table view.

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
        auth["auth<br/>FastAPI: login, sessions, users, OIDC and SAML"]
        web["web<br/>React + nginx"]
        notifier["notifier x2<br/>Go, SSE"]
    end

    subgraph core["Core"]
        api["api x2<br/>FastAPI: ranking, analytics, settings, /metrics"]
        ingest["ingest<br/>snapshots and change stream"]
        scoring["scoring x2<br/>riskcore: score, reasons, money"]
        simulator["simulator<br/>extended data and live stream"]
        alerts["alerts x2<br/>notifications: panel, email, Telegram bot"]
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

    authentik["Authentik<br/>IdP, sso profile"]

    browser --> traefik
    traefik -. "session check" .-> auth
    traefik --> web
    traefik --> api
    traefik --> ingest
    traefik --> simulator
    traefik -- "/api/v1/stream" --> notifier
    auth --> redis
    auth -. "OIDC, SAML" .-> authentik
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
    traefik -- "/api/v1/alerts" --> alerts
    nats -- "scores.updated" --> alerts
    alerts --> pg
    alerts -. "webhook, Telegram" .-> outside(["external channels"])
    prom -. "/metrics" .-> api
    graf --> prom
```

Every request goes through Traefik. Before each route except login and the login page assets,
the gateway asks `auth` (ForwardAuth): the session lives in an httpOnly cookie, and Redis stores
only the SHA-256 of the token. `auth` returns the user and their roles in headers, and core
services trust only those. Password login always works; sign-in through Authentik over OIDC
(Authorization Code + PKCE) and SAML 2.0 is enabled by the `sso` profile.

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
5. `alerts` listens to the same `churn.scores.updated` with its own durable consumer and checks
   critical events against each user's thresholds: expected loss grew, a big customer moved into
   high risk, the share of revenue at risk crossed a line. Whoever cares gets a notification in
   the panel, by email or in Telegram (see [Notifications](#notifications)).

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

## Data sources (connectors)

Data does not only arrive in ChurnRadar on its own (snapshot and change stream): the system also
fetches it on a schedule. Connectors in `ingest` do this. Their list, status, run history and the
"Run now" button live on the "Data" → "Sources" page (admins only).

- **Billing HTTP API.** ingest calls `GET {base}/accounts|services|transactions?updated_since=…&cursor=…`
  and takes only what changed since the last run. The contract your billing implements is
  [`contracts/billing-source.openapi.yaml`](contracts/billing-source.openapi.yaml).
- **Yandex Metrica.** Visits are exported through the Logs API and turned into per-customer
  aggregates: last visit, visits over 7 and 30 days, visits to payment, deletion and pricing pages.
  Raw visits are not stored; daily aggregates are kept for 90 days. The model takes three
  features from them: "Viewed the deletion page", "Has not visited the panel for a while",
  "Visits the site less often".

Whatever a connector fetches goes the same way as push data: rows in `source.*` and an outbox
event in one transaction, then the usual rescore. The scheduler runs in every ingest replica, but
only one executes: the leader holds `pg_try_advisory_lock`, and each run also holds its own lock
per connector, so a connector never runs twice at once, even during leader handover. The cursor
(last `updatedAt` per entity, or the last Metrica day) is stored in the database. Network errors,
timeouts, 429 and 5xx are retried with a growing pause; after a failed run the pause before the
next one doubles, capped by the interval. Metrics: `connector_runs_total{source,result}`,
`connector_records_total`, `connector_last_success_timestamp`, alert `ConnectorStale`.

**Tokens are not stored in the database.** A connector keeps only the name of an ingest
environment variable (`CONNECTOR_TOKEN_*`); the token itself lives in `.env` or a Kubernetes
Secret. We chose a reference over encryption with a key from env: with encryption the key still
lives in the environment next to the database, plus key rotation and migrations. With a reference
a database dump reveals nothing and rotating a token means replacing a secret. The name must start
with `CONNECTOR_TOKEN_`, so a connector cannot be used to send, say, `DATABASE_URL` elsewhere.
`CONNECTOR_ALLOWED_HOSTS` limits where connectors may go.

### Demo

In compose both connectors are created and switched off. Their source is the simulator:
`http://simulator:8000/demo/billing/v1` (its world with `updatedAt` stamps) and
`http://simulator:8000/demo/metrica` (a fake Logs API with realistic visits: customer 6 visited
the deletion page, customer 4 has not logged in for a month, customer 1 almost stopped visiting).

1. "Data" → "Live change stream": switch to "Only serves the API" (pull) and start the stream.
   The simulator stops sending changes on its own.
2. "Sources" → "Billing": enable it or press "Run now". The run history shows how many records
   were fetched, and scores are recalculated as usual.
3. "Yandex Metrica" → "Run now". Customer 6 gets the "Viewed the deletion page" reason, customer 4
   gets "Has not visited the panel for a while".

### Your own billing

Implement the three methods from [`contracts/billing-source.openapi.yaml`](contracts/billing-source.openapi.yaml)
and set in `.env`:

```bash
CONNECTOR_BILLING_URL=https://billing.example.com/churnradar/v1
CONNECTOR_TOKEN_BILLING=<token your billing expects in Authorization: Bearer>
```

After `docker compose up -d` the `billing` connector points to your address. Turn it on and pick a
schedule in "Sources".

### Production Yandex Metrica

1. After the user signs in to the control panel, pass the account id:

   ```js
   ym(12345678, 'setUserID', String(accountId));
   ym(12345678, 'params', {account_id: String(accountId)});
   ```

   `setUserID` links the UserID to the ClientID inside Metrica (reports, visitor parameters). The
   Logs API list of visit fields has no separate UserID field, so the account id is also sent as a
   visit parameter: it arrives in `ym:s:parsedParamsKey1`/`ym:s:parsedParamsKey2`. The parameter
   key is configurable (`account_param`, `account_id` by default).
2. If the site cannot pass the account id, upload a clientID → account table in "Sources"
   (CSV `client_id,account_id`) or via `POST /api/v1/source/metrica/client-ids`. The site gets the
   ClientID with `ym(12345678, 'getClientID', cb)`.
3. Get an OAuth token with the `metrika:read` scope and set in `.env`:

   ```bash
   CONNECTOR_METRICA_URL=https://api-metrika.yandex.net
   CONNECTOR_METRICA_COUNTER=12345678
   CONNECTOR_TOKEN_METRICA=<OAuth token>
   ```

No code changes: the same connector talks to the production Logs API (evaluate the period, create
a log request, wait for `processed`, download the parts, `clean`). Metrica does not serve the
current day, so the export runs up to yesterday, and the last three days are re-read because
visits keep being completed for up to three days. Payment, deletion and pricing page patterns and
goal ids are connector options (`pages`, `goals`). In Kubernetes tokens go to `secrets.connectors`,
addresses to `connectors.*`, and `networkPolicy.connectorsEgress` opens `api-metrika.yandex.net:443`.

## Notifications

Nobody watches a dashboard all day, so ChurnRadar speaks up on its own. But only about what
matters: a message for every customer who drifts into high risk stops being read within a week.
So the service reports critical events for the whole base, and every panel user sets their own
thresholds and channels on the «Уведомления» (Notifications) page: the bell in the header →
«Настроить», or the profile menu.

| Event | Default threshold |
|---|---|
| Expected loss across the base grew by more than X % over a period (1 h, 6 h, a day, 3 days, a week) | 10 % per day |
| A big customer moved into high risk: expected loss on them is above N € | €500 |
| Share of revenue at risk crossed Y % upwards | 40 % |
| High-risk customers grew by more than Z over a period | 10 per day |
| Data arrived but no recalculation for more than T minutes (off by default) | 15 min |
| Daily digest at a chosen time (off by default) | 09:00 |

For each event a user turns it on or off and picks a threshold and channels: in the panel (the
bell), email, Telegram. Quiet hours with a time zone hold email and Telegram until they end; the
panel shows everything right away. «Отправить тестовое уведомление» checks every channel and says
plainly what did not arrive and why. Every threshold has a preview of the resulting message. A new
user starts with four events on and only in the panel: no emails or messages until they decide.
An admin sees who set up what and can reset anyone's settings.

Anti-spam is per user: the same event comes at most once an hour (`ALERT_COOLDOWN_MIN`), and
growth is measured from the previous notification, not from the start of the period again.
However many big customers move into high risk in one recalculation (say, after a snapshot
load), it is one notification listing the most expensive ones. A customer flipping back and
forth shows up again no sooner than in 6 hours.

**Telegram bot.** Long polling, no public URL needed. Linking goes through a one-time link
`https://t.me/<bot>?start=<code>` from the settings (the code lives 10 minutes); unlinking is a
button in the settings or `/stop`. Commands: `/top` (five customers with the largest expected
loss), `/customer <id>` (card with reasons), `/summary` (state of the base), `/mute <hours>`.
The bot shows data only to linked chats. Notifications carry an «Открыть в панели» button when
the panel is reachable from outside (Telegram rejects localhost links, then the link goes into the
text). With two alerts replicas exactly one polls `getUpdates`, the one holding
`pg_try_advisory_lock`; if it dies, another one takes over within seconds, the offset is stored in
the database. Without `TELEGRAM_BOT_TOKEN` the bot is off and the settings page says so.

**Email.** HTML plus a plain-text version, in Russian, with a link to the right page and to the
notification settings, sent through the shared `svckit.mail`. On the demo stack Mailpit catches
every email: http://localhost:8025.

**Journal.** A shared event feed on the notifications page, built with default thresholds
regardless of personal settings. The webhook (`ALERTS_WEBHOOK_URL`, HMAC signature, `text` field
for Slack and Mattermost) and the team Telegram chat (`TELEGRAM_CHAT_ID`) still get it.

The service, [`services/alerts`](services/alerts), is one more consumer of the
`churn.scores.updated` event that scoring already publishes. Not a single line of `scoring`,
`api`, `ingest` or `notifier` changed for it. It has its own `alerts` schema (migrations
`db/core/004`–`006`), the `/api/v1/alerts/*` route in the gateway and a page in the panel.

How delivery works:

- The service only reads `scoring.*`. Who moved into high risk is one SQL query; base metrics
  after each recalculation go to `alerts.metric_snapshots`, and growth over a period is measured
  against them.
- An event is checked against every subscriber's thresholds. The personal feed, the email and
  message queue (`alerts.deliveries`) and the "event processed" mark are written in one
  transaction, so a repeated event creates no duplicates. A background loop sends with
  5/15/60/300/900 s retries; after 8 attempts a delivery is marked failed, 4xx answers (except 408
  and 429) are not retried.
- Replicas split events through a JetStream durable consumer, checks run under
  `pg_advisory_xact_lock`, deliveries are claimed with `FOR UPDATE SKIP LOCKED` and a 60 s lease.
  The daily digest goes out exactly once: the day is claimed with an atomic UPDATE.
- An event that keeps failing goes to `churn.dlq.alerts` after 10 attempts.
- The Telegram token never reaches the logs: httpx logs request URLs and the Bot API URL contains
  the token, so that logger is muted.

To turn the channels on in Compose, set the variables in `.env` and restart alerts:

```bash
TELEGRAM_BOT_TOKEN=123456:ABC...   # a bot from @BotFather
SMTP_HOST=smtp.example.com          # defaults to mailpit:1025, the demo inbox
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=... SMTP_PASSWORD=...
```

In Helm the bot token and the SMTP password go to `secrets.alerts` (a separate Secret) or to a
ready Secret via `secrets.alerts.existingSecret`; the rest goes to `alerts.env` (`SMTP_HOST` and
so on). Outbound HTTPS and SMTP (587, 465) are opened by a dedicated NetworkPolicy
(`networkPolicy.alertsEgress`).

## Users and SSO

### Panel users

Accounts live in PostgreSQL (`auth` schema). On first start `auth` seeds `admin` / `admin` and
`analyst` / `analyst` from the environment; after that the administrator manages users on the
"Пользователи" (Users) page:

- search by login, name and email;
- create with a password, with a temporary password (shown once, hand it over to the person) or
  "SSO only": the account waits for the first Authentik sign-in with the same login and gets the
  role the admin picked;
- change name, email and role, block and unblock, reset the password, delete;
- an audit log: who did what and when.

You cannot block, demote or delete yourself, nor the last active administrator. Blocking, a
role change, a password reset and deletion close all of the user's sessions in Redis right
away, no need to wait for the session to expire. Every user changes their own email and
password in the profile (menu in the top right). The email is returned by `/api/v1/auth/me` and
is used for notifications.

One caveat: the `admin` and `analyst` accounts from the environment are seeded on every start,
so a deleted seed account comes back. A blocked one stays blocked.

### Sign-in through Authentik (OIDC and SAML)

Authentik is the project's main IdP. It starts with the `sso` profile and configures itself
without any clicking in its admin UI: the blueprint
[`deploy/authentik/blueprints/churn-radar.yaml`](deploy/authentik/blueprints/churn-radar.yaml)
creates groups, demo users, OIDC and SAML providers with applications, and group-to-role
mappings.

```bash
docker compose --profile sso up -d --build --wait
```

The first Authentik start takes a minute or two while it migrates its database. Then
http://localhost:8080/login shows "Войти через Authentik (OIDC)" and "Войти через Authentik
(SAML)". The buttons appear only when the IdP is configured and answering right now: without the
`sso` profile the login page looks as usual.

| Authentik login | Password | Group | Panel role |
|---|---|---|---|
| `admin` | `admin` | `churn-admins` | administrator |
| `marketer` | `marketer` | `churn-analysts` | analyst |
| `outsider` | `outsider` | none | access denied |
| `akadmin` | `authentik` | — | Authentik admin UI at http://localhost:9000 |

The passwords are demo values from `.env.example`; change them, together with
`AUTHENTIK_SECRET_KEY`, for anything beyond a demo.

How it works:

- **OIDC**: Authorization Code + PKCE (S256), one-time `state`, `nonce` is checked. Endpoints
  come from discovery. The browser talks to the public Authentik address, while `auth` exchanges
  the code and fetches keys over the internal network. Authentik builds `iss` from the Host
  header, so internal requests carry the public Host and `iss` matches.
- **SAML 2.0**: SP-initiated sign-in (HTTP-Redirect), the assertion comes to the ACS via
  HTTP-POST. SP metadata: `GET /api/v1/auth/saml/metadata`. The signature is checked against the
  certificate from the IdP metadata (by URL, from a file or straight from a variable). After that
  `auth` reads only the fragment covered by the signature, requires exactly one assertion in the
  response and checks Issuer, Audience, Recipient, NotBefore/NotOnOrAfter and InResponseTo. The
  assertion ID is kept in Redis until it expires, so it cannot be accepted twice. Unsolicited
  (IdP-initiated) responses and encrypted assertions are not supported, and there is no SLO.
- **Roles**: from the `roles` claim (OIDC) or the `churn-radar-role` attribute (SAML) filled in by
  the blueprint, or from groups (`groups` and `http://schemas.xmlsoap.org/claims/Group`).
  `SSO_ADMIN_GROUPS` and `SSO_ANALYST_GROUPS` decide which group gives which role. No matching
  group means no access (`SSO_DEFAULT_ROLE` is empty).
- **Accounts**: an SSO user appears in the panel on first sign-in, and the role follows the
  groups on every sign-in. If an admin changed the role by hand, it wins over the groups. A user
  blocked in the panel cannot get in through SSO either, even if Authentik lets them in.
- After SSO the same session is created as after a password login: cookie, Redis, ForwardAuth.

The SAML signature is verified by [signxml](https://github.com/XML-Security/signxml) (lxml +
cryptography). python3-saml and pysaml2 pull in native xmlsec1, which needs exactly the same
libxml2 version as lxml; that breaks builds often and adds packages to the image. The SP logic
(the checks above) is our own and covered by tests: a valid assertion passes, while a foreign
key, a change after signing, a wrong Audience, expiry, replay and signature wrapping are rejected.

Keycloak, which used to live in the `oidc` profile, is gone: Authentik covers both OIDC and SAML,
and two IdPs in a demo is an extra gigabyte of memory. The OIDC client in `auth` works with any
provider via discovery, so Keycloak can be plugged in with variables (below).

### Your own IdP

Your own Authentik, Keycloak, Entra ID, Okta — anything with OIDC or SAML 2.0:

```bash
# OIDC
OIDC_PUBLIC_ISSUER=https://auth.company.com/application/o/churn-radar/
OIDC_INTERNAL_ISSUER=           # empty means the same address
OIDC_CLIENT_ID=churn-radar
OIDC_CLIENT_SECRET=...
OIDC_SCOPES="openid profile email"
# redirect URI in the IdP: <PUBLIC_URL>/api/v1/auth/oidc/callback

# SAML
SAML_IDP_METADATA_URL=https://auth.company.com/application/saml/churn-radar/metadata/
SAML_IDP_PUBLIC_URL=            # only if metadata is fetched over an internal address
# in the IdP: ACS = <PUBLIC_URL>/api/v1/auth/saml/acs, Audience/Entity ID = <PUBLIC_URL>/api/v1/auth/saml/metadata,
# sign the assertion (RSA-SHA256), ACS binding HTTP-POST

# IdP groups -> roles
SSO_ADMIN_GROUPS=churn-admins
SSO_ANALYST_GROUPS=churn-analysts
```

In Kubernetes the same goes into the `sso` block of the Helm chart values: OIDC issuer and
client, SAML metadata, the client secret via `sso.existingSecret`, egress to the IdP via
`sso.egress` (NetworkPolicy). The chart does not install Authentik itself: it is a separate
system that a company usually already runs. See [docs/deploy.md](docs/deploy.md#sso-в-kubernetes).

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
| `cancel_page_views` | 45 | customer | per Yandex Metrica, opened the service deletion or cancellation page within 30 days |
| `panel_absence` | 25 | customer | per Yandex Metrica, has not visited the control panel for over 14 days |
| `web_activity_drop` | 20 | customer | per Yandex Metrica, visits this week are under half of a usual week |

The last three features come from Yandex Metrica (see [Data sources](#data-sources-connectors)).
If a customer has no visits, they stay silent and add nothing to the score.

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
marketer using numbers from the data, for example "Срочно написать: service-0020 удалят через
3 дн." (email urgently: service-0020 will be deleted in 3 days). The main action `next_action` is
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

The same results are shown in the panel on the "Model quality" page (`/quality`). The file
`tools/calibration/results/results.json` is baked into the api image at build time and served
by `GET /api/v1/model/quality`. An api test compares the riskcore and simulator source
fingerprints stored in the file with the current code, so after a model change CI asks you to
rerun the check. If the weights in the settings differ from the tested ones, the page says so.

## Marketing metrics

The "Marketing" page (`/marketing`, open to everyone signed in) shows the numbers a marketer
expects, computed from the case data and the model forecast. Every card has an "i" icon with the
formula on the current numbers, and a plain-language glossary sits at the bottom of the page. The
customer card has a "Marketing" block: the customer's MRR, forecast and actual LTV, and time with us.

A month is always 30 days, money in the API is in euro cents. All formulas live in one place,
[`libs/riskcore/riskcore/marketing.py`](libs/riskcore/riskcore/marketing.py), and are covered by a
test that recomputes the 10-customer case base by hand.

| Metric | How it is computed |
|---|---|
| MRR, monthly recurring revenue | revenue of live services normalised to a month. We use the same collectible revenue as the model: an hourly service at list price would give hundreds of euros a month, which the customer never pays. Equals "monthly revenue" on the overview |
| ARPU | MRR ÷ active customers (at least one live service) |
| ARPPU | MRR ÷ paying customers (MRR above zero) |
| Customer churn (forecast) | mean `risk_score / 100` over paying customers: the score is the share of the customer's revenue the model expects to lose over the horizon. The horizon is converted to a month: `1 − (1 − p)^(30 / horizon)` |
| Gross Revenue Churn (forecast) | the same weighted by customer MRR: which share of MRR leaves in a month |
| Retention | `1 − churn` |
| Customer churn (actual) | customers who left ÷ customer-months observed. Left means all services deleted or blocked; the date comes from status history, otherwise end of the paid period + 5 days |
| Expected lifetime | `1 ÷ monthly churn`, capped at 60 months: near-zero churn would make it infinite |
| LTV (forecast) | `ARPU × margin ÷ monthly customer churn`. Per customer: their MRR × margin × their lifetime |
| LTV (actual) | sum of performed transactions: what the customer has already paid for services |
| LTV at risk | sum of LTV of high and medium risk customers |
| What retention gives | if we keep the high-risk customers, their churn returns to the historical actual churn (or to the low-risk churn if nobody has left). LTV uplift = MRR × (lifetime at that churn − lifetime now) |
| CAC, LTV/CAC, Payback | acquisition cost is not in the data, an administrator enters it. `LTV ÷ CAC` and `CAC ÷ (ARPU × margin)` months to pay back; without CAC the page honestly says "enter CAC" |
| Revenue concentration | share of MRR held by the top 10% of paying customers (at least one) |
| Average payment | performed payments ÷ their count (free zero-amount orders excluded) |
| Auto-renewal | share of active customers with a service on auto-renewal, and the MRR share of such services |

Margin (100% by default, so LTV is revenue, not profit) and CAC are stored in PostgreSQL
(`scoring.marketing_settings`) and only an administrator can change them. scoring computes the
base metrics on every recalculation together with the summary, and api applies margin and CAC
when serving, so changing them does not trigger a model recalculation. The same numbers go to
`/metrics` (`churn_mrr_eur`, `churn_arpu_eur`, `churn_ltv_eur`, `churn_customer_churn_rate`,
`churn_revenue_churn_rate`) and to the "Marketing" row of the Grafana "Отток" dashboard.

On the case data the model sees risk for almost every paying customer, so the forecast churn is
high (about 65% of customers a month) and LTV is small. The actual historical churn is about
7.5% a month, which is exactly what the "What retention gives" block shows.

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
| Authentication | `services/auth`: password (argon2id), `admin` and `analyst` roles, SSO through Authentik over OIDC and SAML 2.0, user management in the panel |
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
| `churn_mrr_eur`, `churn_arpu_eur`, `churn_ltv_eur` | MRR, ARPU and average customer LTV (with the configured margin), euro |
| `churn_customer_churn_rate`, `churn_revenue_churn_rate` | forecast customer and revenue churn over 30 days, share 0..1 |

All business metrics come from the same summary as `GET /api/v1/analytics/summary`, so they
match the panel: money matches `expected_loss` and `exposure`, risk score matches `risk_scores`.
The score distribution is a snapshot of the last recalculation, so it is a gauge with an `le`
label rather than a histogram: a bucket can shrink after a recalculation, while a Prometheus
histogram is made of counters that only grow. `histogram_quantile(0.5, churn_risk_score_bucket)`
still works and gives the median.

Every service also exposes its own metrics (`scoring_runs_total`, `scoring_duration_seconds`,
`notifier_connected_clients`, `auth_logins_total`, `alerts_sent_total{channel}`,
`alerts_failed_total{channel}` and others), and Prometheus scrapes them
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
| Email, Telegram or the webhook does not answer | The notification is already in the panel; the delivery waits in `alerts.deliveries` and is retried with backoff; metric `alerts_failed_total`, alert `AlertsDeliveryFailing` | alerts integration tests (webhook answers 503 then recovers; unreachable address) |
| `scores.updated` delivered to alerts twice | Idempotent by `event_id`: no second notification, email or journal record | alerts integration test |
| The alerts replica polling Telegram dies | The advisory lock is released and another replica resumes polling from the same offset | bot integration test (two replicas, leader change) |

Anything stuck shows up in Grafana on the «Outbox: в очереди» panel, and alerts fire:
`OutboxStuck`, `ScoringLagHigh`, `ScoringStale`, `ServiceDown`, `HighErrorRate`, `AlertsDeliveryFailing`.

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
| `GET /api/v1/customers/export.csv` | the same ranking as an Excel-friendly file; `worklist=true` exports only the contact queue |
| `GET /api/v1/contact-today` | who to contact today: at-risk customers by expected loss, highest first |
| `GET/PUT/DELETE /api/v1/customers/{id}/action` | the "email sent", "resolved" or "snoozed" mark and its log |
| `POST /api/v1/customers/{id}/email/draft`, `POST …/email` | an email from a template: draft and sending over SMTP |
| `POST /api/v1/mail/campaigns` (plus `…/preview`, `GET …/{id}`) | background mailing with repeat protection and progress |
| `GET/PUT /api/v1/mail/templates` | email templates, only admins can edit |
| `GET /api/v1/mail/stats` | how many emails were sent and how many customers they helped |
| `GET /api/v1/analytics/summary` | base summary, actual and forecast chart, top reasons |
| `GET /api/v1/analytics/marketing` | marketing metrics: MRR, ARPU, churn, LTV, risk segments, retention scenarios |
| `GET/PUT /api/v1/analytics/marketing/settings` | margin and customer acquisition cost (CAC); PUT is admin only |
| `GET/PATCH /api/v1/model/settings` | weights, thresholds and horizon (PATCH changes only the fields you send); next to it `POST …/reset` and `GET …/history` |
| `GET /api/v1/model/quality` | model check results on a synthetic future: lift, ROC, baselines, calibration, robustness |
| `POST /api/v1/model/settings/preview` | "what if": same body as PATCH, returns "before → after", saves nothing |
| `PUT /api/v1/source/snapshot` | upload a snapshot in the `data.json` format, gzip allowed |
| `POST /api/v1/source/events` | a batch of real-time changes |
| `GET /api/v1/stream` | live updates (SSE) |
| `GET/PUT /api/v1/simulator` | extended dataset generation and the demo stream |
| `GET/PUT /api/v1/alerts/preferences` | my notification settings; also `POST …/test`, `GET …/notifications`, `POST …/telegram/link`, and `…/preferences/users` for admins |
| `GET /api/v1/alerts` | journal of critical events (alerts service) |

State-changing requests with a cookie session must carry an `Origin` header from the same site.
Bus events are described with JSON Schema in [`contracts/events/`](contracts/events/); the "who
publishes, who listens" table is in [contracts/README.md](contracts/README.md).

## Development and tests

```text
.
├── services/
│   ├── auth/         login, sessions, users, OIDC and SAML, ForwardAuth (Python, FastAPI)
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
├── deploy/           Traefik, Authentik (blueprint), Helm chart, kind
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

`users.spec.ts` covers user management: an admin creates an analyst, the analyst signs in, after
blocking their session is closed and they can no longer sign in, and the account is deleted at
the end. Sign-in through Authentik is covered by `sso.spec.ts`. It needs the `sso` profile, so
without the variable it is skipped:

```bash
docker compose --profile sso up -d --build --wait
cd tests/e2e && SSO_E2E=1 npx playwright test specs/sso.spec.ts
```

The `auth` integration tests (migrations up and down, user management, SSO sign-in against real
Postgres and Redis) are enabled by `AUTH_TEST_DATABASE_URL` and `AUTH_TEST_REDIS_URL`; the
database must be empty and disposable. CI runs them in a separate job with postgres and redis
services.

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs on every push and PR: ruff,
eslint and tsc, hadolint, actionlint and promtool, tests of all services, image builds with a
Trivy scan, `helm lint` with kubeconform, e2e in Compose, e2e of the Authentik sign-in with the
`sso` profile, and a chart install into kind (the last two on PRs and `main`). A `v*` tag publishes the images to GHCR.

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
| `API_REPLICAS`, `SCORING_REPLICAS`, `NOTIFIER_REPLICAS`, `ALERTS_REPLICAS` | `2` | replica counts |
| `ALERT_COOLDOWN_MIN` | `60` | anti-spam: the same event reaches a user at most once per N minutes; thresholds are set by each user in the panel |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` | `mailpit`, `1025`, `none` | email for notifications; on the demo stack Mailpit catches it (http://localhost:8025) |
| `ALERTS_WEBHOOK_URL`, `ALERTS_WEBHOOK_SECRET` | empty | journal webhook and its signing key |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | empty | Telegram bot token (no token, no bot) and the team chat for the journal |
| `SEED_REFERENCE_TIME` | `2026-10-19T15:00:00Z` | the moment the case sample is scored at on first start |
| `GRAFANA_PORT`, `PROMETHEUS_PORT` | `3000`, `9090` | observability ports |
| `OIDC_*`, `SAML_*`, `SSO_*` | see `.env.example` | sign-in through an IdP over OIDC and SAML, groups for roles |
| `AUTHENTIK_*`, `SSO_DEMO_*` | see `.env.example` | Authentik in the `sso` profile and its demo users' passwords |

Default passwords and tokens are for the demo only. Set your own for any other deployment.

## Security

- Every route except login is behind ForwardAuth. The gateway strips incoming `X-User-*`
  headers and sets them only from the `auth` response, so a user cannot be spoofed from outside.
- Passwords are hashed with argon2id, the session is an httpOnly cookie, and Redis keeps only a
  hash of the token. Password guessing is limited to 5 failed attempts per minute per IP and
  login pair.
- CSRF: the cookie is `SameSite=Lax`, and for state-changing requests the gateway requires an
  `Origin` from the same site.
- Roles: `admin` changes the model, uploads data, controls the simulator and manages users;
  `analyst` can only view. Permissions are checked both at the gateway and in the service.
  Blocking, a role change or a password reset closes all of the user's sessions at once, and
  admin actions are written to an audit log.
- SSO: the SAML assertion signature is checked against the certificate from the IdP metadata,
  and only the signed fragment is read afterwards (protection against signature wrapping), plus
  Audience, validity window, InResponseTo and assertion replay. OIDC checks the `id_token`
  signature, `iss`, `aud`, `nonce` and PKCE.
- Application containers run as non-root, with a read-only root filesystem, `cap_drop: ALL`,
  `no-new-privileges` and CPU and memory limits.
- `/metrics` is protected by its own token; Grafana and Prometheus listen on localhost only.
- CSP `default-src 'self'` and `frame-ancestors 'none'` on the panel.

Full threat model: [docs/architecture.md](docs/architecture.md#модель-угроз).

## Team

- Dmitry ([@dimon0804](https://github.com/dimon0804))
- Nikita ([@Fr1bilder](https://github.com/Fr1bilder))
