-- Сценарии удержания: «если условие — то действие» после каждого пересчёта, с контрольной
-- группой. Пишет api (исполнитель сценариев в app/playbooks.py), scoring эти таблицы не трогает.
-- Внешних ключей на customer_scores нет по той же причине, что у customer_actions: оценки
-- перезаписываются, а история срабатываний нужна для оценки эффекта.

CREATE TABLE scoring.playbooks (
    id             bigserial PRIMARY KEY,
    name           text NOT NULL,
    description    text,
    enabled        boolean NOT NULL DEFAULT false,
    -- simulation: только считаем, кого бы задело; live: выполняем действия
    mode           text NOT NULL DEFAULT 'simulation' CHECK (mode IN ('simulation', 'live')),
    conditions     jsonb NOT NULL,
    actions        jsonb NOT NULL,
    control_share  double precision NOT NULL DEFAULT 0.2 CHECK (control_share >= 0 AND control_share <= 0.5),
    max_per_day    integer NOT NULL DEFAULT 50 CHECK (max_per_day > 0),
    cooldown_days  integer NOT NULL DEFAULT 14 CHECK (cooldown_days > 0),
    preset         text,
    created_at     timestamptz(3) NOT NULL DEFAULT now(),
    created_by     text,
    updated_at     timestamptz(3) NOT NULL DEFAULT now(),
    updated_by     text
);

-- Запуск сценария: по пересчёту (scores) или вручную из панели (manual).
CREATE TABLE scoring.playbook_runs (
    id                bigserial PRIMARY KEY,
    playbook_id       bigint NOT NULL REFERENCES scoring.playbooks (id) ON DELETE CASCADE,
    cause             text NOT NULL CHECK (cause IN ('scores', 'manual')),
    mode              text NOT NULL CHECK (mode IN ('simulation', 'live')),
    matched           integer NOT NULL,
    treated           integer NOT NULL,
    control           integer NOT NULL,
    skipped_cooldown  integer NOT NULL DEFAULT 0,
    skipped_limit     integer NOT NULL DEFAULT 0,
    skipped_no_email  integer NOT NULL DEFAULT 0,
    emails            integer NOT NULL DEFAULT 0,
    created_at        timestamptz(3) NOT NULL DEFAULT now(),
    created_by        text
);
CREATE INDEX playbook_runs_playbook_idx ON scoring.playbook_runs (playbook_id, id DESC);

-- Клиент, попавший под сценарий: затронутый (treated) или контроль (control). Состояние в
-- момент срабатывания — точка отсчёта для «риск снизился / пополнил / не ушёл».
CREATE TABLE scoring.playbook_hits (
    id                 bigserial PRIMARY KEY,
    playbook_id        bigint NOT NULL REFERENCES scoring.playbooks (id) ON DELETE CASCADE,
    run_id             bigint NOT NULL REFERENCES scoring.playbook_runs (id) ON DELETE CASCADE,
    customer_id        bigint NOT NULL,
    grp                text NOT NULL CHECK (grp IN ('treated', 'control')),
    risk_level_at      text NOT NULL,
    risk_score_at      double precision,
    expected_loss_at   bigint NOT NULL,
    balance_at         bigint NOT NULL,
    replenished_at     bigint NOT NULL,
    actions            jsonb NOT NULL DEFAULT '[]',   -- [{type, result}]
    email_id           bigint,
    hit_at             timestamptz(3) NOT NULL DEFAULT now()
);
CREATE INDEX playbook_hits_playbook_idx ON scoring.playbook_hits (playbook_id, hit_at DESC);
CREATE INDEX playbook_hits_customer_idx ON scoring.playbook_hits (playbook_id, customer_id, hit_at DESC);

-- Что исполнитель уже видел: отметка последнего пересчёта и уровни клиентов с прошлого
-- раза (для условия «стал high»).
CREATE TABLE scoring.playbook_state (
    id            smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    last_stamp    text,
    levels_ready  boolean NOT NULL DEFAULT false,
    updated_at    timestamptz(3) NOT NULL DEFAULT now()
);
INSERT INTO scoring.playbook_state (id) VALUES (1);

CREATE TABLE scoring.playbook_levels (
    customer_id  bigint PRIMARY KEY,
    risk_level   text NOT NULL
);
