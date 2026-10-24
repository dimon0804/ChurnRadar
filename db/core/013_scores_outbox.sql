-- outbox для churn.scores.updated: событие о пересчёте пишется в той же транзакции,
-- что и сами оценки, и не теряется, если NATS недоступен в момент коммита.
-- Отдельная таблица, а не scoring.outbox: ту отправляет relay сервиса api с producer=api.

CREATE TABLE IF NOT EXISTS scoring.scores_outbox (
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
CREATE INDEX IF NOT EXISTS scores_outbox_pending_idx ON scoring.scores_outbox (id) WHERE published_at IS NULL;
CREATE INDEX IF NOT EXISTS scores_outbox_published_idx ON scoring.scores_outbox (published_at) WHERE published_at IS NOT NULL;
