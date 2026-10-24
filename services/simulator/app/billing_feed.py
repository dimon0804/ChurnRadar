"""Демо-API биллинга: мир симулятора с отметками updatedAt для коннектора ingest (режим pull).

Контракт — contracts/billing-source.openapi.yaml: GET /accounts|/services|/transactions с
updated_since (включительно), cursor и limit; записи упорядочены по (updatedAt, id).

Состояние аккаунтов и услуг берём прямо из мира (он меняет словари на месте), а здесь храним
только время последнего изменения каждой записи. Транзакции мир не хранит, поэтому их копии
лежат тут же, последние MAX_TRANSACTIONS штук.
"""

from __future__ import annotations

import base64
from collections import OrderedDict
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Any

from .stream import Batch, World
from .timeutil import iso, parse_iso

ENTITIES = ("accounts", "services", "transactions")
MAX_TRANSACTIONS = 100_000


class BadCursor(ValueError):
    pass


def encode_cursor(updated_at: str, record_id: int) -> str:
    return base64.urlsafe_b64encode(f"{updated_at}|{record_id}".encode()).decode().rstrip("=")


def decode_cursor(raw: str) -> tuple[str, int]:
    try:
        padded = raw + "=" * (-len(raw) % 4)
        stamp, record_id = base64.urlsafe_b64decode(padded.encode()).decode().split("|", 1)
        parse_iso(stamp)
        return stamp, int(record_id)
    except (ValueError, UnicodeDecodeError) as exc:
        raise BadCursor("cursor не распознан: передайте next_cursor из прошлого ответа как есть") from exc


class BillingFeed:
    def __init__(self, world: World, clock: Callable[[], datetime]) -> None:
        self.world = world
        self.clock = clock
        self.updated: dict[str, dict[int, str]] = {"accounts": {}, "services": {}}
        self.transactions: OrderedDict[int, tuple[str, dict[str, Any]]] = OrderedDict()
        self._last: datetime | None = None

    def _stamp(self) -> str:
        """Время изменения строго растёт: две пачки в одну миллисекунду не получат одинаковую отметку."""
        now = self.clock()
        if self._last is not None and now <= self._last:
            now = self._last + timedelta(milliseconds=1)
        self._last = now
        return iso(now)

    def reset(self, transactions: Iterable[dict[str, Any]] = ()) -> None:
        """Мир загружен заново: все записи считаются изменёнными сейчас."""
        stamp = self._stamp()
        self.updated = {
            "accounts": dict.fromkeys(self.world.accounts, stamp),
            "services": dict.fromkeys(self.world.services, stamp),
        }
        self.transactions.clear()
        for tx in transactions:
            self._add_tx(stamp, tx)

    def record(self, batch: Batch) -> None:
        stamp = self._stamp()
        for account_id in batch.accounts:
            self.updated["accounts"][account_id] = stamp
        for service_id in batch.services:
            self.updated["services"][service_id] = stamp
        for tx in batch.transactions:
            self._add_tx(stamp, tx)

    def _add_tx(self, stamp: str, tx: dict[str, Any]) -> None:
        tx_id = int(tx["id"])
        self.transactions.pop(tx_id, None)
        self.transactions[tx_id] = (stamp, dict(tx))
        while len(self.transactions) > MAX_TRANSACTIONS:
            self.transactions.popitem(last=False)

    def _rows(self, entity: str) -> list[tuple[str, int, dict[str, Any]]]:
        if entity == "transactions":
            return [(stamp, tx_id, tx) for tx_id, (stamp, tx) in self.transactions.items()]
        source = self.world.accounts if entity == "accounts" else self.world.services
        return [(stamp, rid, source[rid]) for rid, stamp in self.updated[entity].items() if rid in source]

    def page(
        self, entity: str, *, updated_since: datetime | None, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        rows = self._rows(entity)
        if updated_since is not None:
            since = iso(updated_since)
            rows = [r for r in rows if r[0] >= since]
        rows.sort(key=lambda r: (r[0], r[1]))
        if cursor:
            after = decode_cursor(cursor)
            rows = [r for r in rows if (r[0], r[1]) > after]
        chunk = rows[:limit]
        items = [{**record, "updatedAt": stamp} for stamp, _, record in chunk]
        next_cursor = encode_cursor(chunk[-1][0], chunk[-1][1]) if len(rows) > limit else None
        return {"items": items, "next_cursor": next_cursor}

    def counts(self) -> dict[str, int]:
        return {
            "accounts": len(self.updated["accounts"]),
            "services": len(self.updated["services"]),
            "transactions": len(self.transactions),
        }
