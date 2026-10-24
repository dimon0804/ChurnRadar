"""Признаки модели: по файлу на признак. Модули подключаются автоматически."""

import importlib
import pkgutil

ORDER = (
    "prolong_wait",
    "autoprolong_off_expiring",
    "insufficient_funds",
    "suspended",
    "failed_payments",
    "short_commitment",
    "churn_history",
    "inactivity",
    "bonus_dependency",
    "withdraw_pressure",
    "cancel_page_views",
    "panel_absence",
    "web_activity_drop",
)


def _load() -> None:
    found = {module.name for module in pkgutil.iter_modules(__path__) if not module.name.startswith("_")}
    for name in (*[n for n in ORDER if n in found], *sorted(found - set(ORDER))):
        importlib.import_module(f"{__name__}.{name}")


_load()
