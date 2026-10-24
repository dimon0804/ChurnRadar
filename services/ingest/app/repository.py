"""Запись исходных данных в схему source."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import asyncpg
from svckit.errors import ApiError
from svckit.outbox import add_event

from app.schemas import (
    SourceAccount,
    SourceChangeBatch,
    SourceService,
    SourceSnapshot,
    SourceTransaction,
    check_batch_consistency,
)

# Все записи в source.* сериализуются этой блокировкой (снимок и поток изменений).
SOURCE_LOCK_KEY = 7_340_211_002
# Событие для scoring пишется в outbox той же транзакцией, что и данные; отправляет его relay.
OUTBOX_TABLE = "source.outbox"
SUBJECT_SOURCE_CHANGED = "churn.source.changed"
# По регламенту услуга в prolong_wait удаляется через 5 дней.
DELETE_AFTER_PROLONG_WAIT = timedelta(days=5)

ACCOUNT_COLS = ("id", "email", "balance", "withdraw_balance", "total_replenished", "bonus_balance", "bonus_state")
SERVICE_COLS = ("id", "name", "owner_id", "payment_term", "auto_prolong", "created_at", "expires_at", "status", "price")
TX_COLS = ("id", "service_id", "amount", "status", "type", "created_at", "performed_at")
HISTORY_COLS = ("service_id", "owner_id", "old_status", "new_status", "changed_at", "estimated")

# Построчная проверка внешних ключей при COPY в разы медленнее самой вставки
# (200k транзакций: ~6 с против ~0.6 с). При полной замене снимка ключи снимаются
# и возвращаются одной проверкой в той же транзакции: целостность сохраняется.
_DROP_FKS = """
ALTER TABLE source.transactions DROP CONSTRAINT IF EXISTS transactions_service_id_fkey;
ALTER TABLE source.services DROP CONSTRAINT IF EXISTS services_owner_id_fkey;
"""
_ADD_FKS = """
ALTER TABLE source.services ADD CONSTRAINT services_owner_id_fkey
    FOREIGN KEY (owner_id) REFERENCES source.accounts (id) ON DELETE CASCADE;
ALTER TABLE source.transactions ADD CONSTRAINT transactions_service_id_fkey
    FOREIGN KEY (service_id) REFERENCES source.services (id) ON DELETE CASCADE;
"""


@dataclass(slots=True)
class WriteResult:
    batch_id: str
    accounts: int
    services: int
    transactions: int
    account_ids: list[int] = field(default_factory=list)
    status_changes: int = 0

    @property
    def counts(self) -> dict[str, int]:
        return {"accounts": self.accounts, "services": self.services, "transactions": self.transactions}


def source_changed_payload(kind: str, result: WriteResult) -> dict:
    """payload churn.source.changed (contracts/events/source.changed.schema.json)."""
    return {
        "kind": kind,
        "snapshot_id": result.batch_id,
        "counts": result.counts,
        "account_ids": [] if kind == "snapshot" else result.account_ids,
    }


async def _enqueue_source_changed(conn: asyncpg.Connection, kind: str, result: WriteResult) -> None:
    await add_event(conn, OUTBOX_TABLE, SUBJECT_SOURCE_CHANGED, "source.changed", source_changed_payload(kind, result))


def _account_row(a: SourceAccount) -> tuple:
    return (a.id, a.email, a.balance, a.withdraw_balance, a.total_replenished, a.bonus_balance, a.bonus_state)


def _service_row(s: SourceService) -> tuple:
    return (s.id, s.name, s.owner_id, s.payment_term, s.auto_prolong, s.created_at, s.expires_at, s.status, s.price)


def _tx_row(t: SourceTransaction) -> tuple:
    return (t.id, t.service_id, t.amount, t.status, t.type, t.created_at, t.performed_at)


def snapshot_history_rows(
    prior: dict[int, tuple[int, str]],
    services: list[SourceService],
    as_of: datetime,
) -> list[tuple]:
    """Записи status_history при полной замене снимка.

    prior — {service_id: (owner_id, status)} до замены.
    - статус известной услуги изменился → точная запись на момент as_of;
    - услуга впервые видна уже удалённой → оценочная запись: удаление через 5 дней
      после expires_at (prolong_wait → deleted), если эта дата не позже as_of.
    """
    rows: list[tuple] = []
    for s in services:
        before = prior.get(s.id)
        if before is not None:
            if before[1] != s.status:
                rows.append((s.id, s.owner_id, before[1], s.status, as_of, False))
            continue
        if s.status == "deleted":
            deleted_at = s.expires_at + DELETE_AFTER_PROLONG_WAIT
            if deleted_at <= as_of:
                rows.append((s.id, s.owner_id, "prolong_wait", "deleted", deleted_at, True))
    return rows


async def _clock_time(conn: asyncpg.Connection) -> datetime:
    ref = await conn.fetchval("SELECT reference_time FROM source.clock WHERE id = 1")
    return ref or datetime.now(UTC)


async def replace_snapshot(
    pool: asyncpg.Pool,
    snap: SourceSnapshot,
    *,
    kind: str = "snapshot",
    created_by: str | None,
    only_if_empty: bool = False,
    reset_history: bool = False,
) -> WriteResult | None:
    """Атомарная замена source.*. При only_if_empty ничего не делает, если данные уже есть.

    reset_history — снимок описывает другой набор клиентов (например, сгенерированный
    заново), и прошлая история статусов к нему не относится.
    """
    batch_id = f"snap_{uuid.uuid4().hex[:16]}"
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", SOURCE_LOCK_KEY)
        if only_if_empty and await conn.fetchval("SELECT EXISTS (SELECT 1 FROM source.accounts)"):
            return None

        if reset_history:
            await conn.execute("TRUNCATE source.status_history")
            prior = {}
        else:
            prior = {
                r["id"]: (r["owner_id"], r["status"])
                for r in await conn.fetch("SELECT id, owner_id, status FROM source.services")
            }
        as_of = snap.reference_time or datetime.now(UTC)

        await conn.execute("TRUNCATE source.transactions, source.services, source.accounts")
        await conn.execute(_DROP_FKS)
        # строки для COPY отдаём генераторами: второй копии снимка в памяти не будет
        if snap.accounts:
            await conn.copy_records_to_table(
                "accounts", schema_name="source", columns=ACCOUNT_COLS,
                records=(_account_row(a) for a in snap.accounts),
            )
        if snap.services:
            await conn.copy_records_to_table(
                "services", schema_name="source", columns=SERVICE_COLS,
                records=(_service_row(s) for s in snap.services),
            )
        if snap.transactions:
            await conn.copy_records_to_table(
                "transactions", schema_name="source", columns=TX_COLS,
                records=(_tx_row(t) for t in snap.transactions),
            )
        await conn.execute(_ADD_FKS)

        history = snapshot_history_rows(prior, snap.services, as_of)
        if history:
            await conn.copy_records_to_table(
                "status_history", schema_name="source", columns=HISTORY_COLS, records=history
            )

        await conn.execute(
            """
            INSERT INTO source.snapshots (id, kind, reference_time, accounts, services, transactions, created_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            batch_id, kind, snap.reference_time,
            len(snap.accounts), len(snap.services), len(snap.transactions), created_by,
        )
        await conn.execute(
            "UPDATE source.clock SET reference_time = $1, updated_at = now() WHERE id = 1",
            snap.reference_time,
        )
        result = WriteResult(
            batch_id=batch_id,
            accounts=len(snap.accounts),
            services=len(snap.services),
            transactions=len(snap.transactions),
            status_changes=len(history),
        )
        # сид — тоже полный снимок для scoring
        await _enqueue_source_changed(conn, "snapshot", result)
    return result


async def apply_changes(pool: asyncpg.Pool, batch: SourceChangeBatch, *, created_by: str | None) -> WriteResult:
    """Upsert пакета изменений с записью смен статусов в status_history."""
    batch_id = f"chg_{uuid.uuid4().hex[:16]}"
    service_ids = list({s.id for s in batch.services} | {t.service_id for t in batch.transactions})
    owner_ids = list({a.id for a in batch.accounts} | {s.owner_id for s in batch.services})

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", SOURCE_LOCK_KEY)

        known_accounts = {
            r["id"] for r in await conn.fetch("SELECT id FROM source.accounts WHERE id = ANY($1::bigint[])", owner_ids)
        }
        prior_services = {
            r["id"]: (r["owner_id"], r["status"])
            for r in await conn.fetch(
                "SELECT id, owner_id, status FROM source.services WHERE id = ANY($1::bigint[]) FOR UPDATE",
                service_ids,
            )
        }
        errors = check_batch_consistency(
            batch.accounts, batch.services, batch.transactions,
            known_account_ids=known_accounts, known_service_ids=set(prior_services),
        )
        if errors:
            raise ApiError(422, "validation_error", "Пакет изменений не прошёл проверку целостности", errors)

        changed_at = await _clock_time(conn)
        try:
            if batch.accounts:
                await conn.executemany(
                    """
                    INSERT INTO source.accounts (id, email, balance, withdraw_balance, total_replenished, bonus_balance, bonus_state)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (id) DO UPDATE SET
                        email = EXCLUDED.email, balance = EXCLUDED.balance,
                        withdraw_balance = EXCLUDED.withdraw_balance,
                        total_replenished = EXCLUDED.total_replenished,
                        bonus_balance = EXCLUDED.bonus_balance, bonus_state = EXCLUDED.bonus_state,
                        updated_at = now()
                    """,
                    [_account_row(a) for a in batch.accounts],
                )
            if batch.services:
                await conn.executemany(
                    """
                    INSERT INTO source.services (id, name, owner_id, payment_term, auto_prolong, created_at, expires_at, status, price)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name, owner_id = EXCLUDED.owner_id,
                        payment_term = EXCLUDED.payment_term, auto_prolong = EXCLUDED.auto_prolong,
                        created_at = EXCLUDED.created_at, expires_at = EXCLUDED.expires_at,
                        status = EXCLUDED.status, price = EXCLUDED.price, updated_at = now()
                    """,
                    [_service_row(s) for s in batch.services],
                )
            if batch.transactions:
                await conn.executemany(
                    """
                    INSERT INTO source.transactions (id, service_id, amount, status, type, created_at, performed_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (id) DO UPDATE SET
                        service_id = EXCLUDED.service_id, amount = EXCLUDED.amount,
                        status = EXCLUDED.status, type = EXCLUDED.type,
                        created_at = EXCLUDED.created_at, performed_at = EXCLUDED.performed_at
                    """,
                    [_tx_row(t) for t in batch.transactions],
                )
        except asyncpg.UniqueViolationError as exc:
            raise ApiError(
                422, "validation_error", "Email уже занят другим аккаунтом",
                [{"field": "accounts.email", "message": exc.detail or str(exc), "type": "duplicate"}],
            ) from exc
        except asyncpg.ForeignKeyViolationError as exc:
            raise ApiError(
                422, "validation_error", "Нарушена ссылочная целостность",
                [{"field": None, "message": exc.detail or str(exc), "type": "broken_reference"}],
            ) from exc

        history: list[tuple] = []
        affected: set[int] = {a.id for a in batch.accounts}
        for s in batch.services:
            affected.add(s.owner_id)
            before = prior_services.get(s.id)
            if before is None:
                history.append((s.id, s.owner_id, None, s.status, changed_at, False))
                continue
            affected.add(before[0])
            if before[1] != s.status:
                history.append((s.id, s.owner_id, before[1], s.status, changed_at, False))
        if history:
            await conn.copy_records_to_table(
                "status_history", schema_name="source", columns=HISTORY_COLS, records=history
            )

        if batch.transactions:
            rows = await conn.fetch(
                "SELECT DISTINCT owner_id FROM source.services WHERE id = ANY($1::bigint[])",
                list({t.service_id for t in batch.transactions}),
            )
            affected.update(r["owner_id"] for r in rows)

        await conn.execute(
            """
            INSERT INTO source.snapshots (id, kind, reference_time, accounts, services, transactions, created_by)
            VALUES ($1, 'changes', NULL, $2, $3, $4, $5)
            """,
            batch_id, len(batch.accounts), len(batch.services), len(batch.transactions), created_by,
        )
        result = WriteResult(
            batch_id=batch_id,
            accounts=len(batch.accounts),
            services=len(batch.services),
            transactions=len(batch.transactions),
            account_ids=sorted(affected),
            status_changes=len(history),
        )
        await _enqueue_source_changed(conn, "changes", result)
    return result
