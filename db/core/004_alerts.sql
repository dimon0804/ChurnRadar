-- Схема alerts: сервис оповещений (services/alerts).
-- Он слушает churn.scores.updated, читает scoring.* только на чтение и пишет только сюда.
-- Всё через IF NOT EXISTS: тесты других пакетов пересоздают source/scoring и прогоняют
-- все файлы db/core заново, схема alerts при этом остаётся.

CREATE SCHEMA IF NOT EXISTS alerts;

-- Что сервис помнит о клиенте с прошлого пересчёта.
-- loss_ref — точка отсчёта для правила «ожидаемая потеря выросла»: минимум с момента
-- последнего оповещения, поэтому медленный рост тоже рано или поздно сработает.
CREATE TABLE IF NOT EXISTS alerts.customer_state (
    customer_id    bigint PRIMARY KEY,
    risk_level     text NOT NULL,
    expected_loss  bigint NOT NULL,
    loss_ref       bigint NOT NULL,
    last_alert_at  timestamptz(3),
    updated_at     timestamptz(3) NOT NULL DEFAULT now()
);

-- Одна строка: когда снят исходный срез и что с общей ожидаемой потерей.
CREATE TABLE IF NOT EXISTS alerts.meta (
    id                  smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    baseline_at         timestamptz(3),
    total_loss          bigint,
    total_loss_ref      bigint,
    total_last_alert_at timestamptz(3),
    updated_at          timestamptz(3) NOT NULL DEFAULT now()
);
INSERT INTO alerts.meta (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- Лента оповещений: то, что видит панель (GET /api/v1/alerts).
CREATE TABLE IF NOT EXISTS alerts.feed (
    id             bigserial PRIMARY KEY,
    batch_id       uuid NOT NULL,                 -- одна пачка = одно событие scores.updated
    event_id       uuid NOT NULL,                 -- event_id исходного события
    kind           text NOT NULL CHECK (kind IN ('high_risk', 'loss_jump', 'total_loss_jump')),
    customer_id    bigint,                        -- NULL у total_loss_jump
    email          text,
    risk_level     text,
    prev_level     text,
    expected_loss  bigint,                        -- евроценты
    prev_loss      bigint,
    message        text NOT NULL,
    created_at     timestamptz(3) NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS feed_created_idx ON alerts.feed (created_at DESC);
CREATE INDEX IF NOT EXISTS feed_customer_idx ON alerts.feed (customer_id, created_at DESC);

-- Очередь доставки во внешние каналы (webhook, telegram). Пишется в той же транзакции,
-- что и лента, а отправляет её фоновый цикл: упавший канал не теряет оповещения.
CREATE TABLE IF NOT EXISTS alerts.deliveries (
    id               bigserial PRIMARY KEY,
    batch_id         uuid NOT NULL,
    channel          text NOT NULL CHECK (channel IN ('webhook', 'telegram')),
    payload          jsonb NOT NULL,
    status           text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    attempts         integer NOT NULL DEFAULT 0,
    next_attempt_at  timestamptz(3) NOT NULL DEFAULT now(),
    last_error       text,
    created_at       timestamptz(3) NOT NULL DEFAULT now(),
    sent_at          timestamptz(3),
    UNIQUE (batch_id, channel)
);
CREATE INDEX IF NOT EXISTS deliveries_pending_idx ON alerts.deliveries (next_attempt_at) WHERE status = 'pending';

-- Идемпотентность: повтор того же scores.updated не создаёт второе оповещение.
CREATE TABLE IF NOT EXISTS alerts.processed_events (
    event_id      uuid PRIMARY KEY,
    processed_at  timestamptz(3) NOT NULL DEFAULT now()
);
