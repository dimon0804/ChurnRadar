"""Настройки api из переменных окружения."""

from __future__ import annotations

from pathlib import Path

from svckit.settings import ServiceSettings


def _default_openapi_path() -> str:
    """В образе спецификация лежит в /app/contracts, при запуске из репозитория — в его корне."""
    here = Path(__file__).resolve()
    candidates = [Path("/app/contracts/openapi.yaml")]
    candidates += [parent / "contracts" / "openapi.yaml" for parent in here.parents]
    for path in candidates:
        if path.is_file():
            return str(path)
    return str(candidates[0])


def _default_model_quality_path() -> str:
    """Результаты tools/calibration: в образе копия в /app/data, в репозитории — оригинал."""
    here = Path(__file__).resolve()
    candidates = [Path("/app/data/model_quality.json")]
    candidates += [parent / "tools" / "calibration" / "results" / "results.json" for parent in here.parents]
    for path in candidates:
        if path.is_file():
            return str(path)
    return str(candidates[0])


class Settings(ServiceSettings):
    redis_url: str = "redis://redis:6379/0"

    simulator_url: str = "http://simulator:8000"
    simulator_timeout_s: float = 120.0
    openapi_path: str = _default_openapi_path()
    model_quality_path: str = _default_model_quality_path()
    summary_cache_ttl_s: float = 30.0

    db_pool_max: int = 10
    nats_wait_on_start_s: float = 5.0


settings = Settings()
