"""Сводка по всей базе и лента «факт / прогноз»."""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from .money import FIXED_PERIODS, format_eur
from .registry import get_feature
from .scoring import CustomerResult, ServiceOutcome
from .text import format_percent
from .types import PROLONG_WAIT_GRACE, Service, Settings, StatusChange, ensure_utc, isoformat_z

FACT_DAYS = 30
AT_RISK_LEVELS = ("medium", "high")
# Верхние границы корзин распределения score: 10, 20, ..., 100.
RISK_SCORE_BUCKETS = tuple(range(10, 101, 10))


def decision_moment(service: Service) -> datetime | None:
    """Когда решится судьба услуги. None — решение размазано по горизонту (период короче дня)."""
    if service.status == "prolong_wait":
        return service.expires_at + PROLONG_WAIT_GRACE
    if service.payment_term in FIXED_PERIODS and FIXED_PERIODS[service.payment_term] < timedelta(days=1):
        return None
    return service.expires_at


@dataclass(frozen=True, slots=True)
class _Decision:
    outcome: ServiceOutcome
    day: date | None
    spread: bool


def _decision_day(outcome: ServiceOutcome, as_of: datetime, horizon_days: int) -> _Decision:
    moment = decision_moment(outcome.service)
    if moment is None:
        return _Decision(outcome, None, spread=True)
    first_day = as_of.date() + timedelta(days=1)
    if moment > as_of + timedelta(days=horizon_days):
        return _Decision(outcome, None, spread=False)
    return _Decision(outcome, max(moment.date(), first_day), spread=False)


def build_timeline(
    outcomes: Sequence[ServiceOutcome],
    status_history: Iterable[StatusChange],
    as_of: datetime,
    horizon_days: int,
) -> list[dict[str, Any]]:
    as_of = ensure_utc(as_of)
    today = as_of.date()

    deletions: Counter[date] = Counter(
        ensure_utc(change.changed_at).date() for change in status_history if change.new_status == "deleted"
    )
    points: list[dict[str, Any]] = [
        {
            "date": day.isoformat(),
            "kind": "fact",
            "deletions": deletions.get(day, 0),
            "at_risk_services": None,
            "expected_nonrenewals": None,
            "expected_loss": None,
        }
        for day in (today - timedelta(days=offset) for offset in range(FACT_DAYS - 1, -1, -1))
    ]

    decisions = [_decision_day(o, as_of, horizon_days) for o in outcomes if o.probability is not None]
    nonrenewals: dict[date, float] = defaultdict(float)
    losses: dict[date, float] = defaultdict(float)
    spread_p = 0.0
    spread_loss = 0.0
    for d in decisions:
        p = d.outcome.probability or 0.0
        if d.spread:
            spread_p += p / horizon_days
            spread_loss += d.outcome.expected_loss / horizon_days
        elif d.day is not None:
            nonrenewals[d.day] += p
            losses[d.day] += d.outcome.expected_loss

    risky = [d for d in decisions if d.outcome.level in AT_RISK_LEVELS]
    for offset in range(1, horizon_days + 1):
        day = today + timedelta(days=offset)
        points.append(
            {
                "date": day.isoformat(),
                "kind": "forecast",
                "deletions": None,
                "at_risk_services": sum(1 for d in risky if d.day is None or d.day >= day),
                "expected_nonrenewals": nonrenewals.get(day, 0.0) + spread_p,
                "expected_loss": round(losses.get(day, 0.0) + spread_loss),
            }
        )
    return points


def _top_reasons(results: Sequence[CustomerResult]) -> list[dict[str, Any]]:
    """Главные причины по базе. Клиенты и деньги считаются по одному правилу.

    customers — у скольких оценённых клиентов причина есть в разложении score (features[]),
    primary_customers — у скольких она главная (первая в reasons), expected_loss — сколько
    ожидаемых потерь на неё приходится у этих же клиентов. Причин без клиентов в списке нет.
    """
    customers: Counter[str] = Counter()
    primary: Counter[str] = Counter()
    loss: dict[str, float] = defaultdict(float)
    for result in results:
        if result.risk_score is None:
            continue
        for total in result.feature_totals:
            customers[total.code] += 1
            loss[total.code] += total.loss
        if result.reasons:
            primary[result.reasons[0]["code"]] += 1

    ranked = sorted(customers, key=lambda code: (-round(loss[code]), -customers[code], code))
    rows: list[dict[str, Any]] = []
    for code in ranked:
        feature = get_feature(code)
        rows.append(
            {
                "code": code,
                "title": feature.title if feature else code,
                "customers": customers[code],
                "primary_customers": primary[code],
                "expected_loss": round(loss[code]),
            }
        )
    return rows


def _risk_scores(results: Sequence[CustomerResult]) -> dict[str, Any]:
    """Распределение score оценённых клиентов: среднее, максимум и накопленные корзины.

    buckets[i].count — сколько клиентов со score не выше le (как le у гистограмм Prometheus),
    по этим числам api отдаёт churn_risk_score_bucket.
    """
    scores = sorted(r.risk_score for r in results if r.risk_score is not None)
    return {
        "scored": len(scores),
        "avg": sum(scores) / len(scores) if scores else None,
        "max": scores[-1] if scores else None,
        "buckets": [{"le": le, "count": bisect_right(scores, le)} for le in RISK_SCORE_BUCKETS],
    }


def _money_explanation(revenue: float, expected: float, exposure: float, horizon_days: int) -> str:
    if revenue <= 0:
        return "Действующих платных услуг нет, денежного риска нет."
    share = expected / revenue
    return (
        f"За {horizon_days} дн. действующие услуги принесут {format_eur(revenue)}. "
        f"С учётом риска по каждой услуге ожидаем потерять {format_eur(expected)}, "
        f"это {format_percent(share)} выручки. Если уйдут все услуги среднего и высокого риска, "
        f"потеря составит {format_eur(exposure)}."
    )


def build_summary(
    results: Sequence[CustomerResult],
    services: Iterable[Service],
    status_history: Iterable[StatusChange],
    settings: Settings,
    as_of: datetime,
    scored_at: datetime | None = None,
) -> dict[str, Any]:
    """Сводка в форме схемы Summary."""
    as_of = ensure_utc(as_of)
    by_level = Counter(r.risk_level for r in results)
    outcomes = [o for r in results for o in r.services]
    revenue = sum(r.horizon_revenue for r in results)
    expected = sum(o.expected_loss for o in outcomes if o.probability is not None)
    exposure = sum(r.exposure for r in results)
    live_services = sum(1 for s in services if s.is_live)

    return {
        "customers_total": len(results),
        "by_level": {level: by_level.get(level, 0) for level in ("high", "medium", "low", "unknown")},
        "services_total": live_services,
        "services_at_risk": sum(r.services_at_risk for r in results),
        "expected_loss": sum(r.expected_loss for r in results),
        "exposure": exposure,
        "monthly_revenue_total": sum(r.monthly_revenue for r in results),
        "revenue_at_risk_share": 0.0 if revenue <= 0 else min(max(expected / revenue, 0.0), 1.0),
        "horizon_days": settings.horizon_days,
        "money_explanation": _money_explanation(revenue, expected, exposure, settings.horizon_days),
        "top_reasons": _top_reasons(results),
        "risk_scores": _risk_scores(results),
        "timeline": build_timeline(outcomes, status_history, as_of, settings.horizon_days),
        "scored_at": isoformat_z(scored_at or as_of),
        "model_version": settings.version,
    }
