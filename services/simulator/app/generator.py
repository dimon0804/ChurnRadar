"""Генератор расширенного набора данных в формате SourceSnapshot (data.json кейса).

Клиенты делятся на сегменты с разным поведением: лояльные, рискованные, уходящие и новые.
Цена услуги согласована с периодом оплаты (одна месячная ставка тарифа), транзакции
повторяют реальные оплаты по периодам, а пополнения аккаунта покрывают все списания.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .timeutil import add_terms, boundary_after, iso, period_starts

# Месячные ставки тарифов, евроценты.
MONTHLY_TARIFFS = (299, 499, 799, 1199, 1599, 2499, 3999, 5999, 8999, 14999)
TARIFF_WEIGHTS = (10, 18, 20, 16, 12, 9, 6, 4, 3, 2)

# Скидка за длинный период: квартал −5 %, полгода −10 %, год −15 %.
TERM_MULTIPLIER = {
    "hour": 1 / 720,
    "week": 7 / 30,
    "month": 1.0,
    "quarter_year": 3 * 0.95,
    "half_year": 6 * 0.90,
    "year": 12 * 0.85,
}

PREFIXES = ("vps", "vds", "dedic", "cloud", "web", "db", "mail", "vpn", "game", "storage")
MAX_PROLONG_HISTORY = 3


@dataclass(frozen=True, slots=True)
class Segment:
    name: str
    share: float
    services: tuple[int, int]
    terms: dict[str, int]
    statuses: dict[str, int]
    auto_prolong: float
    age_days: tuple[int, int]
    balance_months: tuple[float, float]
    zero_balance: float
    failed_prolong: float
    bonus_unlocked: float


SEGMENTS: tuple[Segment, ...] = (
    Segment(
        name="loyal",
        share=0.58,
        services=(1, 4),
        terms={"hour": 2, "week": 3, "month": 50, "quarter_year": 15, "half_year": 10, "year": 20},
        statuses={"active": 88, "suspended": 4, "prolong_wait": 1, "deleted": 6, "blocked": 1},
        auto_prolong=0.88,
        age_days=(60, 1100),
        balance_months=(0.8, 6.0),
        zero_balance=0.03,
        failed_prolong=0.02,
        bonus_unlocked=0.4,
    ),
    Segment(
        name="risky",
        share=0.24,
        services=(1, 3),
        terms={"hour": 8, "week": 14, "month": 58, "quarter_year": 12, "half_year": 5, "year": 3},
        statuses={"active": 55, "suspended": 18, "prolong_wait": 10, "deleted": 15, "blocked": 2},
        auto_prolong=0.45,
        age_days=(20, 500),
        balance_months=(0.0, 1.1),
        zero_balance=0.25,
        failed_prolong=0.2,
        bonus_unlocked=0.6,
    ),
    Segment(
        name="churning",
        share=0.14,
        services=(1, 3),
        terms={"hour": 15, "week": 20, "month": 55, "quarter_year": 7, "half_year": 2, "year": 1},
        statuses={"active": 18, "suspended": 10, "prolong_wait": 24, "deleted": 42, "blocked": 6},
        auto_prolong=0.15,
        age_days=(10, 400),
        balance_months=(0.0, 0.35),
        zero_balance=0.55,
        failed_prolong=0.45,
        bonus_unlocked=0.5,
    ),
    Segment(
        name="newcomer",
        share=0.04,
        services=(0, 0),
        terms={"month": 1},
        statuses={"active": 1},
        auto_prolong=0.5,
        age_days=(0, 30),
        balance_months=(0.0, 0.0),
        zero_balance=0.7,
        failed_prolong=0.0,
        bonus_unlocked=0.5,
    ),
)


def price_for(monthly: int, term: str) -> int:
    return max(1, round(monthly * TERM_MULTIPLIER[term]))


def monthly_of(price: int, term: str) -> float:
    return price / TERM_MULTIPLIER[term]


def _pick(rng: random.Random, weights: dict[str, int]) -> str:
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys])[0]


class _Builder:
    def __init__(self, rng: random.Random, reference: datetime) -> None:
        self.rng = rng
        self.ref = reference
        self.services: list[dict[str, Any]] = []
        self.transactions: list[dict[str, Any]] = []
        self.next_service = 1
        self.next_tx = 1

    def tx(self, service_id: int, amount: int, tx_type: str, created: datetime, performed: bool) -> int:
        rng = self.rng
        performed_at = created + timedelta(seconds=rng.randint(1, 90)) if performed else None
        if performed_at is not None and performed_at > self.ref:
            performed_at = self.ref
        if created > self.ref:
            created = self.ref
        self.transactions.append(
            {
                "id": self.next_tx,
                "amount": amount,
                "status": "performed" if performed else "not_performed",
                "performedAt": iso(performed_at) if performed_at else None,
                "createdAt": iso(created),
                "type": tx_type,
                "serviceId": service_id,
            }
        )
        self.next_tx += 1
        return amount if performed else 0

    def service(self, owner_id: int, seg: Segment) -> tuple[int, float]:
        """Создаёт услугу с историей оплат. Возвращает (оплачено, месячная выручка если живая)."""
        rng, ref = self.rng, self.ref
        term = _pick(rng, seg.terms)
        status = _pick(rng, seg.statuses)
        monthly = rng.choices(MONTHLY_TARIFFS, weights=TARIFF_WEIGHTS)[0]
        price = price_for(monthly, term)
        auto = rng.random() < seg.auto_prolong
        age = timedelta(days=rng.uniform(*seg.age_days), seconds=rng.randint(0, 86399))

        if status in ("active", "suspended"):
            created = ref - age
            expires, periods = boundary_after(created, term, ref)
        else:
            if status == "prolong_wait":
                expires = ref - timedelta(seconds=rng.randint(60, 5 * 86400 - 60))
            elif status == "deleted":
                # Удалена через 5 дней ожидания оплаты; удаления разбросаны по последним двум месяцам.
                expires = ref - timedelta(days=5) - timedelta(seconds=rng.randint(3600, 60 * 86400))
            else:
                expires = ref - timedelta(days=rng.uniform(0, 90))
            periods = self._periods_for(term, age)
            created = add_terms(expires, term, -periods)
            if status == "prolong_wait":
                auto = auto and rng.random() < 0.5

        sid = self.next_service
        self.next_service += 1
        prefix = rng.choice(PREFIXES)
        self.services.append(
            {
                "id": sid,
                "name": f"{prefix}-{sid:05d}",
                "ownerId": owner_id,
                "paymentTerm": term,
                "autoProlong": auto,
                "createdAt": iso(created),
                "expiresAt": iso(expires),
                "status": status,
                "price": price,
            }
        )

        paid = self.tx(sid, price, "order", created, True)
        for start in period_starts(created, term, periods, MAX_PROLONG_HISTORY):
            if rng.random() < seg.failed_prolong:
                # Неудачная попытка списания, затем оплата в течение пары дней.
                self.tx(sid, price, "prolong", start, False)
                start = start + timedelta(hours=rng.randint(2, 60))
            paid += self.tx(sid, price, "prolong", start, True)
        if status in ("prolong_wait", "deleted") and auto:
            self.tx(sid, price, "prolong", expires, False)
        live = status in ("active", "suspended", "prolong_wait")
        return paid, (monthly_of(price, term) if live else 0.0)

    def _periods_for(self, term: str, age: timedelta) -> int:
        if term == "hour":
            return max(1, min(int(age.total_seconds() // 3600), 24 * 400))
        if term == "week":
            return max(1, int(age.days // 7))
        months = {"month": 1, "quarter_year": 3, "half_year": 6, "year": 12}[term]
        return max(1, int(age.days // (30 * months)))


def generate(accounts_count: int, seed: int, reference: datetime) -> dict[str, Any]:
    """Детерминированный набор: одинаковые (accounts_count, seed, reference) дают одинаковый результат."""
    if accounts_count < 1:
        raise ValueError("accounts_count должен быть ≥ 1")
    rng = random.Random(seed)
    builder = _Builder(rng, reference)
    width = max(4, len(str(accounts_count)))
    accounts: list[dict[str, Any]] = []
    segments = list(SEGMENTS)
    shares = [s.share for s in segments]

    for account_id in range(1, accounts_count + 1):
        seg = rng.choices(segments, weights=shares)[0]
        n_services = rng.randint(*seg.services)
        paid_total = 0
        monthly_live = 0.0
        for _ in range(n_services):
            paid, monthly = builder.service(account_id, seg)
            paid_total += paid
            monthly_live += monthly

        if rng.random() < seg.zero_balance:
            balance = 0
        else:
            reference_month = monthly_live or rng.choice(MONTHLY_TARIFFS)
            balance = int(reference_month * rng.uniform(*seg.balance_months)) + rng.randint(0, 500)
        withdraw = rng.randint(0, balance // 2) if balance and rng.random() < 0.15 else 0
        if rng.random() < 0.4:
            bonus = rng.randint(100, 3000)
            if seg.name != "loyal" and rng.random() < 0.3:
                bonus += balance + rng.randint(500, 3000)
        else:
            bonus = 0
        accounts.append(
            {
                "id": account_id,
                "email": f"user-{account_id:0{width}d}@example.test",
                "balance": balance,
                "withdrawBalance": withdraw,
                "totalReplenished": balance + paid_total + rng.randint(0, 5000),
                "bonusBalance": bonus,
                "bonusState": "unlocked" if rng.random() < seg.bonus_unlocked else "locked",
            }
        )

    return {
        "reference_time": iso(reference),
        "accounts": accounts,
        "services": builder.services,
        "transactions": builder.transactions,
    }
