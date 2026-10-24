from __future__ import annotations

import calendar
from datetime import UTC, datetime, timedelta

TERMS = ("hour", "week", "month", "quarter_year", "half_year", "year")
MONTHS_BY_TERM = {"month": 1, "quarter_year": 3, "half_year": 6, "year": 12}
FIXED_PERIOD = {"hour": timedelta(hours=1), "week": timedelta(weeks=1)}


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def utcnow_ms() -> datetime:
    now = datetime.now(UTC)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def add_months(value: datetime, months: int) -> datetime:
    index = value.year * 12 + value.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def add_terms(value: datetime, term: str, count: int = 1) -> datetime:
    """Сдвиг на count периодов оплаты (count может быть отрицательным). Месяцы — календарно."""
    if term in FIXED_PERIOD:
        return value + FIXED_PERIOD[term] * count
    return add_months(value, MONTHS_BY_TERM[term] * count)


def boundary_after(created: datetime, term: str, moment: datetime) -> tuple[datetime, int]:
    """Первая граница оплаченного периода строго после moment и число периодов от created до неё."""
    if term in FIXED_PERIOD:
        period = FIXED_PERIOD[term]
        n = max(1, int((moment - created) // period) + 1)
        return created + period * n, n
    step = MONTHS_BY_TERM[term]
    months = (moment.year - created.year) * 12 + (moment.month - created.month)
    n = max(1, months // step)
    while add_months(created, step * n) <= moment:
        n += 1
    while n > 1 and add_months(created, step * (n - 1)) > moment:
        n -= 1
    return add_months(created, step * n), n


def period_starts(created: datetime, term: str, periods: int, last: int) -> list[datetime]:
    """Начала последних `last` оплаченных периодов (кроме первого — это заказ)."""
    first = max(1, periods - last)
    return [add_terms(created, term, i) for i in range(first, periods)]
