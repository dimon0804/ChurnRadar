"""Периоды оплаты, приведение цен к месяцу, график продлений и формат сумм."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta

from .types import PaymentTerm, Service

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
    """Первый момент, когда суммарные продления превысят доступные деньги."""
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
    return high


def format_eur(cents: float) -> str:
    amount = round(cents) / 100
    whole, fraction = f"{amount:,.2f}".split(".")
    whole = whole.replace(",", " ")
    if fraction == "00":
        return f"€{whole}"
    return f"€{whole},{fraction}"
