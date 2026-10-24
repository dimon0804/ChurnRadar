-- Персональные уведомления: каждый пользователь панели сам выбирает, о каких критических
-- событиях, с какими порогами и куда (панель, почта, Telegram) ему сообщать.
-- Всё через IF NOT EXISTS / DROP IF EXISTS: файл можно прогнать повторно (так делают тесты).

-- Настройки пользователя. events: {"<тип>": {"enabled", "threshold", "period_h", "channels": [...]}}.
-- Чего в events нет — берётся по умолчанию из каталога событий сервиса.
CREATE TABLE IF NOT EXISTS alerts.user_prefs (
    username        text PRIMARY KEY,           -- X-User-Name из шлюза
    email           text,
    timezone        text NOT NULL DEFAULT 'Europe/Moscow',
    quiet_from      time,                       -- тихие часы: почта и Telegram ждут их конца
    quiet_to        time,
    digest_enabled  boolean NOT NULL DEFAULT false,
    digest_time     time NOT NULL DEFAULT '09:00',
    digest_channels text[] NOT NULL DEFAULT '{email}',
    last_digest_on  date,
    mute_until      timestamptz(3),             -- /mute в Telegram
    events          jsonb NOT NULL DEFAULT '{}',
    updated_at      timestamptz(3) NOT NULL DEFAULT now(),
    updated_by      text
);

-- Когда пользователю последний раз сообщали о событии этого типа (анти-спам и точка отсчёта роста).
-- active — событие-состояние ещё длится (данные устарели), повторно не шлём, пока не пройдёт.
CREATE TABLE IF NOT EXISTS alerts.user_event_state (
    username      text NOT NULL,
    event_type    text NOT NULL,
    last_sent_at  timestamptz(3),
    active        boolean NOT NULL DEFAULT false,
    PRIMARY KEY (username, event_type)
);

-- Личная лента пользователя (канал «в панели»): то, что показывает колокольчик.
CREATE TABLE IF NOT EXISTS alerts.notifications (
    id          bigserial PRIMARY KEY,
    username    text NOT NULL,
    event_type  text NOT NULL,
    title       text NOT NULL,
    message     text NOT NULL,
    link        text NOT NULL,
    created_at  timestamptz(3) NOT NULL DEFAULT now(),
    read_at     timestamptz(3)
);
CREATE INDEX IF NOT EXISTS notifications_user_idx ON alerts.notifications (username, id DESC);

-- Показатели базы после каждого пересчёта: от них считается рост «за период».
CREATE TABLE IF NOT EXISTS alerts.metric_snapshots (
    id              bigserial PRIMARY KEY,
    at              timestamptz(3) NOT NULL DEFAULT now(),
    expected_loss   bigint NOT NULL,            -- евроценты
    high_count      integer NOT NULL,
    risk_share      double precision NOT NULL,  -- доля выручки под риском, 0..1
    customers       integer NOT NULL
);
CREATE INDEX IF NOT EXISTS metric_snapshots_at_idx ON alerts.metric_snapshots (at);

-- Telegram: привязка аккаунта панели к чату, одноразовые коды для deep link и смещение getUpdates.
CREATE TABLE IF NOT EXISTS alerts.telegram_links (
    username     text PRIMARY KEY,
    chat_id      bigint NOT NULL UNIQUE,
    tg_username  text,
    linked_at    timestamptz(3) NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS alerts.telegram_codes (
    code        text PRIMARY KEY,
    username    text NOT NULL,
    expires_at  timestamptz(3) NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts.telegram_state (
    id             smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    update_offset  bigint NOT NULL DEFAULT 0
);
INSERT INTO alerts.telegram_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- Доставки теперь адресные: получатель (email, chat id) и пользователь, для которого письмо.
ALTER TABLE alerts.deliveries ADD COLUMN IF NOT EXISTS recipient text;
ALTER TABLE alerts.deliveries ADD COLUMN IF NOT EXISTS username text;
ALTER TABLE alerts.deliveries DROP CONSTRAINT IF EXISTS deliveries_channel_check;
ALTER TABLE alerts.deliveries ADD CONSTRAINT deliveries_channel_check
    CHECK (channel IN ('webhook', 'telegram', 'email'));
ALTER TABLE alerts.deliveries DROP CONSTRAINT IF EXISTS deliveries_batch_id_channel_key;
CREATE UNIQUE INDEX IF NOT EXISTS deliveries_batch_channel_recipient_uq
    ON alerts.deliveries (batch_id, channel, coalesce(recipient, ''));

-- Журнал (alerts.feed) теперь про критические события базы, а не про каждого клиента.
ALTER TABLE alerts.feed DROP CONSTRAINT IF EXISTS feed_kind_check;
ALTER TABLE alerts.feed ADD CONSTRAINT feed_kind_check CHECK (kind IN (
    'high_risk', 'loss_jump', 'total_loss_jump', 'bulk',
    'loss_growth', 'big_customer_high', 'risk_share', 'high_count_growth', 'stale_data'
));
