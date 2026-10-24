"""Режим «что если»: расчёт по новым настройкам без записи в БД.

Обе стороны сравнения («было» и «станет») считаются на одном снимке source.* в одной
транзакции REPEATABLE READ, поэтому разница — это только эффект настроек, а не изменений
данных за время с последнего пересчёта. «Станет» считается тем же riskcore.score_customers
и build_summary, что и полный пересчёт, поэтому после PATCH получатся те же цифры.

На больших базах точный расчёт занимает десятки секунд, поэтому выше sample_size клиентов
считаем детерминированную выборку (одни и те же клиенты от запроса к запросу) и
масштабируем счётчики и деньги на всю базу. В ответе это видно по exact=false.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import asyncpg
import riskcore
from riskcore import CustomerResult
from riskcore.types import Service, Settings
from svckit.logs import get_logger

from . import repository as repo

log = get_logger("scoring.preview")

LEVELS = ("high", "medium", "low", "unknown")
CHANGES_LIMIT = 50


class PreviewSuperseded(RuntimeError):
    """Пока запрос ждал очереди, пришёл более новый — считать старый незачем."""


@dataclass(frozen=True, slots=True)
class _Input:
    version: int
    current: Settings
    proposed: Settings
    customers_total: int
    exact: bool
    source: repo.SourceData
    as_of: Any


async def _current_settings(conn: asyncpg.Connection) -> tuple[int, dict[str, Any]]:
    row = await conn.fetchrow("SELECT version, data FROM scoring.settings WHERE id = 1")
    if row is None:
        return 1, riskcore.default_settings().to_storage()
    data = row["data"]
    if isinstance(data, str):
        data = json.loads(data)
    return int(row["version"]), data


async def _load(conn: asyncpg.Connection, proposed: Mapping[str, Any], sample_size: int) -> _Input:
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        version, data = await _current_settings(conn)
        as_of, _ = await repo.resolve_as_of(conn)
        total = int(await conn.fetchval("SELECT count(*) FROM source.accounts"))
        exact = total <= sample_size
        if exact:
            source = await repo.load_source(conn)
        else:
            # md5(id) перемешивает клиентов, но выборка одна и та же, пока не меняется состав базы
            ids = [
                r[0]
                for r in await conn.fetch(
                    "SELECT id FROM source.accounts ORDER BY md5(id::text), id LIMIT $1", sample_size
                )
            ]
            source = await repo.load_source(conn, ids)
    return _Input(
        version=version,
        # «станет» получает следующую версию: так же её получит настройка после PATCH
        current=riskcore.merge_settings(data, version=version),
        proposed=riskcore.merge_settings(dict(proposed), version=version + 1),
        customers_total=total,
        exact=exact,
        source=source,
        as_of=as_of,
    )


def _scale_levels(by_level: Mapping[str, int], factor: float, total: int) -> dict[str, int]:
    """Уровни выборки на всю базу: округляем и сводим сумму к total за счёт самого крупного уровня."""
    scaled = {level: round(by_level.get(level, 0) * factor) for level in LEVELS}
    drift = total - sum(scaled.values())
    if drift and any(scaled.values()):
        biggest = max(LEVELS, key=lambda level: scaled[level])
        scaled[biggest] = max(0, scaled[biggest] + drift)
    return scaled


def _totals(
    results: Sequence[CustomerResult], services: Sequence[Service], settings: Settings, as_of: Any, factor: float,
    customers_total: int,
) -> dict[str, Any]:
    summary = riskcore.build_summary(results, services, [], settings, as_of)
    if factor == 1.0:
        by_level = {level: int(summary["by_level"][level]) for level in LEVELS}
        expected_loss = int(summary["expected_loss"])
        exposure = int(summary["exposure"])
        at_risk = int(summary["services_at_risk"])
    else:
        by_level = _scale_levels(summary["by_level"], factor, customers_total)
        expected_loss = round(summary["expected_loss"] * factor)
        exposure = round(summary["exposure"] * factor)
        at_risk = round(summary["services_at_risk"] * factor)
    return {
        "horizon_days": settings.horizon_days,
        "thresholds": dict(settings.thresholds),
        "expected_loss": expected_loss,
        "exposure": exposure,
        "services_at_risk": at_risk,
        "revenue_at_risk_share": float(summary["revenue_at_risk_share"]),
        "by_level": by_level,
    }


def _change_rank(change: dict[str, Any]) -> tuple[int, float]:
    if change["level_after"] == "high":
        group = 0  # попадает в высокий риск
    elif change["level_before"] == "high":
        group = 1  # выходит из высокого
    else:
        group = 2
    return group, -abs(change["expected_loss_after"] - change["expected_loss_before"])


def _changes(before: Sequence[CustomerResult], after: Sequence[CustomerResult]) -> list[dict[str, Any]]:
    old = {r.customer_id: r for r in before}
    changes = []
    for new in after:
        was = old.get(new.customer_id)
        if was is None or was.risk_level == new.risk_level:
            continue
        changes.append(
            {
                "id": new.customer_id,
                "email": new.email,
                "level_before": was.risk_level,
                "level_after": new.risk_level,
                "score_before": was.risk_score,
                "score_after": new.risk_score,
                "expected_loss_before": was.expected_loss,
                "expected_loss_after": new.expected_loss,
            }
        )
    changes.sort(key=_change_rank)
    return changes


def compute(data: _Input) -> dict[str, Any]:
    """CPU-часть: два расчёта riskcore и сравнение. Выполняется в потоке."""
    src = data.source
    before = riskcore.score_customers(src.accounts, src.services, src.transactions, data.current, data.as_of)
    after = riskcore.score_customers(src.accounts, src.services, src.transactions, data.proposed, data.as_of)
    sample = len(before)
    factor = 1.0 if data.exact or sample == 0 else data.customers_total / sample
    changes = _changes(before, after)
    return {
        "model_version": data.version,
        "exact": data.exact,
        "customers_total": data.customers_total,
        "sample_size": sample,
        "before": _totals(before, src.services, data.current, data.as_of, factor, data.customers_total),
        "after": _totals(after, src.services, data.proposed, data.as_of, factor, data.customers_total),
        "changes_total": len(changes),
        "into_high": sum(1 for c in changes if c["level_after"] == "high"),
        "out_of_high": sum(1 for c in changes if c["level_before"] == "high"),
        "changes": changes[:CHANGES_LIMIT],
    }


class Previewer:
    """Один расчёт за раз; из очереди берётся только самый свежий запрос.

    Ползунок шлёт запросы чаще, чем считается выборка на большой базе. Без этого правила
    запросы выстраивались бы в очередь и ответ отставал бы от ползунка на секунды.
    """

    def __init__(self, pool: asyncpg.Pool, *, sample_size: int = 2000, timeout_s: float = 20.0) -> None:
        self._pool = pool
        self._sample_size = max(1, sample_size)
        self._timeout_s = timeout_s
        self._lock = asyncio.Lock()
        self._latest = 0

    async def preview(self, proposed: Mapping[str, Any]) -> dict[str, Any]:
        self._latest += 1
        ticket = self._latest
        async with self._lock:
            if ticket != self._latest:
                raise PreviewSuperseded
            started = time.perf_counter()
            async with asyncio.timeout(self._timeout_s):
                async with self._pool.acquire() as conn:
                    data = await _load(conn, proposed, self._sample_size)
                result = await asyncio.to_thread(compute, data)
            result["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
            log.info(
                "preview_done",
                exact=result["exact"],
                sample=result["sample_size"],
                total=result["customers_total"],
                changes=result["changes_total"],
                duration_ms=result["duration_ms"],
            )
            return result
