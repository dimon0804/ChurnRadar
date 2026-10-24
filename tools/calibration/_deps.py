"""Подключение модели и симулятора из репозитория без установки пакетов.

riskcore берём из libs/riskcore, генератор и живой поток из services/simulator/app.
Код симулятора импортируется как есть, чтобы проверка шла на той же логике, что и демо.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT / "libs" / "riskcore", ROOT / "services" / "simulator"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import riskcore  # noqa: E402
from app import generator, stream, timeutil  # noqa: E402

__all__ = ["ROOT", "generator", "riskcore", "stream", "timeutil"]
