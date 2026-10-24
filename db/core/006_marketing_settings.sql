-- Настройки маркетинговых метрик: маржа и стоимость привлечения клиента (CAC).
--
-- Пишет api (роль admin), scoring их не читает: пересчёт модели они не запускают,
-- маржа и CAC применяются к готовым метрикам при выдаче /analytics/marketing.
-- IF NOT EXISTS: файл можно прогнать повторно (тесты других пакетов так и делают).

CREATE TABLE IF NOT EXISTS scoring.marketing_settings (
    id          smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    -- доля выручки, которая остаётся прибылью, 0..1; 1 — LTV считается в выручке
    margin      double precision NOT NULL DEFAULT 1 CHECK (margin > 0 AND margin <= 1),
    -- сколько стоит привлечь одного клиента, евроценты; NULL — не задано
    cac         bigint CHECK (cac IS NULL OR cac > 0),
    updated_at  timestamptz(3) NOT NULL DEFAULT now(),
    updated_by  text
);
