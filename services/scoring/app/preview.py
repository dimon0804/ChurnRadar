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
from dataclasses import dataclass, replace
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
        # до первого SELECT: иначе во время замены снимка в ingest увидим пустые таблицы
        await repo.lock_source(conn)
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


@dataclass(frozen=True, slots=True)
class _Before:
    """«Было» на снимке: от ползунков не зависит, поэтому считается один раз на снимок."""

    results: list[CustomerResult]
    totals: dict[str, Any]


def _factor(data: _Input, sample: int) -> float:
    return 1.0 if data.exact or sample == 0 else data.customers_total / sample


def _before(data: _Input) -> _Before:
    src = data.source
    results = riskcore.score_customers(src.accounts, src.services, src.transactions, data.current, data.as_of, src.web)
    factor = _factor(data, len(results))
    return _Before(results, _totals(results, src.services, data.current, data.as_of, factor, data.customers_total))


def _compare(data: _Input, before: _Before | None) -> tuple[dict[str, Any], _Before]:
    """CPU-часть: «станет» (и «было», если его ещё нет) и сравнение. Выполняется в потоке."""
    if before is None:
        before = _before(data)
    src = data.source
    after = riskcore.score_customers(src.accounts, src.services, src.transactions, data.proposed, data.as_of, src.web)
    sample = len(after)
    changes = _changes(before.results, after)
    result = {
        "model_version": data.version,
        "exact": data.exact,
        "customers_total": data.customers_total,
        "sample_size": sample,
        "before": before.totals,
        "after": _totals(after, src.services, data.proposed, data.as_of, _factor(data, sample), data.customers_total),
        "changes_total": len(changes),
        "into_high": sum(1 for c in changes if c["level_after"] == "high"),
        "out_of_high": sum(1 for c in changes if c["level_before"] == "high"),
        "changes": changes[:CHANGES_LIMIT],
    }
    return result, before


def compute(data: _Input) -> dict[str, Any]:
    return _compare(data, None)[0]


async def _snapshot_key(conn: asyncpg.Connection) -> tuple[Any, ...] | None:
    """Версия настроек и отметка последнего пересчёта: пока они те же, снимок и «было» те же.

    Новые данные приходят в scoring событием и сразу запускают пересчёт, а он меняет отметку.
    """
    stamp = await repo.summary_stamp(conn)
    if stamp is None:
        return None
    version = await conn.fetchval("SELECT version FROM scoring.settings WHERE id = 1")
    return (version, *stamp)


class Previewer:
    """Один расчёт за раз; из очереди берётся только самый свежий запрос.

    Ползунок шлёт запросы чаще, чем считается выборка на большой базе. Без этого правила
    запросы выстраивались бы в очередь и ответ отставал бы от ползунка на секунды.
    Снимок данных и «было» держим в памяти до следующего пересчёта: пока пользователь
    двигает ползунки, на каждый запрос считается только «станет».
    """

    def __init__(self, pool: asyncpg.Pool, *, sample_size: int = 2000, timeout_s: float = 20.0) -> None:
        self._pool = pool
        self._sample_size = max(1, sample_size)
        self._timeout_s = timeout_s
        self._lock = asyncio.Lock()
        self._latest = 0
        self._cache: tuple[tuple[Any, ...], _Input, _Before] | None = None

    async def preview(self, proposed: Mapping[str, Any]) -> dict[str, Any]:
        self._latest += 1
        ticket = self._latest
        async with self._lock:
            if ticket != self._latest:
                raise PreviewSuperseded
            started = time.perf_counter()
            async with asyncio.timeout(self._timeout_s):
                async with self._pool.acquire() as conn:
                    key = await _snapshot_key(conn)
                    cached = self._cache if key is not None and self._cache and self._cache[0] == key else None
                    before: _Before | None = None
                    if cached is None:
                        data = await _load(conn, proposed, self._sample_size)
                    else:
                        _, base, before = cached
                        data = replace(base, proposed=riskcore.merge_settings(dict(proposed), version=base.version + 1))
                result, before = await asyncio.to_thread(_compare, data, before)
            self._cache = None if key is None else (key, data, before)
            result["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
            log.info(
                "preview_done",
                exact=result["exact"],
                sample=result["sample_size"],
                total=result["customers_total"],
                changes=result["changes_total"],
                cached=cached is not None,
                duration_ms=result["duration_ms"],
            )
            return result
