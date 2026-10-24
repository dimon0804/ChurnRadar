"""Живой поток правдоподобных изменений исходных данных.

Состояние держим в памяти (последний сгенерированный набор или текущие данные из БД),
каждое изменение превращаем в пакет SourceChangeBatch с полными записями (upsert).
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .generator import MONTHLY_TARIFFS, PREFIXES, TARIFF_WEIGHTS, price_for
from .timeutil import add_terms, iso, parse_iso

LIVE = ("active", "suspended", "prolong_wait")

# Вес каждого вида изменения в потоке.
MUTATION_WEIGHTS: dict[str, int] = {
    "topup": 22,
    "renewal": 20,
    "autoprolong_off": 10,
    "autoprolong_on": 5,
    "expire": 10,
    "pay_wait": 8,
    "suspend": 6,
    "resume": 5,
    "new_order": 10,
    "new_account": 4,
}


@dataclass(slots=True)
class Batch:
    accounts: dict[int, dict[str, Any]] = field(default_factory=dict)
    services: dict[int, dict[str, Any]] = field(default_factory=dict)
    transactions: list[dict[str, Any]] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.accounts or self.services or self.transactions)

    def to_payload(self) -> dict[str, Any]:
        """Порядок важен для ссылочной целостности на стороне ingest: аккаунты → услуги → транзакции."""
        return {
            "accounts": [dict(a) for a in self.accounts.values()],
            "services": [dict(s) for s in self.services.values()],
            "transactions": list(self.transactions),
        }


class World:
    """Модель источника данных, которую изменяет поток."""

    def __init__(
        self,
        rng: random.Random | None = None,
        *,
        clock: Callable[[], datetime],
        demo_day_seconds: float = 24.0,
    ) -> None:
        self.rng = rng or random.Random()
        self.clock = clock
        self.demo_day_seconds = demo_day_seconds
        self.accounts: dict[int, dict[str, Any]] = {}
        self.services: dict[int, dict[str, Any]] = {}
        self.service_ids: list[int] = []
        self.account_ids: list[int] = []
        self.deadlines: dict[int, datetime] = {}
        self.next_account = 1
        self.next_service = 1
        self.next_tx = 1

    # --- загрузка -----------------------------------------------------------------------------

    def load(self, accounts: list[dict[str, Any]], services: list[dict[str, Any]], max_tx_id: int) -> None:
        now = self.clock()
        self.accounts = {int(a["id"]): dict(a) for a in accounts}
        self.services = {int(s["id"]): dict(s) for s in services}
        self.account_ids = list(self.accounts)
        self.service_ids = list(self.services)
        self.next_account = max(self.accounts, default=0) + 1
        self.next_service = max(self.services, default=0) + 1
        self.next_tx = max_tx_id + 1
        self.deadlines = {}
        for sid, service in self.services.items():
            if service["status"] == "prolong_wait":
                # Уже ждущим оплаты даём случайный остаток из 5 демо-дней.
                left = self.rng.uniform(0.2, 5.0) * self.demo_day_seconds
                self.deadlines[sid] = now + timedelta(seconds=left)

    def unload(self) -> None:
        """Сбросить мир: следующий шаг перечитает состояние из БД (или дождётся генерации)."""
        self.accounts = {}
        self.services = {}
        self.account_ids = []
        self.service_ids = []
        self.deadlines = {}

    @property
    def loaded(self) -> bool:
        return bool(self.accounts)

    # --- шаг ----------------------------------------------------------------------------------

    def tick(self, max_changes: int = 5) -> Batch:
        batch = Batch()
        self._expire_waiting(batch)
        if not self.loaded:
            return batch
        kinds = list(MUTATION_WEIGHTS)
        weights = [MUTATION_WEIGHTS[k] for k in kinds]
        for _ in range(self.rng.randint(1, max_changes)):
            kind = self.rng.choices(kinds, weights=weights)[0]
            if getattr(self, f"_m_{kind}")(batch):
                batch.kinds.append(kind)
        return batch

    def _expire_waiting(self, batch: Batch) -> None:
        now = self.clock()
        due = [sid for sid, deadline in self.deadlines.items() if deadline <= now]
        for sid in due:
            self.deadlines.pop(sid, None)
            service = self.services.get(sid)
            if service is None or service["status"] != "prolong_wait":
                continue
            service["status"] = "deleted"
            self._touch_service(batch, service)
            batch.kinds.append("delete")

    # --- выбор --------------------------------------------------------------------------------

    def _random_service(self, predicate: Callable[[dict[str, Any]], bool], tries: int = 40) -> dict[str, Any] | None:
        if not self.service_ids:
            return None
        for _ in range(tries):
            service = self.services[self.rng.choice(self.service_ids)]
            if predicate(service):
                return service
        return None

    def _random_account(self) -> dict[str, Any] | None:
        if not self.account_ids:
            return None
        return self.accounts[self.rng.choice(self.account_ids)]

    # --- изменения ----------------------------------------------------------------------------

    def _m_topup(self, batch: Batch) -> bool:
        account = self._random_account()
        if account is None:
            return False
        amount = self.rng.choice((500, 1000, 2000, 2500, 5000, 10000))
        account["balance"] += amount
        account["totalReplenished"] += amount
        self._touch_account(batch, account)
        return True

    def _m_renewal(self, batch: Batch) -> bool:
        service = self._random_service(lambda s: s["status"] in ("active", "suspended") and s["autoProlong"])
        if service is None:
            return False
        account = self.accounts[service["ownerId"]]
        now = self.clock()
        price = int(service["price"])
        if self._charge(account, price):
            expires = parse_iso(service["expiresAt"])
            expires = add_terms(max(expires, now), service["paymentTerm"])
            service["expiresAt"] = iso(expires)
            self._add_tx(batch, service, "prolong", performed=True)
            self._touch_account(batch, account)
        else:
            # Денег не хватило: списание не прошло, услуга ждёт оплаты.
            self._add_tx(batch, service, "prolong", performed=False)
            self._to_wait(service, now)
        self._touch_service(batch, service)
        return True

    def _m_autoprolong_off(self, batch: Batch) -> bool:
        service = self._random_service(lambda s: s["status"] in LIVE and s["autoProlong"])
        if service is None:
            return False
        service["autoProlong"] = False
        self._touch_service(batch, service)
        return True

    def _m_autoprolong_on(self, batch: Batch) -> bool:
        service = self._random_service(lambda s: s["status"] in LIVE and not s["autoProlong"])
        if service is None:
            return False
        service["autoProlong"] = True
        self._touch_service(batch, service)
        return True

    def _m_expire(self, batch: Batch) -> bool:
        # Срок истёк, автопродления нет — услуга переходит в «Ожидает оплаты».
        candidates = [
            self._random_service(lambda s: s["status"] in ("active", "suspended") and not s["autoProlong"])
            for _ in range(5)
        ]
        candidates = [c for c in candidates if c is not None]
        if not candidates:
            return False
        service = min(candidates, key=lambda s: s["expiresAt"])
        now = self.clock()
        service["expiresAt"] = iso(now)
        self._to_wait(service, now)
        self._touch_service(batch, service)
        return True

    def _m_pay_wait(self, batch: Batch) -> bool:
        service = self._random_service(lambda s: s["status"] == "prolong_wait", tries=80)
        if service is None:
            return False
        account = self.accounts[service["ownerId"]]
        price = int(service["price"])
        account["balance"] += price
        account["totalReplenished"] += price
        self._charge(account, price)
        now = self.clock()
        service["status"] = "active"
        service["expiresAt"] = iso(add_terms(now, service["paymentTerm"]))
        self.deadlines.pop(int(service["id"]), None)
        self._add_tx(batch, service, "prolong", performed=True)
        self._touch_account(batch, account)
        self._touch_service(batch, service)
        return True

    def _m_suspend(self, batch: Batch) -> bool:
        service = self._random_service(lambda s: s["status"] == "active")
        if service is None:
            return False
        service["status"] = "suspended"
        self._touch_service(batch, service)
        return True

    def _m_resume(self, batch: Batch) -> bool:
        service = self._random_service(lambda s: s["status"] == "suspended", tries=80)
        if service is None:
            return False
        service["status"] = "active"
        self._touch_service(batch, service)
        return True

    def _m_new_order(self, batch: Batch, account: dict[str, Any] | None = None) -> bool:
        account = account or self._random_account()
        if account is None:
            return False
        rng = self.rng
        term = rng.choices(("hour", "week", "month", "quarter_year", "half_year", "year"), weights=(5, 8, 55, 12, 8, 12))[0]
        monthly = rng.choices(MONTHLY_TARIFFS, weights=TARIFF_WEIGHTS)[0]
        price = price_for(monthly, term)
        now = self.clock()
        sid = self.next_service
        self.next_service += 1
        service = {
            "id": sid,
            "name": f"{rng.choice(PREFIXES)}-{sid:05d}",
            "ownerId": int(account["id"]),
            "paymentTerm": term,
            "autoProlong": rng.random() < 0.6,
            "createdAt": iso(now),
            "expiresAt": iso(add_terms(now, term)),
            "status": "active",
            "price": price,
        }
        self.services[sid] = service
        self.service_ids.append(sid)
        if not self._charge(account, price):
            account["balance"] += price
            account["totalReplenished"] += price
            self._charge(account, price)
        self._touch_account(batch, account)
        self._touch_service(batch, service)
        self._add_tx(batch, service, "order", performed=True)
        return True

    def _m_new_account(self, batch: Batch) -> bool:
        aid = self.next_account
        self.next_account += 1
        topup = self.rng.choice((1000, 2000, 5000, 10000))
        account = {
            "id": aid,
            "email": f"new-{aid:06d}@example.test",
            "balance": topup,
            "withdrawBalance": 0,
            "totalReplenished": topup,
            "bonusBalance": self.rng.choice((0, 0, 500, 1000)),
            "bonusState": self.rng.choice(("locked", "unlocked")),
        }
        self.accounts[aid] = account
        self.account_ids.append(aid)
        return self._m_new_order(batch, account)

    # --- вспомогательное ----------------------------------------------------------------------

    def _charge(self, account: dict[str, Any], amount: int) -> bool:
        """Списание: сначала основной баланс, затем бонусы, если они разблокированы."""
        bonus = account["bonusBalance"] if account["bonusState"] == "unlocked" else 0
        if account["balance"] + bonus < amount:
            return False
        from_balance = min(account["balance"], amount)
        account["balance"] -= from_balance
        account["bonusBalance"] -= amount - from_balance
        account["withdrawBalance"] = min(account["withdrawBalance"], account["balance"])
        return True

    def _to_wait(self, service: dict[str, Any], now: datetime) -> None:
        # В ожидание оплаты услуга попадает только с истёкшим сроком: продление,
        # которое не прошло досрочно, сдвигает срок на текущий момент.
        if parse_iso(service["expiresAt"]) > now:
            service["expiresAt"] = iso(now)
        service["status"] = "prolong_wait"
        self.deadlines[int(service["id"])] = now + timedelta(seconds=5 * self.demo_day_seconds)

    def _add_tx(self, batch: Batch, service: dict[str, Any], tx_type: str, *, performed: bool) -> None:
        now = self.clock()
        batch.transactions.append(
            {
                "id": self.next_tx,
                "amount": int(service["price"]),
                "status": "performed" if performed else "not_performed",
                "performedAt": iso(now) if performed else None,
                "createdAt": iso(now),
                "type": tx_type,
                "serviceId": int(service["id"]),
            }
        )
        self.next_tx += 1

    def _touch_account(self, batch: Batch, account: dict[str, Any]) -> None:
        batch.accounts[int(account["id"])] = account

    def _touch_service(self, batch: Batch, service: dict[str, Any]) -> None:
        batch.services[int(service["id"])] = service
