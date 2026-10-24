-- Связь с клиентами по почте: шаблоны писем, отправленные письма, рассылки.
-- Пишет api (как scoring.customer_actions), scoring эти таблицы не трогает.

-- Статусы работы с клиентом под почту: «Сделано» становится «Решено», добавляется
-- «Письмо отправлено» — оно, как отложка, держит клиента вне очереди до snoozed_until.
ALTER TABLE scoring.customer_actions DROP CONSTRAINT IF EXISTS customer_actions_status_check;
ALTER TABLE scoring.customer_actions DROP CONSTRAINT IF EXISTS customer_actions_check;
UPDATE scoring.customer_actions SET status = 'resolved' WHERE status = 'done';
ALTER TABLE scoring.customer_actions
    ADD CONSTRAINT customer_actions_status_check CHECK (status IN ('emailed', 'resolved', 'snoozed')),
    ADD CONSTRAINT customer_actions_until_check CHECK ((status = 'resolved') = (snoozed_until IS NULL));

ALTER TABLE scoring.customer_actions_log DROP CONSTRAINT IF EXISTS customer_actions_log_status_check;
UPDATE scoring.customer_actions_log SET status = 'resolved' WHERE status = 'done';
ALTER TABLE scoring.customer_actions_log
    ADD CONSTRAINT customer_actions_log_status_check
        CHECK (status IN ('emailed', 'resolved', 'snoozed', 'cleared')),
    ADD COLUMN email_id bigint;

-- Шаблоны писем. Тексты по умолчанию живут в коде api (app/mail_templates.py) и
-- дописываются сюда при первом чтении; правка в панели перезаписывает строку.
CREATE TABLE scoring.mail_templates (
    code        text PRIMARY KEY,
    subject     text NOT NULL,
    body        text NOT NULL,
    updated_at  timestamptz(3) NOT NULL DEFAULT now(),
    updated_by  text
);

CREATE TABLE scoring.mail_campaigns (
    id             bigserial PRIMARY KEY,
    template_code  text,                     -- NULL: шаблон по главной причине каждого клиента
    skip_days      integer NOT NULL,
    created_at     timestamptz(3) NOT NULL DEFAULT now(),
    created_by     text,
    finished_at    timestamptz(3)
);

-- Письма клиентам. Это же очередь рассылки: строки queued забирает фоновый отправщик api
-- (FOR UPDATE SKIP LOCKED, так что реплик может быть несколько).
CREATE TABLE scoring.customer_emails (
    id                      bigserial PRIMARY KEY,
    campaign_id             bigint REFERENCES scoring.mail_campaigns (id) ON DELETE SET NULL,
    customer_id             bigint NOT NULL,
    to_email                text NOT NULL,
    reply_to                text,
    template_code           text,
    subject                 text NOT NULL,
    body                    text NOT NULL,
    status                  text NOT NULL CHECK (status IN ('queued', 'sending', 'sent', 'failed', 'skipped')),
    skip_reason             text,
    error                   text,
    attempts                integer NOT NULL DEFAULT 0,
    message_id              text,
    -- состояние клиента в момент постановки письма: по нему считаем, помогло ли
    risk_score_at_send      double precision,
    expected_loss_at_send   bigint,
    balance_at_send         bigint,
    created_at              timestamptz(3) NOT NULL DEFAULT now(),
    created_by              text,
    sent_at                 timestamptz(3)
);
CREATE INDEX customer_emails_queue_idx ON scoring.customer_emails (id) WHERE status = 'queued';
CREATE INDEX customer_emails_customer_idx ON scoring.customer_emails (customer_id, sent_at DESC);
CREATE INDEX customer_emails_campaign_idx ON scoring.customer_emails (campaign_id);
CREATE INDEX customer_emails_sent_idx ON scoring.customer_emails (sent_at) WHERE status = 'sent';
