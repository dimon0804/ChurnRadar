from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

RUNS = Counter("scoring_runs_total", "Пересчёты риска", ["kind", "result"])
DURATION = Histogram(
    "scoring_duration_seconds",
    "Длительность пересчёта",
    ["kind"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 40, 80, 160),
)
CUSTOMERS = Gauge("scoring_customers", "Клиентов в последнем расчёте")
EVENTS = Counter("scoring_events_total", "Полученные события", ["type", "result"])
LAST_RUN = Gauge("scoring_last_run_timestamp_seconds", "Время последнего успешного пересчёта")
