-- Схемы source (исходные данные) и scoring (результаты модели).
-- Схему auth ведёт auth-service отдельно (db/auth/).

CREATE SCHEMA IF NOT EXISTS source;
CREATE SCHEMA IF NOT EXISTS scoring;

-- Снимки и метаданные источника --------------------------------------------
CREATE TABLE source.snapshots (
    id              text PRIMARY KEY,
    kind            text NOT NULL CHECK (kind IN ('snapshot', 'changes', 'seed')),
    reference_time  timestamptz(3),
    accounts        integer NOT NULL DEFAULT 0,
    services        integer NOT NULL DEFAULT 0,
    transactions    integer NOT NULL DEFAULT 0,
    created_at      timestamptz(3) NOT NULL DEFAULT now(),
    created_by      text
);

-- Текущее «как бы сейчас» для расчёта: опорное время снимка или NULL (= now()).
CREATE TABLE source.clock (
    id              smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    reference_time  timestamptz(3),
    updated_at      timestamptz(3) NOT NULL DEFAULT now()
);
INSERT INTO source.clock (id) VALUES (1);

-- Данные в форме DTO кейса (имена полей в snake_case) ------------------------
CREATE TABLE source.accounts (
    id                bigint PRIMARY KEY,
    email             text NOT NULL,
    balance           bigint NOT NULL CHECK (balance >= 0),
    withdraw_balance  bigint NOT NULL CHECK (withdraw_balance >= 0),
    total_replenished bigint NOT NULL CHECK (total_replenished >= 0),
    bonus_balance     bigint NOT NULL CHECK (bonus_balance >= 0),
    bonus_state       text NOT NULL CHECK (bonus_state IN ('locked', 'unlocked')),
    updated_at        timestamptz(3) NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX accounts_email_uq ON source.accounts (email);

CREATE TABLE source.services (
    id            bigint PRIMARY KEY,
    name          text NOT NULL,
    owner_id      bigint NOT NULL REFERENCES source.accounts (id) ON DELETE CASCADE,
    payment_term  text NOT NULL CHECK (payment_term IN ('hour','week','month','quarter_year','half_year','year')),
    auto_prolong  boolean NOT NULL,
    created_at    timestamptz(3) NOT NULL,
    expires_at    timestamptz(3) NOT NULL,
    status        text NOT NULL CHECK (status IN ('active','suspended','prolong_wait','deleted','blocked')),
    price         bigint NOT NULL CHECK (price >= 0),
    updated_at    timestamptz(3) NOT NULL DEFAULT now()
);
CREATE INDEX services_owner_idx ON source.services (owner_id);
CREATE INDEX services_expires_idx ON source.services (expires_at);

CREATE TABLE source.transactions (
    id            bigint PRIMARY KEY,
    service_id    bigint NOT NULL REFERENCES source.services (id) ON DELETE CASCADE,
    amount        bigint NOT NULL CHECK (amount >= 0),
    status        text NOT NULL CHECK (status IN ('performed','not_performed')),
    type          text NOT NULL CHECK (type IN ('prolong','order')),
    created_at    timestamptz(3) NOT NULL,
    performed_at  timestamptz(3)
);
CREATE INDEX transactions_service_idx ON source.transactions (service_id);

-- История смен статусов услуг. В данных кейса её нет — копим сами при каждом
-- изменении; для начального снимка дату удаления оцениваем (estimated = true).
CREATE TABLE source.status_history (
    id          bigserial PRIMARY KEY,
    service_id  bigint NOT NULL,
    owner_id    bigint NOT NULL,
    old_status  text,
    new_status  text NOT NULL,
    changed_at  timestamptz(3) NOT NULL,
    estimated   boolean NOT NULL DEFAULT false
);
CREATE INDEX status_history_changed_idx ON source.status_history (changed_at);
CREATE INDEX status_history_service_idx ON source.status_history (service_id);

-- Настройки модели -------------------------------------------------------------
CREATE TABLE scoring.settings (
    id          smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    version     integer NOT NULL,
    data        jsonb NOT NULL,          -- {horizon_days, thresholds, weights:{code:{weight,enabled}}}
    updated_at  timestamptz(3) NOT NULL DEFAULT now(),
    updated_by  text
);

CREATE TABLE scoring.settings_history (
    version     integer PRIMARY KEY,
    data        jsonb NOT NULL,
    changes     jsonb NOT NULL,          -- [{path, old, new}]
    changed_at  timestamptz(3) NOT NULL DEFAULT now(),
    changed_by  text
);

-- Результаты расчёта ---------------------------------------------------------------
CREATE TABLE scoring.customer_scores (
    customer_id        bigint PRIMARY KEY,
    email              text NOT NULL,
    balance            bigint NOT NULL,
    bonus_balance      bigint NOT NULL,
    bonus_state        text NOT NULL,
    withdraw_balance   bigint NOT NULL,
    total_replenished  bigint NOT NULL,
    risk_score         double precision,          -- NULL при unknown
    risk_level         text NOT NULL CHECK (risk_level IN ('high','medium','low','unknown')),
    loyalty            double precision,
    reasons            jsonb NOT NULL,            -- [Reason] (топ-5)
    services_total     integer NOT NULL,
    services_at_risk   integer NOT NULL,
    monthly_revenue    bigint NOT NULL,
    expected_loss      bigint NOT NULL,
    exposure           bigint NOT NULL,
    detail             jsonb NOT NULL,            -- services, features, money, recommendations
    model_version      integer NOT NULL,
    scored_at          timestamptz(3) NOT NULL
);
CREATE INDEX customer_scores_score_idx ON scoring.customer_scores (risk_score DESC NULLS LAST);
CREATE INDEX customer_scores_level_idx ON scoring.customer_scores (risk_level);
CREATE INDEX customer_scores_email_idx ON scoring.customer_scores (email);
CREATE INDEX customer_scores_balance_idx ON scoring.customer_scores (balance);
CREATE INDEX customer_scores_repl_idx ON scoring.customer_scores (total_replenished);
CREATE INDEX customer_scores_loss_idx ON scoring.customer_scores (expected_loss);

CREATE TABLE scoring.summary (
    id             smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    data           jsonb NOT NULL,       -- Summary из openapi.yaml целиком
    model_version  integer NOT NULL,
    scored_at      timestamptz(3) NOT NULL
);

-- Идемпотентность обработчиков событий
CREATE TABLE scoring.processed_events (
    event_id      uuid PRIMARY KEY,
    processed_at  timestamptz(3) NOT NULL DEFAULT now()
);
