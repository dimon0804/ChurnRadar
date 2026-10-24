"""Расчёт риска: noisy-OR по услуге, взвешивание выручкой по клиенту, разложение по признакам."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .base import Feature, Signal
from .context import (
    SECONDS_PER_DAY,
    AccountContext,
    ServiceContext,
    build_account_context,
    build_service_context,
)
from .money import format_eur, horizon_revenue
from .recommendations import action_for, build_recommendations
from .registry import account_features, service_features
from .text import format_date
from .types import (
    GONE_STATUSES,
    Account,
    RiskLevel,
    Service,
    Settings,
    Transaction,
    ensure_utc,
    isoformat_z,
)

# Вклад сигнала с силой ровно 1: −ln(0) бесконечен, поэтому ограничиваем.
LOG_CAP = 50.0
REASON_MIN_POINTS = 0.5
REASONS_LIMIT = 5
TRANSACTIONS_LIMIT = 50

UNKNOWN_NO_SERVICES = "Нет действующих услуг: клиент уже ушёл или ещё не купил"
UNKNOWN_ALL_GONE = "Все услуги клиента удалены или заблокированы"
UNKNOWN_NOT_PAID = "Нет платных услуг: оценивать нечего"
# Короткие подписи причин «нет оценки» для таблиц, как title у признаков.
UNKNOWN_LABELS = {
    "no_live_services": "Нет услуг",
    "all_services_gone": "Уже ушёл",
    "no_paid_services": "Нет платных услуг",
}


def noisy_or(strengths: Iterable[float]) -> float:
    """p = 1 − Π(1 − s_i): вероятность, что сработает хотя бы одна из независимых причин."""
    survive = 1.0
    for s in strengths:
        survive *= 1.0 - s
    return 1.0 - survive


def _log_weight(strength: float) -> float:
    if strength >= 1.0:
        return LOG_CAP
    return min(-math.log1p(-strength), LOG_CAP)


def decompose(strengths: Sequence[float]) -> list[float]:
    """Делит p между причинами пропорционально −ln(1 − s_i); сумма долей равна p."""
    p = noisy_or(strengths)
    logs = [_log_weight(s) for s in strengths]
    total = sum(logs)
    if total <= 0:
        return [0.0 for _ in strengths]
    return [p * log / total for log in logs]


@dataclass(frozen=True, slots=True)
class FeatureHit:
    feature: Feature[Any]
    signal: Signal
    weight: float
    contribution: float

    @property
    def strength(self) -> float:
        return self.weight * self.signal.value


@dataclass(frozen=True, slots=True)
class ServiceOutcome:
    service: Service
    days_to_expiry: float
    monthly_price: float
    revenue: float
    probability: float | None
    level: RiskLevel
    hits: tuple[FeatureHit, ...] = ()
    contract_revenue: float = 0.0
    revenue_note: str | None = None

    @property
    def risk_score(self) -> float | None:
        return None if self.probability is None else 100.0 * self.probability

    @property
    def expected_loss(self) -> float:
        return (self.probability or 0.0) * self.revenue

    def to_api(self) -> dict[str, Any]:
        s = self.service
        reasons = [
            {
                "code": hit.feature.code,
                "title": hit.signal.explanation,
                "label": hit.feature.title,
                "contribution": 100.0 * hit.contribution,
                "service_id": s.id,
                "action": action_for(hit.feature, hit.signal).title,
                "expected_loss": round(hit.contribution * self.revenue),
            }
            for hit in sorted(self.hits, key=lambda h: h.contribution, reverse=True)
            if 100.0 * hit.contribution > 0
        ][:REASONS_LIMIT]
        return {
            "id": s.id,
            "name": s.name,
            "status": s.status,
            "payment_term": s.payment_term,
            "price": s.price,
            "monthly_price": round(self.monthly_price),
            "auto_prolong": s.auto_prolong,
            "created_at": isoformat_z(s.created_at),
            "expires_at": isoformat_z(s.expires_at),
            "days_to_expiry": round(self.days_to_expiry, 2),
            "risk_score": self.risk_score,
            "risk_level": self.level,
            "reasons": reasons,
            "expected_loss": round(self.expected_loss),
            "horizon_revenue": round(self.revenue),
            "revenue_note": self.revenue_note,
        }


@dataclass(frozen=True, slots=True)
class FeatureTotal:
    """Признак на уровне клиента: суммарный вклад и самое весомое срабатывание."""

    feature: Feature[Any]
    points: float
    loss: float
    value: float
    weight: float
    top_hit: FeatureHit
    top_service_id: int | None
    # На сколько снизится ожидаемая потеря клиента, если причина исчезнет, евроценты.
    saving: float = 0.0

    @property
    def code(self) -> str:
        return self.feature.code

    @property
    def service_id(self) -> int | None:
        return self.top_service_id if self.feature.level == "service" else None

    def to_reason(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "title": self.top_hit.signal.explanation,
            "label": self.feature.title,
            "contribution": self.points,
            "service_id": self.service_id,
            "action": action_for(self.feature, self.top_hit.signal).title,
            "expected_loss": round(self.loss),
        }

    def to_api(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "title": self.feature.title,
            "group": self.feature.group,
            "value": self.value,
            "weight": self.weight,
            "contribution": self.points,
            "explanation": self.top_hit.signal.explanation,
        }


@dataclass(frozen=True, slots=True)
class CustomerResult:
    customer_id: int
    email: str
    balance: int
    bonus_balance: int
    bonus_state: str
    withdraw_balance: int
    total_replenished: int
    risk_score: float | None
    risk_level: RiskLevel
    loyalty: float | None
    reasons: list[dict[str, Any]]
    services_total: int
    services_at_risk: int
    monthly_revenue: int
    expected_loss: int
    exposure: int
    detail: dict[str, Any]
    model_version: int
    as_of: datetime
    horizon_revenue: float = 0.0
    services: tuple[ServiceOutcome, ...] = field(default=(), repr=False)
    feature_totals: tuple[FeatureTotal, ...] = field(default=(), repr=False)

    def to_row(self, scored_at: datetime | None = None) -> dict[str, Any]:
        """Строка таблицы scoring.customer_scores."""
        return {
            "customer_id": self.customer_id,
            "email": self.email,
            "balance": self.balance,
            "bonus_balance": self.bonus_balance,
            "bonus_state": self.bonus_state,
            "withdraw_balance": self.withdraw_balance,
            "total_replenished": self.total_replenished,
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "loyalty": self.loyalty,
            "reasons": self.reasons,
            "services_total": self.services_total,
            "services_at_risk": self.services_at_risk,
            "monthly_revenue": self.monthly_revenue,
            "expected_loss": self.expected_loss,
            "exposure": self.exposure,
            "detail": self.detail,
            "model_version": self.model_version,
            "scored_at": ensure_utc(scored_at or self.as_of),
        }

    def to_summary_api(self) -> dict[str, Any]:
        """Карточка клиента в форме CustomerSummary."""
        return {
            "id": self.customer_id,
            "email": self.email,
            "balance": self.balance,
            "bonus_balance": self.bonus_balance,
            "bonus_state": self.bonus_state,
            "withdraw_balance": self.withdraw_balance,
            "total_replenished": self.total_replenished,
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "loyalty": self.loyalty,
            "reasons": self.reasons,
            "services_total": self.services_total,
            "services_at_risk": self.services_at_risk,
            "monthly_revenue": self.monthly_revenue,
            "expected_loss": self.expected_loss,
            "exposure": self.exposure,
        }

    def to_api(self, scored_at: datetime | None = None, model_version: int | None = None) -> dict[str, Any]:
        """Полная карточка в форме CustomerDetail."""
        return {
            **self.to_summary_api(),
            **self.detail,
            "scored_at": isoformat_z(scored_at or self.as_of),
            "model_version": self.model_version if model_version is None else model_version,
        }


def _score_service(
    ctx: ServiceContext,
    account_signals: list[tuple[Feature[Any], Signal, float]],
    settings: Settings,
) -> ServiceOutcome:
    found: list[tuple[Feature[Any], Signal, float]] = []
    for feature in service_features():
        weight = settings.effective_weight(feature.code)
        if weight <= 0:
            continue
        signal = feature.compute(ctx)
        if signal is not None and signal.value > 0:
            found.append((feature, signal, weight))
    found.extend(account_signals)

    strengths = [weight * signal.value for _, signal, weight in found]
    contributions = decompose(strengths)
    hits = tuple(
        FeatureHit(feature, signal, weight, share)
        for (feature, signal, weight), share in zip(found, contributions, strict=True)
    )
    probability = noisy_or(strengths)
    return ServiceOutcome(
        service=ctx.service,
        days_to_expiry=ctx.days_to_expiry,
        monthly_price=ctx.monthly_price,
        revenue=ctx.horizon_revenue,
        probability=probability,
        level=settings.level_for(100.0 * probability),
        hits=hits,
        contract_revenue=ctx.contract_revenue,
        revenue_note=_revenue_note(ctx),
    )


def _revenue_note(ctx: ServiceContext) -> str | None:
    if not ctx.revenue_capped:
        return None
    account = ctx.account_ctx
    return (
        f"По цене {ctx.service.name} принесла бы {format_eur(ctx.contract_revenue)} "
        f"за {ctx.horizon_days} дн., но клиент вносит в среднем {format_eur(account.replenishment_rate)} "
        f"в месяц и держит на балансе {format_eur(account.spendable)}, поэтому в расчёт берём "
        f"{format_eur(ctx.horizon_revenue)}: больше он заплатить не сможет"
    )


def _inactive_outcome(service: Service, account_ctx: AccountContext) -> ServiceOutcome:
    ctx = build_service_context(service, account_ctx)
    return ServiceOutcome(
        service=service,
        days_to_expiry=ctx.days_to_expiry,
        monthly_price=ctx.monthly_price,
        revenue=0.0,
        probability=None,
        level="unknown",
    )


def _account_signals(ctx: AccountContext, settings: Settings) -> list[tuple[Feature[Any], Signal, float]]:
    signals: list[tuple[Feature[Any], Signal, float]] = []
    for feature in account_features():
        weight = settings.effective_weight(feature.code)
        if weight <= 0:
            continue
        signal = feature.compute(ctx)
        if signal is not None and signal.value > 0:
            signals.append((feature, signal, weight))
    return signals


def _saving_share(outcome: ServiceOutcome, removed: FeatureHit) -> float:
    """На сколько упадёт вероятность непродления услуги без этой причины: p − p(без неё)."""
    rest = noisy_or(hit.strength for hit in outcome.hits if hit is not removed)
    return (outcome.probability or 0.0) - rest


def _feature_totals(live: Sequence[ServiceOutcome], total_revenue: float) -> list[FeatureTotal]:
    grouped: dict[str, list[tuple[FeatureHit, ServiceOutcome]]] = defaultdict(list)
    savings: dict[str, float] = defaultdict(float)
    for outcome in live:
        for hit in outcome.hits:
            grouped[hit.feature.code].append((hit, outcome))
            savings[hit.feature.code] += outcome.revenue * _saving_share(outcome, hit)

    totals = []
    for pairs in grouped.values():
        loss = sum(hit.contribution * outcome.revenue for hit, outcome in pairs)
        top_hit, top_outcome = max(pairs, key=lambda pair: pair[0].contribution * pair[1].revenue)
        totals.append(
            FeatureTotal(
                feature=top_hit.feature,
                points=100.0 * loss / total_revenue,
                loss=loss,
                value=max(hit.signal.value for hit, _ in pairs),
                weight=100.0 * top_hit.weight,
                top_hit=top_hit,
                top_service_id=top_outcome.service.id,
                saving=savings[top_hit.feature.code],
            )
        )
    totals.sort(key=lambda t: (-t.points, t.code))
    return totals


def _coverage_text(ctx: AccountContext) -> tuple[float | None, str]:
    """На сколько дней хватит баланса — по тому же графику автосписаний, что и признак
    «не хватит денег», поэтому дата здесь и в причине всегда одна."""
    funds = ctx.funds
    if funds.runs_out_at is None:
        if not funds.has_renewals:
            return None, "Автопродление выключено, по графику с баланса ничего не спишется."
        # дальше COVERAGE_LIMIT_DAYS (год) график не строим
        return None, "Баланса хватит на автопродления больше чем на год."
    days = max((funds.runs_out_at - ctx.as_of).total_seconds() / SECONDS_PER_DAY, 0.0)
    date = format_date(funds.runs_out_at)
    if days < 1:
        return days, f"Баланса не хватит уже на ближайшее автопродление ({date})."
    return days, f"Баланса хватит на автопродления до {date} (на {math.floor(days)} дн.)."


def _money(
    monthly_revenue: float,
    expected_loss: float,
    exposure: float,
    ctx: AccountContext,
    capped: int = 0,
) -> dict[str, Any]:
    horizon_days = ctx.horizon_days
    coverage: float | None = None
    revenue = horizon_revenue(monthly_revenue, horizon_days)
    if monthly_revenue <= 0:
        explanation = "Действующих платных услуг нет, терять сейчас нечего."
        runs_out_at = None
    else:
        coverage, coverage_text = _coverage_text(ctx)
        runs_out_at = ctx.funds.runs_out_at
        explanation = (
            f"За {horizon_days} дн. клиент приносит {format_eur(revenue)}. "
            f"С учётом риска по каждой услуге ожидаем потерять {format_eur(expected_loss)}. "
            f"{coverage_text}"
        )
        if capped:
            explanation += (
                f" Выручку {capped} усл. посчитали не по цене, а по тому, сколько клиент способен заплатить."
            )
    return {
        "monthly_revenue": round(monthly_revenue),
        "horizon_revenue": round(revenue),
        "exposure": round(exposure),
        "expected_loss": round(expected_loss),
        "balance_coverage_days": None if coverage is None else round(coverage, 1),
        "funds_run_out_at": None if runs_out_at is None else isoformat_z(runs_out_at),
        "explanation": explanation,
    }


def _transactions_api(transactions: Iterable[Transaction]) -> list[dict[str, Any]]:
    latest = sorted(transactions, key=lambda t: (t.created_at, t.id), reverse=True)[:TRANSACTIONS_LIMIT]
    return [
        {
            "id": t.id,
            "type": t.type,
            "status": t.status,
            "amount": t.amount,
            "created_at": isoformat_z(t.created_at),
            "performed_at": None if t.performed_at is None else isoformat_z(t.performed_at),
            "service_id": t.service_id,
        }
        for t in latest
    ]


def score_account(
    account: Account,
    services: Iterable[Service],
    transactions: Iterable[Transaction],
    settings: Settings,
    as_of: datetime,
) -> CustomerResult:
    as_of = ensure_utc(as_of)
    ctx = build_account_context(account, services, transactions, as_of, settings.horizon_days)

    outcomes: list[ServiceOutcome] = []
    account_signals = _account_signals(ctx, settings) if ctx.live_services else []
    for service in ctx.all_services:
        if service.is_live:
            outcomes.append(_score_service(build_service_context(service, ctx), account_signals, settings))
        else:
            outcomes.append(_inactive_outcome(service, ctx))
    outcomes.sort(key=lambda o: (o.probability is None, -(o.probability or 0.0), o.service.id))

    live = [o for o in outcomes if o.probability is not None]
    total_revenue = sum(o.revenue for o in live)
    # Месячная выручка — та же собираемая выручка, что и в score, приведённая к 30 дням.
    monthly = total_revenue * 30 / settings.horizon_days
    expected = sum(o.expected_loss for o in live)
    exposure = sum(o.revenue for o in live if o.level in ("medium", "high"))
    at_risk = sum(1 for o in live if o.level in ("medium", "high"))

    unknown_reason: tuple[str, str] | None = None
    if not live:
        if any(s.status in GONE_STATUSES for s in ctx.all_services):
            unknown_reason = ("all_services_gone", UNKNOWN_ALL_GONE)
        else:
            unknown_reason = ("no_live_services", UNKNOWN_NO_SERVICES)
    elif total_revenue <= 0:
        unknown_reason = ("no_paid_services", UNKNOWN_NOT_PAID)

    if unknown_reason is None:
        score: float | None = 100.0 * expected / total_revenue
        totals = _feature_totals(live, total_revenue)
        reasons = [t.to_reason() for t in totals if t.points > REASON_MIN_POINTS][:REASONS_LIMIT]
        features = [t.to_api() for t in totals]
    else:
        score = None
        totals = []
        reasons = [
            {
                "code": unknown_reason[0],
                "title": unknown_reason[1],
                "label": UNKNOWN_LABELS[unknown_reason[0]],
                "contribution": 0.0,
                "service_id": None,
            }
        ]
        features = []

    recommendations = build_recommendations(totals, unknown_reason[0] if unknown_reason else None)
    capped = sum(1 for o in live if o.revenue_note is not None)
    detail = {
        "services": [o.to_api() for o in outcomes],
        "features": features,
        "transactions": _transactions_api(ctx.transactions),
        "money": _money(monthly, expected, exposure, ctx, capped),
        "recommendations": [r.to_api() for r in recommendations],
        "next_action": recommendations[0].to_api() if recommendations else None,
    }
    return CustomerResult(
        customer_id=account.id,
        email=account.email,
        balance=account.balance,
        bonus_balance=account.bonus_balance,
        bonus_state=account.bonus_state,
        withdraw_balance=account.withdraw_balance,
        total_replenished=account.total_replenished,
        risk_score=score,
        risk_level=settings.level_for(score),
        loyalty=None if score is None else 100.0 - score,
        reasons=reasons,
        services_total=len(live),
        services_at_risk=at_risk,
        monthly_revenue=round(monthly),
        expected_loss=round(expected),
        exposure=round(exposure),
        detail=detail,
        model_version=settings.version,
        as_of=as_of,
        horizon_revenue=total_revenue,
        services=tuple(outcomes),
        feature_totals=tuple(totals),
    )


def score_customers(
    accounts: Iterable[Account],
    services: Iterable[Service],
    transactions: Iterable[Transaction],
    settings: Settings,
    as_of: datetime,
) -> list[CustomerResult]:
    services_by_owner: dict[int, list[Service]] = defaultdict(list)
    owner_of: dict[int, int] = {}
    for service in services:
        services_by_owner[service.owner_id].append(service)
        owner_of[service.id] = service.owner_id

    transactions_by_owner: dict[int, list[Transaction]] = defaultdict(list)
    for transaction in transactions:
        owner = owner_of.get(transaction.service_id)
        if owner is not None:
            transactions_by_owner[owner].append(transaction)

    return [
        score_account(
            account,
            services_by_owner.get(account.id, []),
            transactions_by_owner.get(account.id, []),
            settings,
            as_of,
        )
        for account in sorted(accounts, key=lambda a: a.id)
    ]
