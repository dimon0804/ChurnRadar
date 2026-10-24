-- Сводное оповещение о массовом изменении (загрузка снимка, пересчёт после смены модели):
-- одна запись в ленте вместо тысяч «клиент перешёл в высокий риск».
-- DROP IF EXISTS + ADD: файл можно прогнать повторно (тесты других пакетов так и делают).

ALTER TABLE alerts.feed DROP CONSTRAINT IF EXISTS feed_kind_check;
ALTER TABLE alerts.feed ADD CONSTRAINT feed_kind_check
    CHECK (kind IN ('high_risk', 'loss_jump', 'total_loss_jump', 'bulk'));
