-- Журнал alerts.feed: системное событие «модель устарела» (дрейф признаков против эталона
-- калибровки, summary.drift.status = alert). Пишет alerts, персональных порогов у события нет.
ALTER TABLE alerts.feed DROP CONSTRAINT IF EXISTS feed_kind_check;
ALTER TABLE alerts.feed ADD CONSTRAINT feed_kind_check CHECK (kind IN (
    'high_risk', 'loss_jump', 'total_loss_jump', 'bulk',
    'loss_growth', 'big_customer_high', 'risk_share', 'high_count_growth', 'stale_data',
    'model_drift'
));
