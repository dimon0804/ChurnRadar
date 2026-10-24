-- Графики и фильтр клиентов по причине риска.

-- Фильтр «клиенты с этой причиной». Коды причин лежат глубоко в jsonb: главная — первая
-- в reasons, все — в detail.features (у клиентов без оценки features пустой, их единственная
-- причина в reasons). Вычисляемые колонки считает сам Postgres при записи строки, поэтому
-- scoring про них не знает, а фильтр идёт по индексу, а не по разбору detail у каждой строки.
ALTER TABLE scoring.customer_scores
    ADD COLUMN main_reason text GENERATED ALWAYS AS (reasons -> 0 ->> 'code') STORED,
    ADD COLUMN reason_codes jsonb GENERATED ALWAYS AS (
        jsonb_path_query_array(detail, '$.features[*].code') || jsonb_path_query_array(reasons, '$[*].code')
    ) STORED;
CREATE INDEX customer_scores_main_reason_idx ON scoring.customer_scores (main_reason);
CREATE INDEX customer_scores_reason_codes_idx ON scoring.customer_scores USING gin (reason_codes);

-- Как менялись главные цифры сводки. Точку пишет scoring вместе со сводкой: после каждого
-- полного пересчёта и не чаще раза в 10 секунд после частичных. Точек не больше 500: когда
-- их больше, старую половину прореживаем через одну, так что видна и давняя история, и
-- подробная свежая.
CREATE TABLE scoring.summary_history (
    id                     bigserial PRIMARY KEY,
    scored_at              timestamptz(3) NOT NULL,
    model_version          integer NOT NULL,
    full_run               boolean NOT NULL,
    customers_total        integer NOT NULL,
    high                   integer NOT NULL,
    medium                 integer NOT NULL,
    low                    integer NOT NULL,
    unknown                integer NOT NULL,
    services_at_risk       integer NOT NULL,
    expected_loss          bigint NOT NULL,
    exposure               bigint NOT NULL,
    monthly_revenue_total  bigint NOT NULL,
    revenue_at_risk_share  double precision NOT NULL
);
CREATE INDEX summary_history_scored_idx ON scoring.summary_history (scored_at);
