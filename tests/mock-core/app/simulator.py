"""Демонстрационный поток изменений и генератор расширенного набора данных."""

import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from .features import TERM_DAYS
from .store import Account, DataStore, Service, Transaction

LIVE_STATUSES = ["active", "active", "active", "suspended", "prolong_wait", "blocked"]
TERMS = list(TERM_DAYS)


def mutate(store: DataStore, rng: random.Random) -> Tuple[int, Dict[str, int]]:
    """Меняет одну случайную запись. Возвращает id аккаунта и что именно изменилось."""
    account_id = rng.choice(list(store.accounts))
    account = store.accounts[account_id]
    services = store.account_services(account_id)
    now = store.reference_time

    if not services or rng.random() < 0.25:
        delta = rng.randint(-account.balance // 2 if account.balance else 0, 20000)
        balance = max(0, account.balance + delta)
        store.update_account(
            replace(
                account,
                balance=balance,
                withdraw_balance=min(account.withdraw_balance, balance),
                total_replenished=account.total_replenished + max(0, delta),
            )
        )
        return account_id, {"accounts": 1, "services": 0, "transactions": 0}

    service = rng.choice(services)
    if not service.live:
        # изредка клиент возвращается и заказывает услугу заново
        if rng.random() < 0.3:
            store.replace_service_fields(
                service.id,
                status="active",
                auto_prolong=rng.random() < 0.5,
                expires_at=now + timedelta(days=TERM_DAYS[service.payment_term]),
            )
        return account_id, {"accounts": 0, "services": 1, "transactions": 0}

    roll = rng.random()
    if roll < 0.3:
        store.replace_service_fields(service.id, auto_prolong=not service.auto_prolong)
    elif roll < 0.6:
        store.replace_service_fields(service.id, expires_at=now + timedelta(hours=rng.randint(-72, 45 * 24)))
    elif roll < 0.95:
        store.replace_service_fields(service.id, status=rng.choice(LIVE_STATUSES))
    else:
        store.replace_service_fields(service.id, status="deleted")
    return account_id, {"accounts": 0, "services": 1, "transactions": 0}


def generate(accounts: int, seed: Optional[int], reference_time: Optional[datetime] = None) -> DataStore:
    rng = random.Random(seed)
    now = reference_time or datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)
    acc: Dict[int, Account] = {}
    srv: Dict[int, Service] = {}
    txs: Dict[int, Transaction] = {}
    service_id = 0
    tx_id = 0
    for aid in range(1, accounts + 1):
        replenished = rng.randint(1000, 500000)
        balance = rng.randint(0, replenished)
        acc[aid] = Account(
            id=aid,
            email=f"user-{aid:06d}@example.test",
            balance=balance,
            withdraw_balance=rng.randint(0, balance),
            total_replenished=replenished,
            bonus_balance=rng.randint(0, 10000),
            bonus_state=rng.choice(["locked", "unlocked"]),
        )
        for _ in range(rng.randint(1, 5)):
            service_id += 1
            term = rng.choice(TERMS)
            status = rng.choices(
                ["active", "suspended", "prolong_wait", "deleted", "blocked"], weights=[60, 8, 10, 15, 7]
            )[0]
            created = now - timedelta(days=rng.uniform(0, 400))
            expires = created + timedelta(days=TERM_DAYS[term])
            if status == "active" and expires < now:
                expires = now + timedelta(days=rng.uniform(0.01, TERM_DAYS[term]))
            srv[service_id] = Service(
                id=service_id,
                name=f"service-{service_id:07d}",
                owner_id=aid,
                payment_term=term,
                auto_prolong=rng.random() < 0.55,
                created_at=created,
                expires_at=expires,
                status=status,
                price=rng.randint(50, 5000) if term != "hour" else rng.randint(1, 30),
            )
            for _ in range(rng.randint(0, 4)):
                tx_id += 1
                tx_created = now - timedelta(days=rng.uniform(0, 180))
                ok = rng.random() < 0.8
                txs[tx_id] = Transaction(
                    id=tx_id,
                    amount=rng.randint(0, 1000),
                    status="performed" if ok else "not_performed",
                    performed_at=tx_created + timedelta(seconds=rng.randint(1, 120)) if ok else None,
                    created_at=tx_created,
                    type=rng.choice(["prolong", "order"]),
                    service_id=service_id,
                )
    store = DataStore()
    store.set_data(acc, srv, txs, now)
    return store
