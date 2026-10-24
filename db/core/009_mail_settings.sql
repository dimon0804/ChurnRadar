-- Настройки писем клиентам: название компании для подписи ({{company}} в шаблонах).
--
-- Пишет api (роль admin). Строки нет — подпись «ChurnRadar».
-- IF NOT EXISTS: файл можно прогнать повторно (тесты других пакетов так и делают).

CREATE TABLE IF NOT EXISTS scoring.mail_settings (
    id          smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    company     text NOT NULL CHECK (length(btrim(company)) BETWEEN 1 AND 100),
    updated_at  timestamptz(3) NOT NULL DEFAULT now(),
    updated_by  text
);
