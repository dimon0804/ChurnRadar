-- Работа с клиентами из списка «Кому позвонить сегодня»: «Сделано» и «Отложить».
--
-- Пишет api (как scoring.settings и scoring.outbox), scoring эти таблицы не трогает.
-- Внешнего ключа на customer_scores нет: scoring перезаписывает оценки, а отметка
-- должна пережить пересчёт и даже временное исчезновение клиента из снимка.

-- Текущая отметка: одна на клиента.
CREATE TABLE scoring.customer_actions (
    customer_id            bigint PRIMARY KEY,
    status                 text NOT NULL CHECK (status IN ('done', 'snoozed')),
    snoozed_until          timestamptz(3),
    note                   text,
    -- что было у клиента в момент отметки: если после «Сделано» потеря или уровень
    -- выросли, клиент возвращается в список
    expected_loss_at_mark  bigint NOT NULL,
    risk_level_at_mark     text NOT NULL,
    updated_at             timestamptz(3) NOT NULL DEFAULT now(),
    updated_by             text,
    CHECK ((status = 'snoozed') = (snoozed_until IS NOT NULL))
);

-- Журнал: кто, когда и что отметил, включая снятие отметки.
CREATE TABLE scoring.customer_actions_log (
    id             bigserial PRIMARY KEY,
    customer_id    bigint NOT NULL,
    status         text NOT NULL CHECK (status IN ('done', 'snoozed', 'cleared')),
    snoozed_until  timestamptz(3),
    note           text,
    expected_loss  bigint,
    changed_at     timestamptz(3) NOT NULL DEFAULT now(),
    changed_by     text
);
CREATE INDEX customer_actions_log_customer_idx ON scoring.customer_actions_log (customer_id, id DESC);
