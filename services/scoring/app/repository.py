"""Доступ к Postgres: чтение source.*, настройки, запись результатов."""

from __future__ import annotations

import json
from bisect import bisect_right
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from riskcore.types import Account, Service, StatusChange, Transaction
from svckit import db

SCORE_COLUMNS: tuple[str, ...] = (
    "customer_id",
    "email",
    "balance",
    "bonus_balance",
    "bonus_state",
    "withdraw_balance",
    "total_replenished",
    "risk_score",
    "risk_level",
    "loyalty",
    "reasons",
    "services_total",
    "services_at_risk",
    "monthly_revenue",
    "expected_loss",
    "exposure",
    "detail",
    "model_version",
    "scored_at",
)
_JSON_COLUMNS = {"reasons", "detail"}

_UPSERT_SQL = (
    f"INSERT INTO scoring.customer_scores ({', '.join(SCORE_COLUMNS)}) "
    f"SELECT {', '.join(SCORE_COLUMNS)} FROM pg_temp.tmp_scores "
    "ON CONFLICT (customer_id) DO UPDATE SET "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in SCORE_COLUMNS if c != "customer_id")
)


@dataclass(frozen=True, slots=True)
class SourceData:
    accounts: list[Account]
    services: list[Service]
    transactions: list[Transaction]


async def create_pool(
    dsn: str,
    *,
    min_size: int = 1,
    max_size: int = 8,
    statement_timeout_ms: int | None = 600_000,
    idle_in_transaction_timeout_ms: int | None = 600_000,
) -> asyncpg.Pool:
    # jsonb пишем и читаем строками сами, повторы подключения — в main
    return await db.create_pool(
        dsn, min_size, max_size, timeout_s=0, command_timeout=600, json_codec=False,
        statement_timeout_ms=statement_timeout_ms,
        idle_in_transaction_timeout_ms=idle_in_transaction_timeout_ms,
    )


# --- настройки --------------------------------------------------------------------------------


async def ensure_settings(conn: asyncpg.Connection, default_data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Возвращает (version, data); если настроек нет — вставляет значения по умолчанию с version=1."""
    await conn.execute(
        "INSERT INTO scoring.settings (id, version, data, updated_by) VALUES (1, 1, $1::jsonb, 'system') "
        "ON CONFLICT (id) DO NOTHING",
        json.dumps(default_data, ensure_ascii=False),
    )
    row = await conn.fetchrow("SELECT version, data FROM scoring.settings WHERE id = 1")
    data = row["data"]
    if isinstance(data, str):
        data = json.loads(data)
    return int(row["version"]), data


async def ensure_settings_history(conn: asyncpg.Connection, version: int, data: dict[str, Any]) -> None:
    await conn.execute(
        "INSERT INTO scoring.settings_history (version, data, changes, changed_by) "
        "VALUES ($1, $2::jsonb, '[]'::jsonb, 'system') ON CONFLICT (version) DO NOTHING",
        version,
        json.dumps(data, ensure_ascii=False),
    )


# --- время ------------------------------------------------------------------------------------


async def fetch_clock(conn: asyncpg.Connection) -> datetime | None:
    return await conn.fetchval("SELECT reference_time FROM source.clock WHERE id = 1")


async def resolve_as_of(conn: asyncpg.Connection) -> tuple[datetime, bool]:
    """(as_of, frozen): frozen=True, если время зафиксировано снимком."""
    reference = await fetch_clock(conn)
    if reference is None:
        return datetime.now(UTC), False
    return reference.astimezone(UTC), True


# --- исходные данные --------------------------------------------------------------------------

_ACCOUNTS_SQL = (
    "SELECT id, email, balance, withdraw_balance, total_replenished, bonus_balance, bonus_state "
    "FROM source.accounts"
)
_SERVICES_SQL = (
    "SELECT id, name, owner_id, payment_term, auto_prolong, created_at, expires_at, status, price "
    "FROM source.services"
)
_TX_SQL = (
    "SELECT t.id, t.service_id, t.amount, t.status, t.type, t.created_at, t.performed_at "
    "FROM source.transactions t"
)


def _accounts(rows: Iterable[Sequence[Any]]) -> list[Account]:
    return [Account(r[0], r[1], r[2], r[3], r[4], r[5], r[6]) for r in rows]


def _services(rows: Iterable[Sequence[Any]]) -> list[Service]:
    return [Service(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8]) for r in rows]


def _transactions(rows: Iterable[Sequence[Any]]) -> list[Transaction]:
    return [Transaction(r[0], r[1], r[2], r[3], r[4], r[5], r[6]) for r in rows]


async def load_source(conn: asyncpg.Connection, account_ids: Sequence[int] | None = None) -> SourceData:
    if account_ids is None:
        accounts = _accounts(await conn.fetch(_ACCOUNTS_SQL))
        services = _services(await conn.fetch(_SERVICES_SQL))
        transactions = _transactions(await conn.fetch(_TX_SQL))
    else:
        ids = list(account_ids)
        accounts = _accounts(await conn.fetch(_ACCOUNTS_SQL + " WHERE id = ANY($1::bigint[])", ids))
        services = _services(await conn.fetch(_SERVICES_SQL + " WHERE owner_id = ANY($1::bigint[])", ids))
        transactions = _transactions(
            await conn.fetch(
                _TX_SQL + " JOIN source.services s ON s.id = t.service_id WHERE s.owner_id = ANY($1::bigint[])",
                ids,
            )
        )
    return SourceData(accounts, services, transactions)


class _OrderedStream:
    """Серверный курсор, упорядоченный по id владельца; выдаёт строки порциями «до id включительно»."""

    def __init__(self, cursor: Any, key: int, batch: int) -> None:
        self._cursor = cursor
        self._key = key
        self._batch = batch
        self._buffer: list[Any] = []
        self._done = False

    async def take_until(self, last_owner: int) -> list[Any]:
        taken: list[Any] = []
        while True:
            if not self._buffer and not self._done:
                self._buffer = await self._cursor.fetch(self._batch)
                self._done = not self._buffer
            if not self._buffer:
                return taken
            if self._buffer[-1][self._key] <= last_owner:
                taken.extend(self._buffer)
                self._buffer = []
                continue
            split = bisect_right(self._buffer, last_owner, key=lambda r: r[self._key])
            taken.extend(self._buffer[:split])
            self._buffer = self._buffer[split:]
            return taken


async def iter_source_chunks(conn: asyncpg.Connection, chunk_size: int) -> AsyncIterator[SourceData]:
    """Все исходные данные частями по chunk_size аккаунтов за один проход по каждой таблице.

    Должен вызываться внутри транзакции (курсоры), лучше REPEATABLE READ — тогда все части
    из одного снимка.
    """
    accounts = await conn.cursor(_ACCOUNTS_SQL + " ORDER BY id")
    services = _OrderedStream(await conn.cursor(_SERVICES_SQL + " ORDER BY owner_id, id"), 2, 4 * chunk_size)
    transactions = _OrderedStream(
        await conn.cursor(
            _TX_SQL.replace("FROM source.transactions t", ", s.owner_id FROM source.transactions t")
            + " JOIN source.services s ON s.id = t.service_id ORDER BY s.owner_id"
        ),
        7,
        16 * chunk_size,
    )
    while rows := await accounts.fetch(chunk_size):
        last = rows[-1][0]
        yield SourceData(
            _accounts(rows),
            _services(await services.take_until(last)),
            _transactions(await transactions.take_until(last)),
        )


async def load_status_history(conn: asyncpg.Connection, as_of: datetime, days: int = 31) -> list[StatusChange]:
    rows = await conn.fetch(
        "SELECT service_id, owner_id, old_status, new_status, changed_at, estimated "
        "FROM source.status_history WHERE changed_at >= $1 AND changed_at <= $2",
        as_of - timedelta(days=days),
        as_of + timedelta(days=1),
    )
    return [StatusChange(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows]


# --- идемпотентность --------------------------------------------------------------------------


async def processed_event_ids(conn: asyncpg.Connection, event_ids: Sequence[str]) -> set[str]:
    if not event_ids:
        return set()
    rows = await conn.fetch(
        "SELECT event_id::text FROM scoring.processed_events WHERE event_id = ANY($1::uuid[])", list(event_ids)
    )
    return {r[0] for r in rows}


async def mark_processed(conn: asyncpg.Connection, event_ids: Sequence[str]) -> None:
    if not event_ids:
        return
    await conn.execute(
        "INSERT INTO scoring.processed_events (event_id) SELECT unnest($1::uuid[]) ON CONFLICT DO NOTHING",
        list(event_ids),
    )


async def prune_processed(conn: asyncpg.Connection, older_than: timedelta = timedelta(days=2)) -> None:
    await conn.execute(
        "DELETE FROM scoring.processed_events WHERE processed_at < now() - $1::interval", older_than
    )


# --- результаты -------------------------------------------------------------------------------


def score_record(row: dict[str, Any], model_version: int, scored_at: datetime) -> tuple[Any, ...]:
    """Строка для COPY: колонки в порядке SCORE_COLUMNS, jsonb — строкой."""
    values: list[Any] = []
    for column in SCORE_COLUMNS:
        if column == "customer_id":
            value = row.get("customer_id", row.get("id"))
        elif column == "model_version":
            value = model_version
        elif column == "scored_at":
            value = scored_at
        else:
            value = row.get(column)
        if column in _JSON_COLUMNS:
            if value is None:
                value = [] if column == "reasons" else {}
            if not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False, default=_json_default)
        values.append(value)
    return tuple(values)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f"не сериализуется в JSON: {type(value).__name__}")


async def replace_all_scores_begin(conn: asyncpg.Connection) -> None:
    """Начало полной замены (внутри транзакции). DELETE, а не TRUNCATE: читатели не блокируются
    и до коммита видят прежние строки; исчезнувшие клиенты уходят вместе с DELETE."""
    await conn.execute("DELETE FROM scoring.customer_scores")


async def copy_scores(conn: asyncpg.Connection, records: Sequence[tuple[Any, ...]]) -> None:
    if records:
        await conn.copy_records_to_table(
            "customer_scores", schema_name="scoring", records=records, columns=list(SCORE_COLUMNS)
        )


async def save_summary(
    conn: asyncpg.Connection, summary: dict[str, Any], model_version: int, scored_at: datetime
) -> None:
    await conn.execute(
        "INSERT INTO scoring.summary (id, data, model_version, scored_at) VALUES (1, $1::jsonb, $2, $3) "
        "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, model_version = EXCLUDED.model_version, "
        "scored_at = EXCLUDED.scored_at",
        json.dumps(summary, ensure_ascii=False, default=_json_default),
        model_version,
        scored_at,
    )


async def write_results(
    conn: asyncpg.Connection,
    records: Sequence[tuple[Any, ...]],
    *,
    full: bool,
    removed_ids: Sequence[int],
    summary: dict[str, Any],
    model_version: int,
    scored_at: datetime,
    event_ids: Sequence[str],
) -> None:
    """Одна транзакция: upsert клиентов, удаление исчезнувших, summary, отметка событий."""
    async with conn.transaction():
        if full:
            await replace_all_scores_begin(conn)
            await copy_scores(conn, records)
        else:
            await conn.execute(
                "CREATE TEMP TABLE IF NOT EXISTS tmp_scores (LIKE scoring.customer_scores) ON COMMIT DELETE ROWS"
            )
            if records:
                await conn.copy_records_to_table("tmp_scores", records=records, columns=list(SCORE_COLUMNS))
                await conn.execute(_UPSERT_SQL)
            if removed_ids:
                await conn.execute(
                    "DELETE FROM scoring.customer_scores WHERE customer_id = ANY($1::bigint[])", list(removed_ids)
                )
        await save_summary(conn, summary, model_version, scored_at)
        await mark_processed(conn, event_ids)


async def vacuum_scores(conn: asyncpg.Connection) -> None:
    await conn.execute("VACUUM (ANALYZE) scoring.customer_scores")


async def summary_stamp(conn: asyncpg.Connection) -> tuple[datetime, int] | None:
    row = await conn.fetchrow("SELECT scored_at, model_version FROM scoring.summary WHERE id = 1")
    if row is None:
        return None
    return row["scored_at"], int(row["model_version"])


async def scores_empty(conn: asyncpg.Connection) -> bool:
    return not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM scoring.customer_scores)")
