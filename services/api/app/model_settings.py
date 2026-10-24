"""Настройки модели: чтение, частичное изменение, сброс, журнал."""

from __future__ import annotations

import copy
import math
from datetime import UTC, datetime
from typing import Any

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, model_validator
from svckit.errors import ApiError
from svckit.outbox import add_event

from app import model_catalog

# churn.settings.changed пишется в outbox той же транзакцией, что и новая версия настроек
OUTBOX_TABLE = "scoring.outbox"
SUBJECT_SETTINGS_CHANGED = "churn.settings.changed"


class ThresholdsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    medium: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    high: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)


class WeightPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=100)
    weight: float | None = Field(default=None, allow_inf_nan=False)
    enabled: bool | None = None


class GroupPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=100)
    cap: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    enabled: bool | None = None


PATCH_FIELDS = ("horizon_days", "thresholds", "weights", "groups", "calibrated")


class ModelSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    horizon_days: int | None = Field(default=None, ge=1, le=365)
    thresholds: ThresholdsPatch | None = None
    weights: list[WeightPatch] | None = None
    groups: list[GroupPatch] | None = None
    calibrated: bool | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> ModelSettingsPatch:
        if not any(getattr(self, f) is not None for f in PATCH_FIELDS):
            raise ValueError("Нужно указать хотя бы одно поле: " + ", ".join(PATCH_FIELDS))
        return self


def apply_patch(
    current: dict[str, Any],
    catalog: list[dict[str, Any]],
    patch: ModelSettingsPatch,
    group_catalog: list[dict[str, Any]] | None = None,
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

    known_groups = {g["code"]: g for g in group_catalog or []}
    seen_groups: set[str] = set()
    for i, g in enumerate(patch.groups or []):
        spec = known_groups.get(g.code)
        if spec is None:
            errors.append({"field": f"groups.{i}.code", "message": f"Неизвестная группа «{g.code}»", "type": "unknown_group"})
            continue
        if g.code in seen_groups:
            errors.append({"field": f"groups.{i}.code", "message": f"Группа «{g.code}» указана дважды", "type": "duplicate"})
            continue
        seen_groups.add(g.code)
        entry = new.setdefault("groups", {}).setdefault(
            g.code, {"cap": float(spec["cap"]), "enabled": bool(spec["enabled"])}
        )
        if g.cap is not None:
            entry["cap"] = float(g.cap)
        if g.enabled is not None:
            entry["enabled"] = g.enabled
    if patch.calibrated is not None:
        new["calibrated"] = patch.calibrated

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
    old_groups, new_groups = old.get("groups") or {}, new.get("groups") or {}
    for code in sorted(set(old_groups) | set(new_groups)):
        o_entry, n_entry = old_groups.get(code, {}), new_groups.get(code, {})
        for field in ("cap", "enabled"):
            o, n = o_entry.get(field), n_entry.get(field)
            if not _same(o, n):
                changes.append({"path": f"groups.{code}.{field}", "old": o, "new": n})
    if "calibrated" in old or "calibrated" in new:
        o, n = old.get("calibrated"), new.get("calibrated")
        if not _same(o, n):
            changes.append({"path": "calibrated", "old": o, "new": n})
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
        new = build_new(current, current_api["weights"], current_api.get("groups", []))
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
        await add_event(
            conn, OUTBOX_TABLE, SUBJECT_SETTINGS_CHANGED, "settings.changed",
            {"version": updated["version"], "updated_by": updated["updated_by"]},
        )
    return _api_of(updated), True


async def patch_settings(pool: asyncpg.Pool, patch: ModelSettingsPatch, user_name: str | None):
    return await _write_new(
        pool, user_name, lambda current, catalog, groups: apply_patch(current, catalog, patch, groups)
    )


async def reset_settings(pool: asyncpg.Pool, user_name: str | None):
    return await _write_new(pool, user_name, lambda current, catalog, groups: model_catalog.default_storage())


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class VersionNotFound(ApiError):
    def __init__(self, version: int) -> None:
        super().__init__(404, "version_not_found", f"Версии настроек {version} нет в журнале")


async def version_data(conn: asyncpg.Connection, version: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Хранимая форма настроек версии и её метка (кто и когда). Текущая версия — из scoring.settings.

    Первая версия в журнал не пишется (она создаётся по умолчанию), поэтому для version = 1 без
    записи в журнале берём настройки по умолчанию.
    """
    current = await conn.fetchrow(_SELECT)
    if current is not None and current["version"] == version:
        data = current["data"]
        ref = {"version": version, "changed_at": current["updated_at"], "changed_by": current["updated_by"]}
    else:
        row = await conn.fetchrow(
            "SELECT version, data, changed_at, changed_by FROM scoring.settings_history WHERE version = $1", version
        )
        if row is not None:
            data = row["data"]
            ref = {"version": version, "changed_at": row["changed_at"], "changed_by": row["changed_by"]}
        elif version == 1:
            data = model_catalog.default_storage()
            ref = {"version": 1, "changed_at": None, "changed_by": None}
        else:
            raise VersionNotFound(version)
    # Старые версии хранились без групп и калибровки: доводим до полной формы так же, как scoring.
    full = model_catalog.storage_from_api(model_catalog.to_api(data, version, datetime.now(UTC), None))
    return full, ref


async def rollback_settings(pool: asyncpg.Pool, version: int, user_name: str | None):
    async with pool.acquire() as conn:
        target, _ = await version_data(conn, version)
    return await _write_new(pool, user_name, lambda current, catalog, groups: copy.deepcopy(target))


async def compare_versions(pool: asyncpg.Pool, version_from: int, version_to: int) -> dict[str, Any]:
    """Различия двух версий; эффект на данных считает scoring (см. preview.compare)."""
    async with pool.acquire() as conn:
        old, ref_from = await version_data(conn, version_from)
        new, ref_to = await version_data(conn, version_to)
    return {
        "from": ref_from,
        "to": ref_to,
        "changes": diff_settings(old, new),
        "old_storage": old,
        "new_storage": new,
    }


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
