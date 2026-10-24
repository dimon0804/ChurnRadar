import os
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[3] if len(_HERE.parents) > 3 else _HERE.parent


def _path_from_env(name: str, default: str, local_fallback: Path) -> Path:
    value = os.environ.get(name)
    if value:
        return Path(value)
    path = Path(default)
    # при запуске из репозитория без Docker берём файлы прямо из дерева
    if not path.exists() and local_fallback.exists():
        return local_fallback
    return path


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    snapshot_path: Path = field(
        default_factory=lambda: _path_from_env(
            "SNAPSHOT_PATH", "/app/data.json", _REPO_ROOT / "data" / "sample" / "data.json"
        )
    )
    contract_path: Path = field(
        default_factory=lambda: _path_from_env(
            "CONTRACT_PATH", "/app/openapi.yaml", _REPO_ROOT / "contracts" / "openapi.yaml"
        )
    )
    settings_path: Path = field(
        default_factory=lambda: Path(os.environ.get("SETTINGS_PATH", "/data/settings.json"))
    )
    nats_url: str = field(default_factory=lambda: os.environ.get("NATS_URL", "nats://nats:4222"))
    nats_enabled: bool = field(default_factory=lambda: _bool_env("NATS_ENABLED", True))
    metrics_token: str = field(
        default_factory=lambda: os.environ.get("METRICS_TOKEN", "churn-metrics-demo-token")
    )
    service_name: str = field(default_factory=lambda: os.environ.get("SERVICE_NAME", "mock-core"))
    simulator_interval_ms: int = field(
        default_factory=lambda: int(os.environ.get("SIMULATOR_INTERVAL_MS", "2000"))
    )
    simulator_autostart: bool = field(default_factory=lambda: _bool_env("SIMULATOR_AUTOSTART", False))
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "info"))
