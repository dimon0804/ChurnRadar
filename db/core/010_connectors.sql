-- Коннекторы (pull): система сама ходит в источники по расписанию.
-- Исполнитель — ingest; при нескольких репликах работает одна (лидер по pg_try_advisory_lock),
-- а каждый запуск дополнительно держит свою advisory-блокировку на коннектор.
--
-- Токены в базе не храним. В token_env записано только имя переменной окружения ingest
-- (CONNECTOR_TOKEN_*), сам секрет лежит в .env / Kubernetes Secret. Дамп базы токенов
-- не раскрывает, ротация — заменой секрета без миграций и ключей шифрования.

CREATE TABLE source.connectors (
    id                    text PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9_-]{1,40}$'),
    kind                  text NOT NULL CHECK (kind IN ('billing_http', 'yandex_metrica')),
    title                 text NOT NULL,
    base_url              text NOT NULL,
    token_env             text CHECK (token_env IS NULL OR token_env ~ '^CONNECTOR_TOKEN_[A-Z0-9_]{1,64}$'),
    interval_s            integer NOT NULL DEFAULT 300 CHECK (interval_s BETWEEN 10 AND 604800),
    enabled               boolean NOT NULL DEFAULT false,
    managed               boolean NOT NULL DEFAULT false,   -- адрес/токен/счётчик заданы окружением ingest
    options               jsonb NOT NULL DEFAULT '{}'::jsonb,
    cursor                jsonb NOT NULL DEFAULT '{}'::jsonb, -- курсор инкремента (updatedAt по сущностям, последний день Метрики)
    next_run_at           timestamptz(3),
    run_requested_at      timestamptz(3),                   -- «Запустить сейчас»
    enabled_at            timestamptz(3),
    last_run_at           timestamptz(3),
    last_success_at       timestamptz(3),
    last_status           text CHECK (last_status IN ('ok', 'error', 'running')),
    last_error            text,
    last_duration_ms      integer,
    last_records          integer,
    consecutive_failures  integer NOT NULL DEFAULT 0,
    created_at            timestamptz(3) NOT NULL DEFAULT now(),
    updated_at            timestamptz(3) NOT NULL DEFAULT now(),
    updated_by            text
);

CREATE TABLE source.connector_runs (
    id            bigserial PRIMARY KEY,
    connector_id  text NOT NULL REFERENCES source.connectors (id) ON DELETE CASCADE,
    trigger       text NOT NULL CHECK (trigger IN ('schedule', 'manual')),
    status        text NOT NULL CHECK (status IN ('running', 'ok', 'error')),
    started_at    timestamptz(3) NOT NULL DEFAULT now(),
    finished_at   timestamptz(3),
    records       integer NOT NULL DEFAULT 0,
    attempts      integer NOT NULL DEFAULT 0,
    error         text,
    details       jsonb NOT NULL DEFAULT '{}'::jsonb,
    instance      text
);
CREATE INDEX connector_runs_recent_idx ON source.connector_runs (connector_id, started_at DESC);

-- Яндекс Метрика -------------------------------------------------------------------------
-- Сырые визиты не храним: из выгрузки Logs API сразу считаем дневные агрегаты по аккаунту.
-- Дневные строки живут 90 дней, из них ingest пересчитывает сводку web_activity, которую
-- читает модель. Внешних ключей на source.accounts нет намеренно: снимок делает TRUNCATE
-- аккаунтов, а визит может прийти раньше, чем аккаунт появится в биллинге.

CREATE TABLE source.web_visits_daily (
    account_id      bigint NOT NULL,
    day             date NOT NULL,                 -- день визита в часовом поясе счётчика
    visits          integer NOT NULL CHECK (visits >= 0),
    payment_visits  integer NOT NULL DEFAULT 0,
    cancel_visits   integer NOT NULL DEFAULT 0,
    pricing_visits  integer NOT NULL DEFAULT 0,
    last_visit_at   timestamptz(3) NOT NULL,
    last_cancel_at  timestamptz(3),
    PRIMARY KEY (account_id, day)
);
CREATE INDEX web_visits_daily_day_idx ON source.web_visits_daily (day);

CREATE TABLE source.web_activity (
    account_id            bigint PRIMARY KEY,
    last_visit_at         timestamptz(3),
    first_visit_at        timestamptz(3),
    visits_7d             integer NOT NULL DEFAULT 0,
    visits_30d            integer NOT NULL DEFAULT 0,
    payment_visits_30d    integer NOT NULL DEFAULT 0,
    cancel_visits_30d     integer NOT NULL DEFAULT 0,
    pricing_visits_30d    integer NOT NULL DEFAULT 0,
    last_cancel_visit_at  timestamptz(3),
    as_of                 timestamptz(3) NOT NULL,     -- момент, на который посчитаны окна 7/30 дней
    computed_at           timestamptz(3) NOT NULL DEFAULT now()
);

-- Запасная связка визитов с аккаунтами: clientID Метрики → аккаунт (POST /api/v1/source/metrica/client-ids).
CREATE TABLE source.metrica_client_ids (
    client_id   numeric(20, 0) PRIMARY KEY,           -- UInt64, в bigint не помещается
    account_id  bigint NOT NULL,
    updated_at  timestamptz(3) NOT NULL DEFAULT now(),
    updated_by  text
);
CREATE INDEX metrica_client_ids_account_idx ON source.metrica_client_ids (account_id);
