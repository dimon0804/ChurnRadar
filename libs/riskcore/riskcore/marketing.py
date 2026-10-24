"""Маркетинговые метрики: MRR, ARPU, отток, LTV, сегменты по риску.

Все формулы — здесь, в одном месте. Деньги — евроценты, месяц — 30 дней, как в money.py.

Что считаем и откуда:

* MRR (месячная регулярная выручка) — выручка действующих услуг, приведённая к месяцу.
  Берём ту же собираемую выручку, по которой взвешивается score (money.collectible_revenue):
  почасовой сервер по цене дал бы сотни евро в месяц, а клиент столько никогда не платил.
  Поэтому MRR здесь совпадает с monthly_revenue_total в сводке.
* Прогнозный отток — из модели. risk_score клиента — доля его выручки, которую модель ожидает
  потерять за горизонт. Считаем клиента ушедшим на эту долю: отток клиентов — среднее по
  платящим, отток выручки — то же, взвешенное MRR. Горизонт пересчитывается в месяц:
  c_мес = 1 − (1 − c_гор)^(30 / горизонт).
* Фактический отток — по истории: ушедшие клиенты (все услуги удалены или заблокированы),
  делённые на суммарный срок наблюдения всех клиентов в месяцах (клиенто-месяцы).
  Дата ухода — из истории статусов, а если смены статуса никто не видел, оценка как в ingest:
  окончание оплаты + 5 дней, не позже опорного момента.
* LTV прогнозный = ARPU × маржа / месячный отток клиентов. Средний срок жизни = 1 / отток,
  но не больше LIFETIME_CAP_MONTHS: при оттоке около нуля формула даёт бесконечность.
  Здесь LTV — выручка (маржа 100%); маржу и CAC применяет apply_economics.
* LTV фактический — сколько клиент уже заплатил: сумма проведённых транзакций за услуги.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .types import (
    GONE_STATUSES,
    PROLONG_WAIT_GRACE,
    Service,
    StatusChange,
    Transaction,
    ensure_utc,
    isoformat_z,
)

if TYPE_CHECKING:
    from .scoring import CustomerResult

DAYS_PER_MONTH = 30
SECONDS_PER_DAY = 86_400
# Срок жизни при оттоке около нуля ограничиваем пятью годами.
LIFETIME_CAP_MONTHS = 60.0
# Топ-10% клиентов по MRR для концентрации выручки.
TOP_SHARE = 0.10
LEVELS = ("high", "medium", "low", "unknown")
AT_RISK_LEVELS = ("high", "medium")
PAYMENT_TERMS = ("hour", "week", "month", "quarter_year", "half_year", "year")
RETENTION_SCENARIOS: tuple[tuple[str, ...], ...] = (("high",), ("high", "medium"))
DEFAULT_MARGIN = 1.0


# --- формулы ---------------------------------------------------------------------------------


def monthly_rate(horizon_share: float, horizon_days: int) -> float:
    """Доля за горизонт → доля за 30 дней при постоянной интенсивности: 1 − (1 − p)^(30/h)."""
    p = min(max(horizon_share, 0.0), 1.0)
    if p >= 1.0:
        return 1.0
    return 1.0 - (1.0 - p) ** (DAYS_PER_MONTH / horizon_days)


def expected_lifetime(monthly_churn: float) -> tuple[float, bool]:
    """Ожидаемый срок жизни в месяцах = 1 / отток; второй элемент — упёрлись ли в потолок."""
    if monthly_churn <= 1.0 / LIFETIME_CAP_MONTHS:
        return LIFETIME_CAP_MONTHS, True
    return 1.0 / monthly_churn, False


def ltv(monthly_revenue: float, monthly_churn: float, margin: float = DEFAULT_MARGIN) -> float:
    """LTV = выручка в месяц × маржа × ожидаемый срок жизни (1 / отток, с потолком)."""
    lifetime, _ = expected_lifetime(monthly_churn)
    return monthly_revenue * margin * lifetime


def payback_months(cac: float | None, arpu: float, margin: float) -> float | None:
    """За сколько месяцев клиент окупит стоимость привлечения: CAC / (ARPU × маржа)."""
    if cac is None or cac <= 0 or arpu * margin <= 0:
        return None
    return cac / (arpu * margin)


def ltv_to_cac(ltv_value: float, cac: float | None) -> float | None:
    if cac is None or cac <= 0:
        return None
    return ltv_value / cac


def _months(start: datetime, end: datetime) -> float:
    return max((end - start).total_seconds() / SECONDS_PER_DAY / DAYS_PER_MONTH, 0.0)


# --- клиент ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Payments:
    """Проведённые платежи клиента: сумма, число ненулевых и первый контакт с нами."""

    total: int
    count: int
    first_seen: datetime | None


def payments_of(services: Iterable[Service], transactions: Iterable[Transaction]) -> Payments:
    """Первый контакт — самая ранняя услуга или транзакция; платёж — проведённая транзакция.

    Нулевые проведённые транзакции (бесплатный заказ) в сумму входят, но платежом
    для среднего чека не считаются.
    """
    total = 0
    count = 0
    moments = [s.created_at for s in services]
    for t in transactions:
        moments.append(t.created_at)
        if t.status == "performed":
            total += t.amount
            if t.amount > 0:
                count += 1
    return Payments(total=total, count=count, first_seen=min(moments) if moments else None)


def exit_moment(
    services: Iterable[Service], as_of: datetime, seen: dict[int, datetime] | None = None
) -> datetime | None:
    """Когда клиент ушёл: позже всех ушла последняя услуга. None — у клиента есть живые услуги.

    seen — даты ухода услуг из истории статусов. Без неё оцениваем как ingest: окончание
    оплаты + 5 дней ожидания продления, но не позже опорного момента.
    """
    services = list(services)
    if not services or any(s.status not in GONE_STATUSES for s in services):
        return None
    seen = seen or {}
    moments = [seen.get(s.id) or min(s.expires_at + PROLONG_WAIT_GRACE, as_of) for s in services]
    return min(max(moments), as_of)


def customer_marketing(
    monthly_revenue: float,
    risk_score: float | None,
    horizon_days: int,
    payments: Payments,
    left_at: datetime | None,
    as_of: datetime,
) -> dict[str, Any]:
    """Блок CustomerMarketing для карточки. ltv_forecast — выручка (маржа 100%)."""
    churn = None if risk_score is None else monthly_rate(risk_score / 100.0, horizon_days)
    lifetime: float | None = None
    capped = False
    forecast = 0.0
    if churn is not None and monthly_revenue > 0:
        lifetime, capped = expected_lifetime(churn)
        forecast = monthly_revenue * lifetime
    end = left_at or as_of
    tenure = None if payments.first_seen is None else max((end - payments.first_seen).total_seconds(), 0.0)
    return {
        "mrr": round(monthly_revenue),
        "monthly_churn": churn,
        "expected_lifetime_months": None if lifetime is None else round(lifetime, 2),
        "lifetime_capped": capped,
        "ltv_forecast": round(forecast),
        "ltv_fact": payments.total,
        "payments": payments.count,
        "avg_check": round(payments.total / payments.count) if payments.count else None,
        "first_seen": None if payments.first_seen is None else isoformat_z(payments.first_seen),
        "tenure_days": None if tenure is None else round(tenure / SECONDS_PER_DAY, 1),
        "left_at": None if left_at is None else isoformat_z(left_at),
    }


# --- база ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Customer:
    level: str
    mrr: float
    horizon_churn: float | None
    monthly_churn: float | None
    lifetime: float
    ltv: float
    active: bool
    auto_prolong: bool
    paid: int
    payments: int
    start: datetime | None
    end: datetime | None
    left: bool

    @property
    def paying(self) -> bool:
        return self.monthly_churn is not None and self.mrr > 0

    @property
    def expected_mrr_loss(self) -> float:
        return self.mrr * (self.monthly_churn or 0.0)


def _customer(
    result: CustomerResult, seen: dict[int, datetime], horizon_days: int, as_of: datetime
) -> _Customer:
    live = [o for o in result.services if o.probability is not None]
    mrr = result.horizon_revenue * DAYS_PER_MONTH / horizon_days
    horizon_churn = None if result.risk_score is None or mrr <= 0 else result.risk_score / 100.0
    churn = None if horizon_churn is None else monthly_rate(horizon_churn, horizon_days)
    lifetime, _ = expected_lifetime(churn) if churn is not None else (0.0, False)
    services = [o.service for o in result.services]
    left_at = exit_moment(services, as_of, seen)
    start = None if result.first_seen is None else ensure_utc(result.first_seen)
    end = None if start is None else max(left_at or as_of, start)
    return _Customer(
        level=result.risk_level,
        mrr=mrr,
        horizon_churn=horizon_churn,
        monthly_churn=churn,
        lifetime=lifetime,
        ltv=mrr * lifetime if churn is not None else 0.0,
        active=bool(live),
        auto_prolong=any(o.service.auto_prolong for o in live),
        paid=result.paid_total,
        payments=result.payments,
        start=start,
        end=end,
        left=left_at is not None,
    )


def _seen_exits(status_history: Iterable[StatusChange]) -> dict[int, datetime]:
    seen: dict[int, datetime] = {}
    for change in status_history:
        if change.new_status not in GONE_STATUSES:
            continue
        at = ensure_utc(change.changed_at)
        if change.service_id not in seen or at > seen[change.service_id]:
            seen[change.service_id] = at
    return seen


def _div(a: float, b: float) -> float | None:
    return None if b <= 0 else a / b


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _segment(level: str, customers: Sequence[_Customer]) -> dict[str, Any]:
    paying = [c for c in customers if c.paying]
    mrr = sum(c.mrr for c in customers)
    ltv_sum = sum(c.ltv for c in customers)
    churn = _mean([c.monthly_churn or 0.0 for c in paying])
    return {
        "level": level,
        "customers": len(customers),
        "mrr": round(mrr),
        "arpu": round(mrr / len(paying)) if paying else 0,
        "monthly_churn": churn,
        "ltv": round(ltv_sum),
        "ltv_avg": round(ltv_sum / len(paying)) if paying else 0,
        "expected_mrr_loss": round(sum(c.expected_mrr_loss for c in customers)),
    }


def _fact_churn(customers: Sequence[_Customer]) -> dict[str, Any]:
    observed = [c for c in customers if c.start is not None and c.end is not None]
    exposure = sum(_months(c.start, c.end) for c in observed if c.start and c.end)
    left = [c for c in observed if c.left]
    left_months = sum(_months(c.start, c.end) for c in left if c.start and c.end)
    return {
        "customers_observed": len(observed),
        "customers_left": len(left),
        "exposure_months": round(exposure, 2),
        # без ушедших факт есть (никто не ушёл), но для сценариев удержания он бесполезен
        "monthly_churn": _div(len(left), exposure),
        "avg_tenure_months": _div(exposure, len(observed)),
        "avg_lifetime_left_months": _div(left_months, len(left)),
    }


def _retention(
    levels: tuple[str, ...], customers: Sequence[_Customer], target: float | None, source: str
) -> dict[str, Any]:
    chosen = [c for c in customers if c.level in levels and c.paying]
    now = sum(c.ltv for c in chosen)
    out: dict[str, Any] = {
        "levels": list(levels),
        "customers": len(chosen),
        "mrr": round(sum(c.mrr for c in chosen)),
        "ltv_now": round(now),
        "ltv_retained": None,
        "ltv_uplift": None,
        "saved_mrr": None,
        "target_monthly_churn": target,
        "target_source": source,
    }
    if target is None:
        return out
    lifetime, _ = expected_lifetime(target)
    uplift = sum(c.mrr * max(lifetime - c.lifetime, 0.0) for c in chosen)
    saved = sum(c.mrr * max((c.monthly_churn or 0.0) - target, 0.0) for c in chosen)
    out.update({"ltv_retained": round(now + uplift), "ltv_uplift": round(uplift), "saved_mrr": round(saved)})
    return out


def _retention_target(fact: dict[str, Any], segments: dict[str, dict[str, Any]]) -> tuple[float | None, str]:
    """К какому оттоку вернётся удержанный клиент: к фактическому по истории, иначе к оттоку
    клиентов низкого риска. Если нет ни того, ни другого — сценарий не считаем."""
    if fact["customers_left"] > 0 and fact["monthly_churn"] is not None:
        return float(fact["monthly_churn"]), "fact"
    low = segments["low"]
    if low["customers"] and low["monthly_churn"] is not None:
        return float(low["monthly_churn"]), "low_risk"
    return None, "none"


def build_marketing(
    results: Sequence[CustomerResult],
    status_history: Iterable[StatusChange],
    horizon_days: int,
    as_of: datetime,
) -> dict[str, Any]:
    """Маркетинговые метрики базы без маржи и CAC (их применяет apply_economics)."""
    as_of = ensure_utc(as_of)
    seen = _seen_exits(status_history)
    customers = [_customer(r, seen, horizon_days, as_of) for r in results]
    active = [c for c in customers if c.active]
    paying = [c for c in customers if c.paying]

    mrr = sum(c.mrr for c in customers)
    expected_mrr_loss = sum(c.expected_mrr_loss for c in customers)
    customer_churn = _mean([c.monthly_churn or 0.0 for c in paying])
    customer_horizon = _mean([c.horizon_churn or 0.0 for c in paying])
    revenue_churn = _div(expected_mrr_loss, mrr)
    revenue_horizon = _div(sum(c.mrr * (c.horizon_churn or 0.0) for c in paying), mrr)
    arpu = mrr / len(active) if active else 0.0
    arppu = mrr / len(paying) if paying else 0.0
    lifetime, capped = expected_lifetime(customer_churn) if customer_churn is not None else (0.0, False)

    by_level = {level: [c for c in customers if c.level == level] for level in LEVELS}
    segments = {level: _segment(level, group) for level, group in by_level.items()}
    fact = _fact_churn(customers)
    target, source = _retention_target(fact, segments)

    paid_total = sum(c.paid for c in customers)
    payments = sum(c.payments for c in customers)
    ranked = sorted((c.mrr for c in paying), reverse=True)
    top_n = max(1, math.ceil(len(ranked) * TOP_SHARE)) if ranked else 0
    auto = [c for c in active if c.auto_prolong]

    term_mrr = dict.fromkeys(PAYMENT_TERMS, 0.0)
    term_count = dict.fromkeys(PAYMENT_TERMS, 0)
    auto_mrr = 0.0
    for r in results:
        for o in r.services:
            if o.probability is None:
                continue
            monthly = o.revenue * DAYS_PER_MONTH / horizon_days
            term_mrr[o.service.payment_term] += monthly
            term_count[o.service.payment_term] += 1
            if o.service.auto_prolong:
                auto_mrr += monthly

    ltv_total = sum(c.ltv for c in customers)
    return {
        "horizon_days": horizon_days,
        "customers_total": len(customers),
        "active_customers": len(active),
        "paying_customers": len(paying),
        "mrr": round(mrr),
        "arpu": round(arpu),
        "arppu": round(arppu),
        "churn": {
            "customer_horizon": customer_horizon,
            "customer_monthly": customer_churn,
            "revenue_horizon": revenue_horizon,
            "revenue_monthly": revenue_churn,
            "expected_customers_lost": sum(c.monthly_churn or 0.0 for c in paying),
            "expected_mrr_loss": round(expected_mrr_loss),
            "fact": fact,
        },
        "retention": {
            "customer_monthly": None if customer_churn is None else 1.0 - customer_churn,
            "revenue_monthly": None if revenue_churn is None else 1.0 - revenue_churn,
        },
        "lifetime": {
            "expected_months": round(lifetime, 2) if customer_churn is not None else None,
            "capped": capped,
            "cap_months": LIFETIME_CAP_MONTHS,
            "fact_avg_months": fact["avg_tenure_months"],
        },
        "ltv": round(arpu * lifetime),
        "ltv_total": round(ltv_total),
        "ltv_at_risk": round(sum(segments[level]["ltv"] for level in AT_RISK_LEVELS)),
        "ltv_fact": {
            "total": paid_total,
            "avg_per_customer": round(paid_total / len(customers)) if customers else 0,
            "payments": payments,
            "avg_check": round(paid_total / payments) if payments else None,
        },
        "concentration": {
            "top_customers": top_n,
            "top_share": _div(sum(ranked[:top_n]), mrr),
        },
        "auto_prolong": {
            "customers": len(auto),
            "customers_share": _div(len(auto), len(active)),
            "mrr_share": _div(auto_mrr, mrr),
        },
        "payment_terms": [
            {
                "term": term,
                "services": term_count[term],
                "mrr": round(term_mrr[term]),
                "share": _div(term_mrr[term], mrr),
            }
            for term in PAYMENT_TERMS
        ],
        "segments": [segments[level] for level in LEVELS],
        "retention_scenarios": [
            _retention(levels, customers, target, source) for levels in RETENTION_SCENARIOS
        ],
    }


# --- маржа и CAC -----------------------------------------------------------------------------


def _scale(value: Any, margin: float) -> Any:
    return None if value is None else round(value * margin)


def apply_economics(
    base: dict[str, Any], margin: float = DEFAULT_MARGIN, cac: int | None = None
) -> dict[str, Any]:
    """Маржа и стоимость привлечения поверх базовых метрик.

    LTV в базе — выручка; здесь он умножается на маржу. LTV/CAC и срок окупаемости без CAC
    не считаются (null): придумывать стоимость привлечения мы не можем.
    """
    out = dict(base)
    for key in ("ltv", "ltv_total", "ltv_at_risk"):
        out[key] = _scale(base.get(key), margin)
    out["segments"] = [
        {**row, "ltv": _scale(row["ltv"], margin), "ltv_avg": _scale(row["ltv_avg"], margin)}
        for row in base.get("segments", [])
    ]
    out["retention_scenarios"] = [
        {**row, **{k: _scale(row.get(k), margin) for k in ("ltv_now", "ltv_retained", "ltv_uplift")}}
        for row in base.get("retention_scenarios", [])
    ]
    ltv_value = float(out.get("ltv") or 0)
    out["unit_economics"] = {
        "margin": margin,
        "cac": cac,
        "ltv_cac": ltv_to_cac(ltv_value, cac),
        "payback_months": payback_months(cac, float(base.get("arpu") or 0), margin),
    }
    return out


def scale_customer(block: dict[str, Any], margin: float = DEFAULT_MARGIN) -> dict[str, Any]:
    """Блок карточки клиента с учётом маржи: прогнозный LTV — выручка × маржа."""
    return {**block, "ltv_forecast": _scale(block.get("ltv_forecast"), margin) or 0, "margin": margin}
