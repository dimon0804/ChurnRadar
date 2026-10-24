"""Маркетинговые метрики: выдача готовых чисел из scoring.summary и настройки маржи и CAC.

Сами формулы — в riskcore.marketing. scoring считает базовые метрики при каждом пересчёте
(вместе со сводкой), api только накладывает маржу и стоимость привлечения: они меняются
руками маркетолога и пересчёта модели не требуют.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import asyncpg
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MARGIN = 1.0
# CAC до миллиона евро: больше — почти наверняка опечатка (лишние нули)
CAC_MAX = 100_000_000

_SELECT = "SELECT margin, cac, updated_at, updated_by FROM scoring.marketing_settings WHERE id = 1"


class MarketingSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    margin: float = Field(ge=0.01, le=1.0, allow_inf_nan=False)
    cac: int | None = Field(default=None, ge=1, le=CAC_MAX)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def settings_of(row: Any) -> dict[str, Any]:
    if row is None:
        return {"margin": DEFAULT_MARGIN, "cac": None, "updated_at": None, "updated_by": None}
    return {
        "margin": float(row["margin"]),
        "cac": None if row["cac"] is None else int(row["cac"]),
        "updated_at": _iso(row["updated_at"]),
        "updated_by": row["updated_by"],
    }


async def get_settings(pool: asyncpg.Pool) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_SELECT)
    return settings_of(row)


async def put_settings(pool: asyncpg.Pool, update: MarketingSettingsUpdate, user_name: str | None) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO scoring.marketing_settings (id, margin, cac, updated_at, updated_by) "
            "VALUES (1, $1, $2, now(), $3) "
            "ON CONFLICT (id) DO UPDATE SET margin = EXCLUDED.margin, cac = EXCLUDED.cac, "
            "updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by "
            "RETURNING margin, cac, updated_at, updated_by",
            float(update.margin), update.cac, user_name,
        )
    return settings_of(row)


def build_response(
    base: dict[str, Any] | None, summary: dict[str, Any], settings: dict[str, Any]
) -> dict[str, Any]:
    """Ответ /analytics/marketing: базовые метрики из сводки, маржа и CAC из настроек.

    До первого пересчёта (или со сводкой старого формата без marketing) отдаём пустую базу:
    те же поля, нули и null.
    """
    import riskcore

    horizon = int(summary.get("horizon_days") or 30)
    if not base:
        base = riskcore.build_marketing([], [], horizon, datetime.now(UTC))
    out = riskcore.apply_economics(base, settings["margin"], settings["cac"])
    out["settings"] = settings
    out["scored_at"] = summary.get("scored_at")
    out["model_version"] = summary.get("model_version", 0)
    return out


def customer_block(block: dict[str, Any] | None, margin: float | None) -> dict[str, Any] | None:
    """Блок marketing карточки клиента с маржей из настроек. Старые оценки без блока — null."""
    if not block:
        return None
    import riskcore

    return riskcore.scale_customer(block, DEFAULT_MARGIN if margin is None else float(margin))
