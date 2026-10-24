"""Настройки модели и журнал их изменений с сохранением в JSON-файл."""

import copy
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .features import FEATURES, FEATURES_BY_CODE
from .schemas import ModelSettingsPatch
from .timeutil import iso

log = logging.getLogger("mock-core.settings")

DEFAULT_HORIZON_DAYS = 30
DEFAULT_THRESHOLDS = {"medium": 35.0, "high": 65.0}
HISTORY_LIMIT = 500


class SettingsValidationError(ValueError):
    def __init__(self, message: str, details: Optional[list] = None):
        super().__init__(message)
        self.details = details or []


def default_state() -> Dict[str, Any]:
    return {
        "horizon_days": DEFAULT_HORIZON_DAYS,
        "thresholds": dict(DEFAULT_THRESHOLDS),
        "weights": {f.code: {"weight": float(f.default_weight), "enabled": True} for f in FEATURES},
    }


def _flatten(state: Dict[str, Any]) -> Dict[str, Any]:
    flat = {"horizon_days": state["horizon_days"]}
    for key, value in state["thresholds"].items():
        flat[f"thresholds.{key}"] = value
    for code, w in state["weights"].items():
        flat[f"weights.{code}.weight"] = w["weight"]
        flat[f"weights.{code}.enabled"] = w["enabled"]
    return flat


def diff(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, Any]]:
    a, b = _flatten(old), _flatten(new)
    return [{"path": k, "old": a.get(k), "new": b[k]} for k in b if a.get(k) != b[k]]


class SettingsManager:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.state = default_state()
        self.version = 1
        self.updated_at = datetime.now(timezone.utc)
        self.updated_by: Optional[str] = None
        self.history: List[Dict[str, Any]] = []
        self.persisted = False

    # --- файл ---

    def load(self) -> None:
        if not self.path.exists():
            self.save()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("не удалось прочитать %s: %s, беру значения по умолчанию", self.path, exc)
            return
        state = default_state()
        stored = raw.get("settings", {})
        if isinstance(stored.get("horizon_days"), int):
            state["horizon_days"] = stored["horizon_days"]
        for key in ("medium", "high"):
            value = stored.get("thresholds", {}).get(key)
            if isinstance(value, (int, float)):
                state["thresholds"][key] = float(value)
        for code, w in stored.get("weights", {}).items():
            if code in state["weights"] and isinstance(w, dict):
                state["weights"][code]["weight"] = float(w.get("weight", state["weights"][code]["weight"]))
                state["weights"][code]["enabled"] = bool(w.get("enabled", True))
        self.state = state
        self.version = int(raw.get("version", 1))
        if raw.get("updated_at"):
            self.updated_at = datetime.fromisoformat(raw["updated_at"].replace("Z", "+00:00"))
        self.updated_by = raw.get("updated_by")
        self.history = list(raw.get("history", []))[:HISTORY_LIMIT]
        self.persisted = True
        log.info("настройки модели загружены из %s, версия %s", self.path, self.version)

    def save(self) -> None:
        payload = {
            "version": self.version,
            "updated_at": iso(self.updated_at),
            "updated_by": self.updated_by,
            "settings": self.state,
            "history": self.history,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".settings-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            self.persisted = True
        except OSError as exc:
            self.persisted = False
            log.warning("не удалось сохранить настройки в %s: %s (работаю в памяти)", self.path, exc)

    # --- чтение ---

    @property
    def horizon_days(self) -> int:
        return self.state["horizon_days"]

    @property
    def thresholds(self) -> Dict[str, float]:
        return self.state["thresholds"]

    def weight(self, code: str) -> float:
        w = self.state["weights"][code]
        return w["weight"] if w["enabled"] else 0.0

    def snapshot(self) -> Dict[str, Any]:
        """Неизменяемая копия, которой пользуется расчёт."""
        return copy.deepcopy(self.state)

    def to_api(self) -> Dict[str, Any]:
        weights = []
        for f in FEATURES:
            w = self.state["weights"][f.code]
            weights.append(
                {
                    "code": f.code,
                    "title": f.title,
                    "description": f.description,
                    "group": f.group,
                    "level": f.level,
                    "weight": w["weight"],
                    "default_weight": float(f.default_weight),
                    "min": f.min,
                    "max": f.max,
                    "enabled": w["enabled"],
                }
            )
        return {
            "version": self.version,
            "updated_at": iso(self.updated_at),
            "updated_by": self.updated_by,
            "horizon_days": self.state["horizon_days"],
            "thresholds": dict(self.state["thresholds"]),
            "weights": weights,
        }

    def history_items(self, limit: int) -> List[Dict[str, Any]]:
        return self.history[:limit]

    # --- изменение ---

    def patch(self, patch: ModelSettingsPatch, user: Optional[str]) -> List[Dict[str, Any]]:
        new = copy.deepcopy(self.state)
        errors = []
        if patch.horizon_days is not None:
            new["horizon_days"] = patch.horizon_days
        if patch.thresholds is not None:
            if patch.thresholds.medium is not None:
                new["thresholds"]["medium"] = float(patch.thresholds.medium)
            if patch.thresholds.high is not None:
                new["thresholds"]["high"] = float(patch.thresholds.high)
        for item in patch.weights or []:
            feature = FEATURES_BY_CODE.get(item.code)
            if feature is None:
                errors.append({"field": f"weights.{item.code}", "message": "неизвестный признак"})
                continue
            if item.weight is not None:
                if not feature.min <= item.weight <= feature.max:
                    errors.append(
                        {
                            "field": f"weights.{item.code}.weight",
                            "message": f"вес должен быть от {feature.min:g} до {feature.max:g}",
                        }
                    )
                    continue
                new["weights"][item.code]["weight"] = float(item.weight)
            if item.enabled is not None:
                new["weights"][item.code]["enabled"] = item.enabled
        if new["thresholds"]["medium"] > new["thresholds"]["high"]:
            errors.append({"field": "thresholds", "message": "порог medium не может быть больше порога high"})
        if errors:
            raise SettingsValidationError("Некорректные настройки модели", errors)
        return self._commit(new, user)

    def reset(self, user: Optional[str]) -> List[Dict[str, Any]]:
        return self._commit(default_state(), user)

    def _commit(self, new: Dict[str, Any], user: Optional[str]) -> List[Dict[str, Any]]:
        changes = diff(self.state, new)
        self.state = new
        self.version += 1
        self.updated_at = datetime.now(timezone.utc)
        self.updated_by = user
        self.history.insert(
            0,
            {"version": self.version, "changed_at": iso(self.updated_at), "changed_by": user, "changes": changes},
        )
        del self.history[HISTORY_LIMIT:]
        self.save()
        return changes
