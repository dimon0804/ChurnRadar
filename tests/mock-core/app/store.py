"""Исходные данные в памяти: аккаунты, услуги, транзакции и индексы по ним."""

import json
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from .schemas import SourceChangeBatch, SourceSnapshot

LIVE_EXCLUDED = "deleted"


@dataclass(slots=True)
class Account:
    id: int
    email: str
    balance: int
    withdraw_balance: int
    total_replenished: int
    bonus_balance: int
    bonus_state: str


@dataclass(slots=True)
class Service:
    id: int
    name: str
    owner_id: int
    payment_term: str
    auto_prolong: bool
    created_at: datetime
    expires_at: datetime
    status: str
    price: int

    @property
    def live(self) -> bool:
        return self.status != LIVE_EXCLUDED


@dataclass(slots=True)
class Transaction:
    id: int
    amount: int
    status: str
    performed_at: Optional[datetime]
    created_at: datetime
    type: str
    service_id: int


class DataIntegrityError(ValueError):
    """Ссылка на несуществующий аккаунт или услугу."""

    def __init__(self, message: str, details: list):
        super().__init__(message)
        self.details = details


def _account(a) -> Account:
    return Account(a.id, a.email, a.balance, a.withdrawBalance, a.totalReplenished, a.bonusBalance, a.bonusState)


def _service(s) -> Service:
    return Service(s.id, s.name, s.ownerId, s.paymentTerm, s.autoProlong, s.createdAt, s.expiresAt, s.status, s.price)


def _transaction(t) -> Transaction:
    return Transaction(t.id, t.amount, t.status, t.performedAt, t.createdAt, t.type, t.serviceId)


class DataStore:
    def __init__(self) -> None:
        self.accounts: Dict[int, Account] = {}
        self.services: Dict[int, Service] = {}
        self.transactions: Dict[int, Transaction] = {}
        self.services_by_owner: Dict[int, Set[int]] = {}
        self.tx_by_service: Dict[int, Set[int]] = {}
        self.reference_time: datetime = datetime.now(timezone.utc)
        self.explicit_reference = False
        self.snapshot_id: Optional[str] = None
        self.loaded = False

    # --- загрузка ---

    def load_file(self, path: Path) -> None:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        self.replace_snapshot(SourceSnapshot.model_validate(raw))

    def replace_snapshot(self, snapshot: SourceSnapshot) -> str:
        return self.set_data(
            {a.id: _account(a) for a in snapshot.accounts},
            {s.id: _service(s) for s in snapshot.services},
            {t.id: _transaction(t) for t in snapshot.transactions},
            snapshot.reference_time,
        )

    def set_data(
        self,
        accounts: Dict[int, Account],
        services: Dict[int, Service],
        transactions: Dict[int, Transaction],
        reference_time: Optional[datetime],
    ) -> str:
        errors = []
        for s in services.values():
            if s.owner_id not in accounts:
                errors.append({"loc": ["services", s.id, "ownerId"], "msg": f"нет аккаунта {s.owner_id}"})
        for t in transactions.values():
            if t.service_id not in services:
                errors.append({"loc": ["transactions", t.id, "serviceId"], "msg": f"нет услуги {t.service_id}"})
        if errors:
            raise DataIntegrityError("Снимок ссылается на несуществующие записи", errors[:50])

        self.accounts, self.services, self.transactions = accounts, services, transactions
        self._reindex()
        if reference_time is not None:
            self.reference_time = reference_time
            self.explicit_reference = True
        else:
            self.explicit_reference = False
            self.reference_time = self._max_created_at() or datetime.now(timezone.utc)
        self.snapshot_id = str(uuid.uuid4())
        self.loaded = True
        return self.snapshot_id

    def _max_created_at(self) -> Optional[datetime]:
        stamps = [s.created_at for s in self.services.values()]
        stamps += [t.created_at for t in self.transactions.values()]
        return max(stamps) if stamps else None

    def _reindex(self) -> None:
        self.services_by_owner = {a: set() for a in self.accounts}
        for s in self.services.values():
            self.services_by_owner.setdefault(s.owner_id, set()).add(s.id)
        self.tx_by_service = {}
        for t in self.transactions.values():
            self.tx_by_service.setdefault(t.service_id, set()).add(t.id)

    # --- точечные изменения ---

    def apply_changes(self, batch: SourceChangeBatch) -> Set[int]:
        """Upsert пакета изменений. Возвращает id затронутых аккаунтов."""
        known_accounts = set(self.accounts) | {a.id for a in batch.accounts}
        known_services = set(self.services) | {s.id for s in batch.services}
        errors = []
        for s in batch.services:
            if s.ownerId not in known_accounts:
                errors.append({"loc": ["services", s.id, "ownerId"], "msg": f"нет аккаунта {s.ownerId}"})
        for t in batch.transactions:
            if t.serviceId not in known_services:
                errors.append({"loc": ["transactions", t.id, "serviceId"], "msg": f"нет услуги {t.serviceId}"})
        if errors:
            raise DataIntegrityError("Изменения ссылаются на несуществующие записи", errors[:50])

        touched: Set[int] = set()
        for a in batch.accounts:
            self.accounts[a.id] = _account(a)
            self.services_by_owner.setdefault(a.id, set())
            touched.add(a.id)
        for s in batch.services:
            self.upsert_service(_service(s), touched)
        for t in batch.transactions:
            tx = _transaction(t)
            old = self.transactions.get(tx.id)
            if old is not None:
                self.tx_by_service.get(old.service_id, set()).discard(tx.id)
                touched.add(self.services[old.service_id].owner_id)
            self.transactions[tx.id] = tx
            self.tx_by_service.setdefault(tx.service_id, set()).add(tx.id)
            touched.add(self.services[tx.service_id].owner_id)
        return touched

    def upsert_service(self, service: Service, touched: Set[int]) -> None:
        old = self.services.get(service.id)
        if old is not None and old.owner_id != service.owner_id:
            self.services_by_owner.get(old.owner_id, set()).discard(service.id)
            touched.add(old.owner_id)
        self.services[service.id] = service
        self.services_by_owner.setdefault(service.owner_id, set()).add(service.id)
        touched.add(service.owner_id)

    def update_account(self, account: Account) -> None:
        self.accounts[account.id] = account

    def replace_service_fields(self, service_id: int, **fields) -> Service:
        updated = replace(self.services[service_id], **fields)
        self.services[service_id] = updated
        return updated

    # --- выборки ---

    def account_services(self, account_id: int) -> List[Service]:
        ids = self.services_by_owner.get(account_id, ())
        return sorted((self.services[i] for i in ids), key=lambda s: s.id)

    def account_transactions(self, account_id: int) -> List[Transaction]:
        result: List[Transaction] = []
        for sid in self.services_by_owner.get(account_id, ()):
            result.extend(self.transactions[t] for t in self.tx_by_service.get(sid, ()))
        result.sort(key=lambda t: (t.created_at, t.id), reverse=True)
        return result

    def counts(self) -> dict:
        return {
            "accounts": len(self.accounts),
            "services": len(self.services),
            "transactions": len(self.transactions),
        }

    def account_ids(self) -> Iterable[int]:
        return self.accounts.keys()
