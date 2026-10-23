from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

registry = CollectorRegistry(auto_describe=True)

customers_by_level = Gauge(
    "churn_customers_by_risk_level", "Клиенты по уровням риска", ["level"], registry=registry
)
expected_loss_eur = Gauge("churn_expected_loss_eur", "Ожидаемая потеря за горизонт, евро", registry=registry)
exposure_eur = Gauge("churn_exposure_eur", "Выручка под риском за горизонт, евро", registry=registry)
services_at_risk = Gauge("churn_services_at_risk", "Услуги с уровнем риска medium или high", registry=registry)
model_version = Gauge("churn_model_version", "Версия настроек модели", registry=registry)
last_scored = Gauge(
    "churn_last_scored_timestamp_seconds", "Время последнего пересчёта, unix-время", registry=registry
)
scoring_duration = Histogram(
    "churn_scoring_duration_seconds",
    "Длительность пересчёта",
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
    registry=registry,
)
scoring_runs = Counter("churn_scoring_runs_total", "Количество пересчётов", ["kind"], registry=registry)
simulator_events = Counter("churn_simulator_events_total", "Изменения, сделанные симулятором", registry=registry)

http_requests = Counter(
    "http_requests_total", "HTTP-запросы", ["service", "method", "route", "status"], registry=registry
)
http_duration = Histogram(
    "http_request_duration_seconds", "Длительность HTTP-запросов", ["service", "route"], registry=registry
)


def update_scores(summary: dict, version: int, scored_at_ts: float) -> None:
    for level, count in summary["by_level"].items():
        customers_by_level.labels(level=level).set(count)
    expected_loss_eur.set(summary["expected_loss"] / 100)
    exposure_eur.set(summary["exposure"] / 100)
    services_at_risk.set(summary["services_at_risk"])
    model_version.set(version)
    last_scored.set(scored_at_ts)


def render() -> bytes:
    return generate_latest(registry)
