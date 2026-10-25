"""Периоды оплаты, приведение цен к месяцу, график продлений и формат сумм."""

from __future__ import annotations

import calendar
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from .types import PaymentTerm, Service

EPSILON = timedelta(microseconds=1)
# Насколько вперёд смотрим, когда считаем, на сколько дней хватит баланса.
COVERAGE_LIMIT_DAYS = 365
FIXED_PERIODS: dict[str, timedelta] = {"hour": timedelta(hours=1), "week": timedelta(days=7)}
CALENDAR_PERIODS: dict[str, int] = {"month": 1, "quarter_year": 3, "half_year": 6, "year": 12}

# Сколько раз за 30-дневный месяц оплачивается период.
MONTHLY_FACTOR: dict[str, float] = {
    "hour": 24 * 30,
    "week": 30 / 7,
    "month": 1.0,
    "quarter_year": 1 / 3,
    "half_year": 1 / 6,
    "year": 1 / 12,
}


def monthly_price(price: int, term: PaymentTerm) -> float:
    """Цена услуги, приведённая к месяцу в 30 дней, в евроцентах (без округления)."""
    return price * MONTHLY_FACTOR[term]


def horizon_revenue(monthly: float, horizon_days: int) -> float:
    return monthly * horizon_days / 30


def replenishment_rate(total_replenished: int, lifetime_days: float) -> float:
    """Сколько клиент в среднем вносит за 30 дней. Срок жизни меньше месяца считаем за месяц."""
    return max(total_replenished, 0) / max(lifetime_days / 30, 1.0)


def payment_capacity(spendable: int, rate: float, horizon_days: int) -> float:
    """Сколько клиент способен заплатить за горизонт: то, что уже на балансе, плюс обычные пополнения."""
    return max(spendable, 0) + rate * horizon_days / 30


def collectible_revenue(contract: float, capacity: float, price: int) -> float:
    """Выручка услуги за горизонт, которую реально можно получить.

    По цене услуга может обещать больше, чем клиент когда-либо платил (почасовой сервер
    за €0,95 в час — это €684 в месяц). Больше, чем клиент способен заплатить, одна услуга
    принести не может; при этом хотя бы один платёж за период считаем всегда.
    """
    return min(contract, max(capacity, float(price)))


def add_months(moment: datetime, months: int) -> datetime:
    month_index = moment.month - 1 + months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def add_periods(moment: datetime, term: PaymentTerm, count: int) -> datetime:
    if term in FIXED_PERIODS:
        return moment + FIXED_PERIODS[term] * count
    return add_months(moment, CALENDAR_PERIODS[term] * count)


@dataclass(frozen=True, slots=True)
class RenewalPlan:
    """Продления одной услуги внутри горизонта: first, first+период, ... не позже end."""

    service_id: int
    term: PaymentTerm
    price: int
    first: datetime
    end: datetime

    def count_until(self, moment: datetime) -> int:
        limit = min(moment, self.end)
        if limit < self.first:
            return 0
        if self.term in FIXED_PERIODS:
            return int((limit - self.first) / FIXED_PERIODS[self.term]) + 1
        count = 0
        while add_periods(self.first, self.term, count) <= limit:
            count += 1
        return count

    @property
    def count(self) -> int:
        return self.count_until(self.end)

    @property
    def cost(self) -> int:
        return self.count * self.price

    def cost_until(self, moment: datetime) -> int:
        return self.count_until(moment) * self.price

    def cost_before(self, moment: datetime) -> int:
        """Сколько стоят продления строго раньше moment."""
        return self.cost_until(moment - EPSILON)

    def next_after(self, moment: datetime) -> datetime | None:
        """Первое продление позже moment, если оно попадает в горизонт."""
        if moment < self.first:
            return self.first
        if self.term in FIXED_PERIODS:
            step = FIXED_PERIODS[self.term]
            candidate = self.first + step * (int((moment - self.first) / step) + 1)
        else:
            candidate = add_periods(self.first, self.term, self.count_until(moment))
        return candidate if candidate <= self.end else None


def renewal_plan(service: Service, as_of: datetime, horizon_days: int) -> RenewalPlan | None:
    """График продлений автопродляемой живой услуги. Просроченная оплата считается срочной."""
    if not (service.is_live and service.auto_prolong):
        return None
    end = as_of + timedelta(days=horizon_days)
    first = max(service.expires_at, as_of)
    if first > end:
        return None
    return RenewalPlan(service.id, service.payment_term, service.price, first, end)


def shortfall_moment(plans: list[RenewalPlan], spendable: int) -> datetime | None:
    """Момент первого продления, на которое уже не хватит денег.

    Продления оплачиваются по очереди, во времени. Двоичный поиск сужает окно до минуты,
    а затем берётся точный момент ближайшего продления внутри окна.
    """
    if not plans or sum(plan.cost for plan in plans) <= spendable:
        return None

    def spent(moment: datetime) -> int:
        return sum(plan.cost_until(moment) for plan in plans)

    low = min(plan.first for plan in plans)
    if spent(low) > spendable:
        return low
    high = max(plan.end for plan in plans)
    while high - low > timedelta(minutes=1):
        middle = low + (high - low) / 2
        if spent(middle) > spendable:
            high = middle
        else:
            low = middle
    moments = [moment for plan in plans if (moment := plan.next_after(low)) is not None and moment <= high]
    return min(moments, default=high)


def funds_run_out(
    services: Iterable[Service],
    as_of: datetime,
    spendable: int,
    horizon_days: int,
    shortfall_at: datetime | None,
) -> datetime | None:
    """Когда баланса не хватит на очередное автопродление, если смотреть дальше горизонта.

    Тот же расчёт, что и для признака «не хватит денег»: продления списываются по графику,
    по очереди. Внутри горизонта момент уже известен (shortfall_at), иначе смотрим на год
    вперёд. None — автопродлений нет или денег хватит дольше.
    """
    if shortfall_at is not None or horizon_days >= COVERAGE_LIMIT_DAYS:
        return shortfall_at
    plans = [plan for s in services if (plan := renewal_plan(s, as_of, COVERAGE_LIMIT_DAYS)) is not None]
    return shortfall_moment(plans, spendable)


# Промокод или бонус не больше месячной выручки клиента и не больше пятой части того,
# что действие сбережёт: иначе скидка стоит дороже, чем мы спасаем.
OFFER_MRR_SHARE = 1.0
OFFER_SAVING_SHARE = 0.20


def offer_size(wanted: float, monthly_revenue: float, saving: float = float("inf")) -> int:
    """Размер промокода или бонуса, евроценты, вниз до целого евро.

    wanted — сколько хотелось бы дать (нехватка денег, доля от пополнения); дальше
    ограничиваем месячной выручкой клиента и долей ожидаемого сбережения.
    """
    cap = min(wanted, monthly_revenue * OFFER_MRR_SHARE, saving * OFFER_SAVING_SHARE)
    return int(max(cap, 0.0) // 100) * 100


def format_eur(cents: float) -> str:
    amount = round(cents) / 100
    whole, fraction = f"{amount:,.2f}".split(".")
    whole = whole.replace(",", " ")
    if fraction == "00":
        return f"€{whole}"
    return f"€{whole},{fraction}"
