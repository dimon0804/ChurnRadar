"""Коннектор «HTTP API биллинга» (contracts/billing-source.openapi.yaml).

Забирает изменения аккаунтов, услуг и транзакций с последнего updatedAt по каждой сущности
и кладёт их тем же путём, что и push-поток: apply_changes → source.* и событие в outbox,
дальше пересчёт идёт как обычно.

updated_since включительный: записи с тем же updatedAt, что и курсор, придут повторно, а upsert
сделает повтор безвредным. Так не теряются записи, у которых совпало время изменения.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import asyncpg
from pydantic import AwareDatetime, TypeAdapter, ValidationError
from svckit.errors import ApiError

from app.repository import WriteResult, apply_changes
from app.schemas import SourceAccount, SourceChangeBatch, SourceService, SourceTransaction

from .http import SourceClient, SourceError

ENTITIES = ("accounts", "services", "transactions")
ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "accounts": TypeAdapter(SourceAccount),
    "services": TypeAdapter(SourceService),
    "transactions": TypeAdapter(SourceTransaction),
}
MOMENT = TypeAdapter(AwareDatetime)
# Больше записей за один apply_changes не кладём: одна транзакция и одно событие на порцию.
APPLY_CHUNK = 2000


@dataclass(slots=True)
class Item:
    updated_at: datetime
    record: Any


@dataclass(slots=True)
class Pulled:
    items: dict[str, list[Item]] = field(default_factory=lambda: {e: [] for e in ENTITIES})
    truncated: bool = False


@dataclass(slots=True)
class BillingResult:
    cursor: dict[str, str]
    records: int
    details: dict[str, Any]
    account_ids: set[int]
    writes: int


def iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def parse_item(entity: str, index: int, raw: Any) -> Item:
    if not isinstance(raw, dict):
        raise SourceError(f"{entity}[{index}]: запись должна быть объектом")
    try:
        updated = MOMENT.validate_python(raw.get("updatedAt"))
        record = ADAPTERS[entity].validate_python(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "updatedAt"
        raise SourceError(f"{entity}[{index}] не по контракту: {where} — {first['msg']}") from exc
    return Item(updated, record)


async def pull(client: SourceClient, cursor: dict[str, Any], *, page_size: int, max_records: int) -> Pulled:
    """Страницы по каждой сущности до next_cursor = null или до лимита записей за запуск."""
    pulled = Pulled()
    total = 0
    for entity in ENTITIES:
        page: str | None = None
        while True:
            body = await client.get_json(
                f"/{entity}", {"updated_since": cursor.get(entity), "cursor": page, "limit": page_size}
            )
            if not isinstance(body, dict) or not isinstance(body.get("items"), list):
                raise SourceError(f"/{entity}: в ответе нет массива items")
            for raw in body["items"]:
                pulled.items[entity].append(parse_item(entity, len(pulled.items[entity]), raw))
            total += len(body["items"])
            page = body.get("next_cursor")
            if total >= max_records:
                pulled.truncated = bool(page) or entity != ENTITIES[-1]
                return pulled
            if not page:
                break
    return pulled


def _latest(items: list[Item]) -> list[Item]:
    """Одна запись на id — самая свежая (страница могла повториться при сдвиге курсора)."""
    by_id: dict[int, Item] = {}
    for item in items:
        known = by_id.get(item.record.id)
        if known is None or item.updated_at >= known.updated_at:
            by_id[item.record.id] = item
    return sorted(by_id.values(), key=lambda i: (i.updated_at, i.record.id))


async def _known(pool: asyncpg.Pool, table: str, ids: set[int]) -> set[int]:
    if not ids:
        return set()
    rows = await pool.fetch(f"SELECT id FROM source.{table} WHERE id = ANY($1::bigint[])", list(ids))
    return {r["id"] for r in rows}


def _watermark(previous: str | None, accepted: list[Item], orphans: list[Item]) -> str | None:
    """Новый курсор сущности. Записи-сироты (ссылаются на то, чего ещё нет) не теряем: курсор
    останавливается на первой из них, и следующий запуск заберёт её снова. Если курсор уже стоит
    на ней (ссылка так и не появилась), идём дальше, чтобы не крутиться на месте вечно."""
    newest = max((i.updated_at for i in accepted + orphans), default=None)
    if newest is None:
        return previous
    if orphans:
        first_orphan = iso(min(i.updated_at for i in orphans))
        if first_orphan != previous:
            return first_orphan
    return iso(newest)


async def run_billing(
    pool: asyncpg.Pool,
    client: SourceClient,
    cursor: dict[str, Any],
    options: dict[str, Any],
    *,
    created_by: str,
) -> BillingResult:
    pulled = await pull(client, cursor, page_size=options["page_size"], max_records=options["max_records"])
    accounts = _latest(pulled.items["accounts"])
    services = _latest(pulled.items["services"])
    transactions = _latest(pulled.items["transactions"])

    account_ids = {i.record.id for i in accounts}
    missing_owners = {i.record.owner_id for i in services} - account_ids
    known_owners = account_ids | await _known(pool, "accounts", missing_owners)
    service_ok = [i for i in services if i.record.owner_id in known_owners]
    service_orphans = [i for i in services if i.record.owner_id not in known_owners]

    service_ids = {i.record.id for i in service_ok}
    missing_services = {i.record.service_id for i in transactions} - service_ids
    known_services = service_ids | await _known(pool, "services", missing_services)
    tx_ok = [i for i in transactions if i.record.service_id in known_services]
    tx_orphans = [i for i in transactions if i.record.service_id not in known_services]

    affected: set[int] = set()
    writes = 0
    chunks: list[SourceChangeBatch]
    total = len(accounts) + len(service_ok) + len(tx_ok)
    if total <= APPLY_CHUNK:
        chunks = [
            SourceChangeBatch(
                accounts=[i.record for i in accounts],
                services=[i.record for i in service_ok],
                transactions=[i.record for i in tx_ok],
            )
        ] if total else []
    else:
        # по порядку сущностей: к услугам их владельцы уже в базе, к транзакциям — услуги
        chunks = []
        for name, items in (("accounts", accounts), ("services", service_ok), ("transactions", tx_ok)):
            for start in range(0, len(items), APPLY_CHUNK):
                part = [i.record for i in items[start : start + APPLY_CHUNK]]
                chunks.append(SourceChangeBatch(**{name: part}))
    for batch in chunks:
        try:
            result: WriteResult = await apply_changes(pool, batch, created_by=created_by)
        except ApiError as exc:
            detail = ""
            if isinstance(exc.details, list) and exc.details:
                first = exc.details[0]
                detail = f": {first.get('message', first)}" if isinstance(first, dict) else f": {first}"
            raise SourceError(f"Данные источника не прошли проверку — {exc.message}{detail}") from exc
        affected.update(result.account_ids)
        writes += 1

    new_cursor = dict(cursor)
    for name, ok, orphans in (
        ("accounts", accounts, []),
        ("services", service_ok, service_orphans),
        ("transactions", tx_ok, tx_orphans),
    ):
        mark = _watermark(cursor.get(name), ok, orphans)
        if mark is not None:
            new_cursor[name] = mark
    return BillingResult(
        cursor=new_cursor,
        records=total,
        details={
            "accounts": len(accounts),
            "services": len(service_ok),
            "transactions": len(tx_ok),
            "skipped": len(service_orphans) + len(tx_orphans),
            "truncated": pulled.truncated,
        },
        account_ids=affected,
        writes=writes,
    )
