"""Настройки модели: чтение, частичное изменение, сброс, журнал."""

from __future__ import annotations

import copy
import math
from typing import Any

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, model_validator
from svckit.errors import ApiError

from app import model_catalog


class ThresholdsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    medium: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    high: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)


class WeightPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=100)
    weight: float | None = Field(default=None, allow_inf_nan=False)
    enabled: bool | None = None


class ModelSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    horizon_days: int | None = Field(default=None, ge=1, le=365)
    thresholds: ThresholdsPatch | None = None
    weights: list[WeightPatch] | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> ModelSettingsPatch:
        if not any(getattr(self, f) is not None for f in ("horizon_days", "thresholds", "weights")):
            raise ValueError("Нужно указать хотя бы одно поле: horizon_days, thresholds или weights")
        return self


def apply_patch(
    current: dict[str, Any], catalog: list[dict[str, Any]], patch: ModelSettingsPatch
) -> dict[str, Any]:
    """Новая хранимая форма настроек. Ошибки валидации — ApiError 422 со списком details."""
    new = copy.deepcopy(current)
    errors: list[dict[str, Any]] = []
    by_code = {w["code"]: w for w in catalog}

    if patch.horizon_days is not None:
        new["horizon_days"] = patch.horizon_days
    if patch.thresholds is not None:
        if patch.thresholds.medium is not None:
            new["thresholds"]["medium"] = float(patch.thresholds.medium)
        if patch.thresholds.high is not None:
            new["thresholds"]["high"] = float(patch.thresholds.high)
    if new["thresholds"]["medium"] >= new["thresholds"]["high"]:
        errors.append(
            {
                "field": "thresholds",
                "message": "Порог medium должен быть меньше порога high",
                "type": "invalid_range",
            }
        )

    seen: set[str] = set()
    for i, w in enumerate(patch.weights or []):
        spec = by_code.get(w.code)
        if spec is None:
            errors.append({"field": f"weights.{i}.code", "message": f"Неизвестный признак «{w.code}»", "type": "unknown_feature"})
            continue
        if w.code in seen:
            errors.append({"field": f"weights.{i}.code", "message": f"Признак «{w.code}» указан дважды", "type": "duplicate"})
            continue
        seen.add(w.code)
        entry = new["weights"].setdefault(w.code, {"weight": float(spec["weight"]), "enabled": bool(spec["enabled"])})
        if w.weight is not None:
            lo, hi = float(spec.get("min", 0)), float(spec.get("max", 100))
            if not (lo <= w.weight <= hi):
                errors.append(
                    {
                        "field": f"weights.{i}.weight",
                        "message": f"Вес признака «{w.code}» должен быть от {lo:g} до {hi:g}",
                        "type": "out_of_range",
                    }
                )
                continue
            entry["weight"] = float(w.weight)
        if w.enabled is not None:
            entry["enabled"] = w.enabled

    if errors:
        raise ApiError(422, "validation_error", "Настройки модели не прошли проверку", errors)
    return new


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return math.isclose(float(a), float(b), rel_tol=0, abs_tol=1e-9)
        except (TypeError, ValueError):
            return False
    return a == b


def diff_settings(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    if not _same(old.get("horizon_days"), new.get("horizon_days")):
        changes.append({"path": "horizon_days", "old": old.get("horizon_days"), "new": new.get("horizon_days")})
    for key in ("medium", "high"):
        o, n = old["thresholds"].get(key), new["thresholds"].get(key)
        if not _same(o, n):
            changes.append({"path": f"thresholds.{key}", "old": o, "new": n})
    for code in sorted(set(old["weights"]) | set(new["weights"])):
        o_entry, n_entry = old["weights"].get(code, {}), new["weights"].get(code, {})
        for field in ("weight", "enabled"):
            o, n = o_entry.get(field), n_entry.get(field)
            if not _same(o, n):
                changes.append({"path": f"weights.{code}.{field}", "old": o, "new": n})
    return changes


_SELECT = "SELECT version, data, updated_at, updated_by FROM scoring.settings WHERE id = 1"


async def _ensure_row(conn: asyncpg.Connection, for_update: bool) -> asyncpg.Record:
    row = await conn.fetchrow(_SELECT + (" FOR UPDATE" if for_update else ""))
    if row is None:
        await conn.execute(
            "INSERT INTO scoring.settings (id, version, data, updated_by) VALUES (1, 1, $1, NULL) "
            "ON CONFLICT (id) DO NOTHING",
            model_catalog.default_storage(),
        )
        row = await conn.fetchrow(_SELECT + (" FOR UPDATE" if for_update else ""))
    return row


def _api_of(row: asyncpg.Record) -> dict[str, Any]:
    return model_catalog.to_api(row["data"], row["version"], row["updated_at"], row["updated_by"])


async def get_settings(pool: asyncpg.Pool) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_SELECT)
        if row is None:
            async with conn.transaction():
                row = await _ensure_row(conn, for_update=False)
    return _api_of(row)


async def _write_new(
    pool: asyncpg.Pool, user_name: str | None, build_new
) -> tuple[dict[str, Any], bool]:
    """Общая часть PATCH и reset. Возвращает (новое состояние, были ли изменения)."""
    async with pool.acquire() as conn, conn.transaction():
        row = await _ensure_row(conn, for_update=True)
        current_api = _api_of(row)
        current = model_catalog.storage_from_api(current_api)
        new = build_new(current, current_api["weights"])
        changes = diff_settings(current, new)
        if not changes:
            return current_api, False
        version = row["version"] + 1
        updated = await conn.fetchrow(
            "UPDATE scoring.settings SET version = $1, data = $2, updated_at = now(), updated_by = $3 "
            "WHERE id = 1 RETURNING version, data, updated_at, updated_by",
            version, new, user_name,
        )
        await conn.execute(
            "INSERT INTO scoring.settings_history (version, data, changes, changed_at, changed_by) "
            "VALUES ($1, $2, $3, $4, $5)",
            version, new, changes, updated["updated_at"], user_name,
        )
    return _api_of(updated), True


async def patch_settings(pool: asyncpg.Pool, patch: ModelSettingsPatch, user_name: str | None):
    return await _write_new(pool, user_name, lambda current, catalog: apply_patch(current, catalog, patch))


async def reset_settings(pool: asyncpg.Pool, user_name: str | None):
    return await _write_new(pool, user_name, lambda current, catalog: model_catalog.default_storage())


async def history(pool: asyncpg.Pool, limit: int) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT version, changed_at, changed_by, changes FROM scoring.settings_history "
            "ORDER BY version DESC LIMIT $1",
            limit,
        )
    return [
        {
            "version": r["version"],
            "changed_at": r["changed_at"],
            "changed_by": r["changed_by"],
            "changes": r["changes"],
        }
        for r in rows
    ]
