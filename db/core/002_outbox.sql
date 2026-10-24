-- Transactional outbox: событие пишется в той же транзакции, что и данные,
-- а в NATS его отправляет фоновый relay (libs/svckit/svckit/outbox.py).
-- Если NATS лежит, событие ждёт в таблице и уходит, когда шина вернётся.
--
-- Таблица живёт в схеме того, кто пишет данные:
--   source.outbox  — ingest (churn.source.changed);
--   scoring.outbox — api, настройки модели в scoring.settings (churn.settings.changed).

CREATE TABLE source.outbox (
    id            bigserial PRIMARY KEY,
    event_id      uuid NOT NULL UNIQUE,          -- он же Nats-Msg-Id: JetStream отсекает повтор
    subject       text NOT NULL,
    type          text NOT NULL,
    payload       jsonb NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),   -- occurred_at в конверте
    published_at  timestamptz,
    attempts      integer NOT NULL DEFAULT 0,
    last_error    text
);
CREATE INDEX outbox_pending_idx ON source.outbox (id) WHERE published_at IS NULL;
CREATE INDEX outbox_published_idx ON source.outbox (published_at) WHERE published_at IS NOT NULL;

CREATE TABLE scoring.outbox (
    id            bigserial PRIMARY KEY,
    event_id      uuid NOT NULL UNIQUE,
    subject       text NOT NULL,
    type          text NOT NULL,
    payload       jsonb NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    published_at  timestamptz,
    attempts      integer NOT NULL DEFAULT 0,
    last_error    text
);
CREATE INDEX outbox_pending_idx ON scoring.outbox (id) WHERE published_at IS NULL;
CREATE INDEX outbox_published_idx ON scoring.outbox (published_at) WHERE published_at IS NOT NULL;
