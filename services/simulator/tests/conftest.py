from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_DIR.parents[1]
sys.path.insert(0, str(SERVICE_DIR))

OPENAPI = REPO_ROOT / "contracts" / "openapi.yaml"
SCHEMA_FILES = sorted((REPO_ROOT / "db" / "core").glob("*.sql"))
SAMPLE = REPO_ROOT / "data" / "sample" / "data.json"
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "postgresql://churn:churn@localhost:55433/churn")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: тесты против реальных Postgres и NATS")


@pytest.fixture(scope="session")
def openapi_validator():  # type: ignore[no-untyped-def]
    """Фабрика валидаторов по схемам из components/schemas контракта."""
    yaml = pytest.importorskip("yaml")
    jsonschema = pytest.importorskip("jsonschema")
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))

    def make(name: str) -> Any:
        schema = {"$ref": f"#/components/schemas/{name}", "components": spec["components"]}
        return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())

    return make
