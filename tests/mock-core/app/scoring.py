"""Расчёт risk-score, денежной оценки и сводки.

Сигналы признаков складываются по схеме «шумного ИЛИ»:
    score = 100 * (1 - Π(1 - weight_i / 100 * signal_i))
Один признак с весом 45 и сигналом 1 даёт 45 пунктов, несколько признаков
добавляют риск с убывающей отдачей, и итог никогда не выходит за 100.
Вклад признака — доля score, пропорциональная его weight * signal,
поэтому сумма вкладов равна итоговому score.
"""

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .features import (
    DAYS_IN_MONTH,
    FEATURES,
    Context,
    Feature,
    days_to_expiry,
    human_days,
    monthly_price,
    plural,
)
from .store import Account, DataStore, Service

AT_RISK_LEVELS = ("high", "medium")
# Деньги на балансе снижают ожидаемую потерю не больше чем наполовину:
# клиент может вывести остаток или просто перестать пользоваться услугами.
BALANCE_COVERAGE_CAP = 0.5
# Признаки аккаунта влияют на оценку отдельной услуги вполсилы, иначе
# одна проблема с оплатой делала бы рискованными все услуги клиента сразу.
ACCOUNT_WEIGHT_IN_SERVICE = 0.5
REASON_MIN_CONTRIBUTION = 0.5
FACT_DAYS = 14
OVERDUE_SPREAD_DAYS = 7


def eur(cents: float) -> str:
    text = f"{cents / 100:,.2f}".replace(",", " ").replace(".", ",")
    return f"{text} €"


def level_for(score: Optional[float], thresholds: Dict[str, float]) -> str:
    if score is None:
        return "unknown"
    if score >= thresholds["high"]:
        return "high"
    if score >= thresholds["medium"]:
        return "medium"
    return "low"


@dataclass
class Part:
    feature: Feature
    weight: float
    value: float
    explanation: str
    service: Optional[Service] = None
    contribution: float = 0.0


def combine(parts: List[Part]) -> float:
    probs = [max(0.0, min(1.0, p.weight / 100 * p.value)) for p in parts]
    remaining = 1.0
    for pr in probs:
        remaining *= 1 - pr
    score = 100 * (1 - remaining)
    total = sum(probs)
    for part, pr in zip(parts, probs):
        part.contribution = round(score * pr / total, 1) if total > 0 else 0.0
    return round(score, 1)


def reasons_from(parts: List[Part], with_service_name: bool) -> List[Dict[str, Any]]:
    reasons = []
    for p in sorted(parts, key=lambda x: x.contribution, reverse=True):
        if p.contribution < REASON_MIN_CONTRIBUTION:
            continue
        title = p.explanation
        if with_service_name and p.service is not None:
            title = f"{p.service.name}: {title}"
        reasons.append(
            {
                "code": p.feature.code,
                "title": title,
                "contribution": p.contribution,
                "service_id": p.service.id if p.service is not None else None,
            }
        )
    return reasons


@dataclass
class ServiceResult:
    service: Service
    days_to_expiry: float
    monthly_price: float
    score: Optional[float]
    level: str
    reasons: List[Dict[str, Any]]
    exposure: int = 0
    expected_loss: int = 0

    @property
    def at_risk(self) -> bool:
        return self.level in AT_RISK_LEVELS


@dataclass
class CustomerResult:
    account: Account
    score: Optional[float]
    level: str
    reasons: List[Dict[str, Any]]
    features: List[Dict[str, Any]]
    services: List[ServiceResult]
    services_total: int
    services_at_risk: int
    monthly_revenue: int
    exposure: int
    expected_loss: int
    balance_coverage_days: Optional[float]
    money_explanation: str
    recommendations: List[Dict[str, str]] = field(default_factory=list)

    @property
    def loyalty(self) -> Optional[float]:
        return None if self.score is None else round(100 - self.score, 1)

    def fingerprint(self) -> Tuple:
        return (self.score, self.level, self.expected_loss, self.exposure, self.services_at_risk, self.monthly_revenue)


RECOMMENDATIONS = {
    "autoprolong_off_expiring": (
        "enable_autoprolong",
        "Предложить включить автопродление",
        "Напомнить о скором окончании срока и показать, как включить автопродление в один клик.",
    ),
    "prolong_wait_status": (
        "remind_payment",
        "Напомнить об оплате продления",
        "Отправить письмо или пуш со ссылкой на оплату, пока услуга не удалилась.",
    ),
    "suspended_or_blocked": (
        "contact_support",
        "Связаться и помочь разблокировать услугу",
        "Выяснить причину блокировки или приостановки и предложить решение — поддержку, рассрочку, смену тарифа.",
    ),
    "low_balance_coverage": (
        "topup_bonus",
        "Предложить бонус за пополнение баланса",
        "Например, +5% на баланс при пополнении в ближайшую неделю.",
    ),
    "failed_payments": (
        "check_payment_method",
        "Попросить обновить способ оплаты",
        "Платежи не проходят — стоит предложить привязать другую карту или оплатить иначе.",
    ),
    "recent_deletions": (
        "exit_survey",
        "Узнать, почему клиент сокращает услуги",
        "Короткий опрос письмом: что не устроило и что можно улучшить, плюс персональная скидка на оставшиеся услуги.",
    ),
    "no_recent_payments": (
        "reactivation",
        "Вернуть интерес к сервису",
        "Реактивационная рассылка: новые возможности, полезные гайды, специальное предложение.",
    ),
}


def _recommendations(level: str, reasons: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    if level == "unknown":
        return [
            {
                "code": "winback",
                "title": "Запустить кампанию возврата",
                "detail": "Действующих услуг нет — можно предложить скидку на новый заказ.",
            }
        ]
    result: List[Dict[str, str]] = []
    if level == "high":
        result.append(
            {
                "code": "personal_email",
                "title": "Написать клиенту лично в ближайшие дни",
                "detail": "Риск ухода высокий: личное письмо от менеджера работает лучше массовой рассылки.",
            }
        )
    seen = set()
    for r in reasons:
        rec = RECOMMENDATIONS.get(r["code"])
        if rec and rec[0] not in seen:
            seen.add(rec[0])
            result.append({"code": rec[0], "title": rec[1], "detail": rec[2]})
        if len(result) >= 4:
            break
    if not result:
        result.append(
            {
                "code": "keep_in_touch",
                "title": "Поддерживать обычную коммуникацию",
                "detail": "Признаков ухода нет — подойдут стандартные рассылки и предложения расширить услуги.",
            }
        )
    return result


def _unknown_customer(account: Account, services: List[Service], now) -> CustomerResult:
    if services:
        n = len(services)
        text = f"Оценки нет: все {n} {plural(n, 'услуга', 'услуги', 'услуг')} клиента удалены, оценивать нечего."
    else:
        text = "Оценки нет: у клиента нет ни одной услуги."
    service_results = [
        ServiceResult(
            s,
            days_to_expiry(s, now),
            monthly_price(s),
            None,
            "unknown",
            [{"code": "service_deleted", "title": "Услуга удалена.", "contribution": 0, "service_id": s.id}],
        )
        for s in services
    ]
    features = [
        {
            "code": f.code,
            "title": f.title,
            "group": f.group,
            "value": 0.0,
            "weight": 0.0,
            "contribution": 0.0,
            "explanation": "Не оценивается: у клиента нет действующих услуг.",
        }
        for f in FEATURES
    ]
    return CustomerResult(
        account=account,
        score=None,
        level="unknown",
        reasons=[{"code": "no_live_services", "title": text, "contribution": 0, "service_id": None}],
        features=features,
        services=service_results,
        services_total=0,
        services_at_risk=0,
        monthly_revenue=0,
        exposure=0,
        expected_loss=0,
        balance_coverage_days=None,
        money_explanation="У клиента нет действующих услуг, поэтому выручки под риском нет и ожидаемая потеря равна нулю.",
        recommendations=_recommendations("unknown", []),
    )


def score_customer(store: DataStore, account_id: int, settings: Dict[str, Any]) -> CustomerResult:
    account = store.accounts[account_id]
    now = store.reference_time
    services = store.account_services(account_id)
    live = [s for s in services if s.live]
    if not live:
        return _unknown_customer(account, services, now)

    horizon = settings["horizon_days"]
    thresholds = settings["thresholds"]
    weights = settings["weights"]

    def effective(code: str) -> float:
        w = weights[code]
        return w["weight"] if w["enabled"] else 0.0

    transactions = store.account_transactions(account_id)
    daily_cost = sum(monthly_price(s) for s in live) / DAYS_IN_MONTH
    base = Context(account, services, live, transactions, now, horizon, daily_cost)

    account_parts: List[Part] = []
    service_parts: Dict[int, List[Part]] = {s.id: [] for s in live}
    for f in FEATURES:
        if f.level == "account":
            value, text = f.compute(base)
            account_parts.append(Part(f, effective(f.code), value, text))
        else:
            for s in live:
                ctx = Context(account, services, live, transactions, now, horizon, daily_cost, s)
                value, text = f.compute(ctx)
                service_parts[s.id].append(Part(f, effective(f.code), value, text, s))

    coverage_days = account.balance / daily_cost if daily_cost > 0 else None
    coverage_factor = 1.0
    if coverage_days is not None:
        coverage_factor = 1 - BALANCE_COVERAGE_CAP * min(1.0, coverage_days / horizon)
    horizon_months = horizon / DAYS_IN_MONTH

    service_results: List[ServiceResult] = []
    for s in services:
        dte = days_to_expiry(s, now)
        mp = monthly_price(s)
        if not s.live:
            service_results.append(
                ServiceResult(
                    s, dte, mp, None, "unknown",
                    [{"code": "service_deleted", "title": "Услуга удалена.", "contribution": 0, "service_id": s.id}],
                )
            )
            continue
        parts = [Part(p.feature, p.weight, p.value, p.explanation, p.service) for p in service_parts[s.id]]
        parts += [
            Part(p.feature, p.weight * ACCOUNT_WEIGHT_IN_SERVICE, p.value, p.explanation) for p in account_parts
        ]
        score = combine(parts)
        res = ServiceResult(s, dte, mp, score, level_for(score, thresholds), reasons_from(parts, False))
        if res.at_risk:
            res.exposure = round(mp * horizon_months)
            res.expected_loss = round(res.exposure * score / 100 * coverage_factor)
        service_results.append(res)

    # на уровне клиента берём по каждому признаку услуги самый сильный сигнал
    customer_parts: List[Part] = []
    for f in FEATURES:
        if f.level == "account":
            customer_parts.append(next(p for p in account_parts if p.feature.code == f.code))
            continue
        candidates = [p for s in live for p in service_parts[s.id] if p.feature.code == f.code]
        best = max(candidates, key=lambda p: (p.value, -days_to_expiry(p.service, now), -p.service.id))
        customer_parts.append(Part(best.feature, best.weight, best.value, best.explanation, best.service))
    score = combine(customer_parts)
    level = level_for(score, thresholds)
    reasons = reasons_from(customer_parts, True)

    features = []
    for p in customer_parts:
        text = p.explanation
        if p.service is not None:
            text = f"{p.service.name}: {text}"
        features.append(
            {
                "code": p.feature.code,
                "title": p.feature.title,
                "group": p.feature.group,
                "value": round(p.value, 3),
                "weight": p.weight,
                "contribution": p.contribution,
                "explanation": text,
            }
        )

    at_risk = [r for r in service_results if r.at_risk]
    monthly_revenue = round(sum(monthly_price(s) for s in live))
    exposure = sum(r.exposure for r in at_risk)
    expected_loss = sum(r.expected_loss for r in at_risk)

    n_live = len(live)
    text = (
        f"Клиент приносит {eur(monthly_revenue)} в месяц с {n_live} "
        f"{plural(n_live, 'действующей услуги', 'действующих услуг', 'действующих услуг')}."
    )
    if at_risk:
        k = len(at_risk)
        text += (
            f" Под риском {k} {plural(k, 'услуга', 'услуги', 'услуг')}: если они не продлятся, "
            f"за {horizon} {plural(horizon, 'день', 'дня', 'дней')} компания потеряет до {eur(exposure)}."
            " С учётом вероятности ухода по каждой услуге"
        )
        if coverage_days is not None:
            text += f" и баланса, которого хватит примерно на {human_days(coverage_days)},"
        text += f" ожидаемая потеря — {eur(expected_loss)}."
    else:
        text += f" Услуг под риском нет, ожидаемая потеря за {horizon} {plural(horizon, 'день', 'дня', 'дней')} — {eur(0)}."

    return CustomerResult(
        account=account,
        score=score,
        level=level,
        reasons=reasons,
        features=features,
        services=service_results,
        services_total=n_live,
        services_at_risk=len(at_risk),
        monthly_revenue=monthly_revenue,
        exposure=exposure,
        expected_loss=expected_loss,
        balance_coverage_days=round(coverage_days, 1) if coverage_days is not None else None,
        money_explanation=text,
        recommendations=_recommendations(level, reasons),
    )


def score_all(store: DataStore, settings: Dict[str, Any]) -> Dict[int, CustomerResult]:
    return {aid: score_customer(store, aid, settings) for aid in store.account_ids()}


# --- сводка ---


def by_level(results: Iterable[CustomerResult]) -> Dict[str, int]:
    counts = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
    for r in results:
        counts[r.level] += 1
    return counts


def short_summary(results: Dict[int, CustomerResult]) -> Dict[str, Any]:
    values = results.values()
    return {
        "customers_total": len(results),
        "by_level": by_level(values),
        "expected_loss": sum(r.expected_loss for r in values),
        "exposure": sum(r.exposure for r in values),
    }


def _timeline(store: DataStore, results: Dict[int, CustomerResult], horizon: int) -> List[Dict[str, Any]]:
    ref_day: date = store.reference_time.date()
    at_risk = [sr for r in results.values() for sr in r.services if sr.at_risk]
    points: List[Dict[str, Any]] = []

    lost_statuses = ("deleted", "prolong_wait", "suspended", "blocked")
    losses: Dict[date, int] = {}
    for s in store.services.values():
        if s.status in lost_statuses:
            day = s.expires_at.date()
            losses[day] = losses.get(day, 0) + 1
    for offset in range(FACT_DAYS - 1, -1, -1):
        day = ref_day - timedelta(days=offset)
        points.append(
            {
                "date": day.isoformat(),
                "kind": "fact",
                "deletions": losses.get(day, 0),
                "at_risk_services": sum(1 for sr in at_risk if sr.service.created_at.date() <= day),
                "expected_nonrenewals": None,
                "expected_loss": None,
            }
        )

    nonrenewals = [0.0] * (horizon + 1)
    loss = [0.0] * (horizon + 1)
    for sr in at_risk:
        p = (sr.score or 0) / 100
        d = sr.days_to_expiry
        if d <= 0:
            # срок уже вышел: решение клиента ждём в ближайшую неделю
            span = min(OVERDUE_SPREAD_DAYS, horizon)
            for i in range(1, span + 1):
                nonrenewals[i] += p / span
                loss[i] += sr.expected_loss / span
            continue
        k = max(1, math.ceil(d))
        if k <= horizon:
            nonrenewals[k] += p
            loss[k] += sr.expected_loss
        else:
            # срок за горизонтом: потерю размазываем равномерно, решения о продлении в окне нет
            for i in range(1, horizon + 1):
                loss[i] += sr.expected_loss / horizon
    remaining = float(len(at_risk))
    for i in range(1, horizon + 1):
        remaining -= nonrenewals[i]
        points.append(
            {
                "date": (ref_day + timedelta(days=i)).isoformat(),
                "kind": "forecast",
                "deletions": None,
                "at_risk_services": max(0, round(remaining)),
                "expected_nonrenewals": round(nonrenewals[i], 3),
                "expected_loss": round(loss[i]),
            }
        )
    return points


def full_summary(store: DataStore, results: Dict[int, CustomerResult], settings: Dict[str, Any]) -> Dict[str, Any]:
    horizon = settings["horizon_days"]
    values = list(results.values())
    services_total = sum(r.services_total for r in values)
    services_at_risk = sum(r.services_at_risk for r in values)
    monthly_total = sum(r.monthly_revenue for r in values)
    at_risk_monthly = sum(sr.monthly_price for r in values for sr in r.services if sr.at_risk)
    share = min(1.0, at_risk_monthly / monthly_total) if monthly_total > 0 else 0.0
    expected_loss = sum(r.expected_loss for r in values)
    exposure = sum(r.exposure for r in values)

    titles = {f.code: f.title for f in FEATURES}
    agg: Dict[str, Dict[str, float]] = {}
    for r in values:
        if not r.score:
            continue
        for reason in r.reasons:
            if reason["contribution"] < 1:
                continue
            a = agg.setdefault(reason["code"], {"customers": 0, "expected_loss": 0.0})
            a["customers"] += 1
            a["expected_loss"] += r.expected_loss * reason["contribution"] / r.score
    top = sorted(agg.items(), key=lambda kv: (-kv[1]["customers"], -kv[1]["expected_loss"], kv[0]))[:5]
    top_reasons = [
        {"code": code, "title": titles.get(code, code), "customers": int(a["customers"]), "expected_loss": round(a["expected_loss"])}
        for code, a in top
    ]

    days_word = plural(horizon, "день", "дня", "дней")
    explanation = (
        f"Действующие услуги приносят {eur(monthly_total)} в месяц. "
        f"Под риском {services_at_risk} {plural(services_at_risk, 'услуга', 'услуги', 'услуг')} "
        f"({round(share * 100)}% выручки): если все они не продлятся, за {horizon} {days_word} "
        f"компания потеряет до {eur(exposure)}. Ожидаемая потеря с учётом вероятности ухода "
        f"и денег на балансах клиентов — {eur(expected_loss)}."
    )
    return {
        "customers_total": len(values),
        "by_level": by_level(values),
        "services_total": services_total,
        "services_at_risk": services_at_risk,
        "expected_loss": expected_loss,
        "exposure": exposure,
        "monthly_revenue_total": monthly_total,
        "revenue_at_risk_share": round(share, 4),
        "horizon_days": horizon,
        "money_explanation": explanation,
        "top_reasons": top_reasons,
        "timeline": _timeline(store, results, horizon),
    }
