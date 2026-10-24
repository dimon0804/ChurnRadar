from __future__ import annotations

import json
from typing import Any

import pytest

from riskcore import Settings, default_settings

from .factories import OPENAPI_PATH, SAMPLE_PATH


@pytest.fixture
def settings() -> Settings:
    return default_settings()


@pytest.fixture(scope="session")
def sample_data() -> dict[str, Any]:
    return json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def openapi() -> dict[str, Any]:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
