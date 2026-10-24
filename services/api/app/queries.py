"""Чтение результатов расчёта: рейтинг клиентов, карточка, сводка."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any, Literal

import asyncpg
from svckit.errors import ApiError

from app import marketing

SortField = Literal["id", "email", "balance", "total_replenished", "risk_score", "expected_loss"]
SortOrder = Literal["asc", "desc"]
LEVELS = ("high", "medium", "low", "unknown")

# Белый список: имя параметра sort -> колонка. Только отсюда что-то попадает в ORDER BY.
SORT_COLUMNS: dict[str, str] = {
    "id": "customer_id",
    "email": "email",
    "balance": "balance",
    "total_replenished": "total_replenished",
    "risk_score": "risk_score",
    "expected_loss": "expected_loss",
}

SUMMARY_COLUMNS = (
    "customer_id, email, balance, bonus_balance, bonus_state, withdraw_balance, total_replenished, "
    "risk_score, risk_level, loyalty, reasons, services_total, services_at_risk, monthly_revenue, "
    "expected_loss, exposure"
)
BIGINT_MAX = 9_223_372_036_854_775_807


def iso_z(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def order_by(sort: str, order: str) -> str:
    """ORDER BY для рейтинга.

    При сортировке по score клиенты без оценки (unknown, risk_score IS NULL) всегда в конце,
    в каком бы направлении ни сортировали. Вторичный ключ — id по возрастанию: так
    постраничный вывод стабилен.
    """
    column = SORT_COLUMNS[sort]
    direction = "ASC" if order == "asc" else "DESC"
    if sort == "id":
        return f"customer_id {direction}"
    if sort == "risk_score":
        return f"risk_score {direction} NULLS LAST, customer_id ASC"
    return f"{column} {direction}, customer_id ASC"


def parse_levels(raw: str | None) -> list[str]:
    if raw is None:
        return []
    levels = [p.strip().lower() for p in raw.split(",") if p.strip()]
    bad = [lvl for lvl in levels if lvl not in LEVELS]
    if bad:
        raise ApiError(
            422,
            "validation_error",
            "Неизвестный уровень риска",
            [{"field": "level", "message": f"Допустимые значения: {', '.join(LEVELS)}", "value": bad, "type": "enum"}],
        )
    return sorted(set(levels), key=LEVELS.index)


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def check_score_range(score_min: float | None, score_max: float | None) -> None:
    if score_min is not None and score_max is not None and score_min >= score_max:
        raise ApiError(
            422,
            "validation_error",
            "Нижняя граница риска должна быть меньше верхней",
            [{"field": "score_min", "message": "score_min < score_max", "value": score_min, "type": "range"}],
        )


def where_clause(
    levels: list[str],
    search: str | None,
    *,
    reason: str | None = None,
    reason_scope: str = "any",
    score_min: float | None = None,
    score_max: float | None = None,
) -> tuple[str, list[Any]]:
    """WHERE для списка клиентов.

    reason — код причины: при reason_scope=main она главная (первая в reasons), при any —
    любая из причин клиента (колонки main_reason и reason_codes из миграции 009).
    score_min включается, score_max нет, кроме верхней границы шкалы 100: так корзины
    гистограммы [0, 10), [10, 20) ... [90, 100] не пересекаются и покрывают всю шкалу.
    """
    conditions: list[str] = []
    args: list[Any] = []
    if levels:
        args.append(levels)
        conditions.append(f"risk_level = ANY(${len(args)}::text[])")
    if reason:
        args.append(reason)
        conditions.append(f"main_reason = ${len(args)}" if reason_scope == "main" else f"reason_codes ? ${len(args)}")
    if score_min is not None:
        args.append(float(score_min))
        conditions.append(f"risk_score >= ${len(args)}")
    if score_max is not None:
        args.append(float(score_max))
        op = "<=" if score_max >= 100 else "<"
        conditions.append(f"risk_score {op} ${len(args)}")
    term = (search or "").strip()
    if term:
        args.append(f"%{_escape_like(term)}%")
        email_cond = f"email ILIKE ${len(args)}"
        if term.isdigit() and int(term) <= BIGINT_MAX:
            args.append(int(term))
            conditions.append(f"(customer_id = ${len(args)} OR {email_cond})")
        else:
            conditions.append(email_cond)
    return (" WHERE " + " AND ".join(conditions)) if conditions else "", args


def summary_item(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": row["customer_id"],
        "email": row["email"],
        "balance": row["balance"],
        "bonus_balance": row["bonus_balance"],
        "bonus_state": row["bonus_state"],
        "withdraw_balance": row["withdraw_balance"],
        "total_replenished": row["total_replenished"],
        "risk_score": row["risk_score"],
        "risk_level": row["risk_level"],
        "loyalty": row["loyalty"],
        "reasons": row["reasons"] or [],
        "services_total": row["services_total"],
        "services_at_risk": row["services_at_risk"],
        "monthly_revenue": row["monthly_revenue"],
        "expected_loss": row["expected_loss"],
        "exposure": row["exposure"],
    }


_META_SQL = """
SELECT
  COALESCE((SELECT model_version FROM scoring.summary WHERE id = 1),
           (SELECT max(model_version) FROM scoring.customer_scores),
           (SELECT version FROM scoring.settings WHERE id = 1),
           0) AS model_version,
  COALESCE((SELECT scored_at FROM scoring.summary WHERE id = 1),
           (SELECT max(scored_at) FROM scoring.customer_scores)) AS scored_at
"""


async def list_customers(
    pool: asyncpg.Pool,
    *,
    sort: str,
    order: str,
    levels: list[str],
    search: str | None,
    page: int,
    page_size: int,
    reason: str | None = None,
    reason_scope: str = "any",
    score_min: float | None = None,
    score_max: float | None = None,
) -> dict[str, Any]:
    check_score_range(score_min, score_max)
    where, args = where_clause(
        levels, search, reason=reason, reason_scope=reason_scope, score_min=score_min, score_max=score_max
    )
    offset = (page - 1) * page_size
    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT count(*) FROM scoring.customer_scores{where}", *args)
        rows = await conn.fetch(
            f"SELECT {SUMMARY_COLUMNS} FROM scoring.customer_scores{where} "
            f"ORDER BY {order_by(sort, order)} LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}",
            *args, page_size, offset,
        )
        meta = await conn.fetchrow(_META_SQL)
    return {
        "items": [summary_item(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "scored_at": iso_z(meta["scored_at"]),
        "model_version": meta["model_version"],
    }


def _get(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _default_money(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "monthly_revenue": row["monthly_revenue"],
        "exposure": row["exposure"],
        "expected_loss": row["expected_loss"],
        "balance_coverage_days": None,
        "explanation": "",
    }


async def get_customer(pool: asyncpg.Pool, customer_id: int) -> dict[str, Any]:
    if customer_id < 1 or customer_id > BIGINT_MAX:
        raise ApiError(404, "not_found", "Клиент не найден")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {SUMMARY_COLUMNS}, detail, model_version, scored_at, "
            "(SELECT margin FROM scoring.marketing_settings WHERE id = 1) AS margin "
            "FROM scoring.customer_scores WHERE customer_id = $1",
            customer_id,
        )
    if row is None:
        raise ApiError(404, "not_found", f"Клиент {customer_id} не найден или ещё не оценён")
    detail = row["detail"] or {}
    out = summary_item(row)
    out.update(
        {
            "services": detail.get("services", []),
            "features": detail.get("features", []),
            "transactions": detail.get("transactions", []),
            "money": detail.get("money") or _default_money(row),
            "recommendations": detail.get("recommendations", []),
            "next_action": detail.get("next_action"),
            "marketing": marketing.customer_block(detail.get("marketing"), _get(row, "margin")),
            "scored_at": iso_z(row["scored_at"]),
            "model_version": row["model_version"],
        }
    )
    return out


def empty_summary(horizon_days: int, model_version: int) -> dict[str, Any]:
    return {
        "customers_total": 0,
        "by_level": {"high": 0, "medium": 0, "low": 0, "unknown": 0},
        "services_total": 0,
        "services_at_risk": 0,
        "expected_loss": 0,
        "exposure": 0,
        "monthly_revenue_total": 0,
        "revenue_at_risk_share": 0.0,
        "horizon_days": horizon_days,
        "money_explanation": "Расчёт ещё не выполнялся: данные появятся после первой оценки клиентов.",
        "top_reasons": [],
        "timeline": [],
        "deleted_services": {"total": 0, "last_date": None, "last_estimated": False},
        "scored_at": None,
        "model_version": model_version,
    }


async def load_summary(pool: asyncpg.Pool) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT data, model_version, scored_at FROM scoring.summary WHERE id = 1")
        if row is None:
            settings_row = await conn.fetchrow("SELECT version, data FROM scoring.settings WHERE id = 1")
    if row is None:
        horizon = 30
        version = 0
        if settings_row is not None:
            version = settings_row["version"]
            try:
                horizon = int((settings_row["data"] or {}).get("horizon_days", 30))
            except (TypeError, ValueError):
                horizon = 30
        return empty_summary(horizon, version)
    data = dict(row["data"] or {})
    data["scored_at"] = iso_z(row["scored_at"])
    data["model_version"] = row["model_version"]
    base = empty_summary(int(data.get("horizon_days") or 30), row["model_version"])
    base.pop("money_explanation")
    for key, value in base.items():
        data.setdefault(key, value)
    data.setdefault("money_explanation", "")
    return data


class SummaryCache:
    """Сводка для /analytics/summary и /metrics.

    Перед выдачей из кэша сверяется отметка пересчёта (scored_at, model_version): после
    scores.updated панель сразу перезапрашивает сводку и не должна получить прежние цифры.
    TTL ограничивает только частоту этой сверки.
    """

    def __init__(self, ttl_s: float) -> None:
        self.ttl_s = ttl_s
        self._value: dict[str, Any] | None = None
        self._marketing: dict[str, Any] | None = None
        self._stamp: tuple[Any, Any] | None = None
        self._at = 0.0
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        self._at = 0.0
        self._stamp = None

    @property
    def last(self) -> dict[str, Any] | None:
        return self._value

    @property
    def last_marketing(self) -> dict[str, Any] | None:
        return self._marketing

    async def marketing(self, pool: asyncpg.Pool) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Базовые маркетинговые метрики последнего пересчёта и сводка, из которой они взяты."""
        summary = await self.get(pool)
        return self._marketing, summary

    async def get(self, pool: asyncpg.Pool) -> dict[str, Any]:
        async with self._lock:
            async with pool.acquire() as conn:
                row = await conn.fetchrow("SELECT scored_at, model_version FROM scoring.summary WHERE id = 1")
            stamp = (row["scored_at"], row["model_version"]) if row is not None else None
            fresh = time.monotonic() - self._at < self.ttl_s
            if self._value is not None and stamp is not None and stamp == self._stamp and fresh:
                return self._value
            value = await load_summary(pool)
            # маркетинг лежит в той же строке, но в /analytics/summary ему не место
            self._marketing = value.pop("marketing", None)
            self._value = value
            self._stamp = stamp
            self._at = time.monotonic()
            return self._value
