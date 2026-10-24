"""Страница «Качество модели»: эндпоинт, схема results.json и его актуальность."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import model_quality
from app.config import settings
from tests.conftest import ANALYST

ROOT = Path(__file__).resolve().parents[3]
RESULTS = ROOT / "tools" / "calibration" / "results" / "results.json"
REGENERATE = "пересчитайте: python -m tools.calibration (из корня репозитория) и закоммитьте results.json"


@pytest.fixture(autouse=True)
def fresh_cache():
    model_quality.load.cache_clear()
    yield
    model_quality.load.cache_clear()


def _raw() -> dict:
    return json.loads(RESULTS.read_text(encoding="utf-8"))


def test_results_json_matches_schema():
    report = model_quality.parse_report(_raw())
    assert set(report["scenarios"]) == {"stream", "calendar"}
    for scenario in report["scenarios"].values():
        levels = {row["level"] for row in scenario["levels"]}
        assert levels == {"low", "medium", "high", "all"}
        assert len(scenario["roc"]["fpr"]) == len(scenario["roc"]["tpr"]["model"])
        assert scenario["ranking"]["churned"]["model"]["roc_auc_ci"] is not None
    assert report["robustness"], (
        "в results.json нет прогонов на устойчивость: " + REGENERATE
    )


def test_results_json_is_fresh():
    """Цифры на странице должны относиться к текущему коду модели и симулятора."""
    params = _raw()["params"]
    riskcore_now = model_quality.fingerprint(ROOT / "libs" / "riskcore" / "riskcore")
    simulator_now = model_quality.fingerprint(ROOT / "services" / "simulator" / "app")
    assert params["riskcore_sources"] == riskcore_now, (
        f"riskcore изменился после проверки, {REGENERATE}"
    )
    assert params["simulator_sources"] == simulator_now, (
        f"симулятор изменился после проверки, {REGENERATE}"
    )


def test_default_path_points_to_results():
    assert Path(settings.model_quality_path).resolve() == RESULTS.resolve()


async def test_endpoint_returns_report(client):
    resp = await client.get("/api/v1/model/quality", headers=ANALYST)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["params"]["accounts"] == _raw()["params"]["accounts"]
    assert body["current_model"]["riskcore_sources"]
    assert isinstance(body["current_model"]["matches"], bool)
    # лишнее из results.json наружу не идёт
    assert "events" not in body["scenarios"]["stream"]
    assert "service_level" not in body["scenarios"]["stream"]


async def test_missing_file_is_503(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "model_quality_path", str(tmp_path / "nope.json"))
    resp = await client.get("/api/v1/model/quality", headers=ANALYST)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "model_quality_unavailable"


async def test_broken_schema_is_503(client, monkeypatch, tmp_path):
    raw = _raw()
    del raw["scenarios"]["stream"]["roc"]
    path = tmp_path / "results.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(settings, "model_quality_path", str(path))
    resp = await client.get("/api/v1/model/quality", headers=ANALYST)
    assert resp.status_code == 503


def test_nan_becomes_null():
    raw = _raw()
    raw["scenarios"]["calendar"]["ranking"]["churned"]["model"]["roc_auc_ci"] = [
        float("nan"),
        float("nan"),
    ]
    report = model_quality.parse_report(raw)
    assert report["scenarios"]["calendar"]["ranking"]["churned"]["model"][
        "roc_auc_ci"
    ] == [None, None]


def test_response_matches_openapi():
    yaml = pytest.importorskip("yaml")
    jsonschema = pytest.importorskip("jsonschema")
    spec = yaml.safe_load(
        (ROOT / "contracts" / "openapi.yaml").read_text(encoding="utf-8")
    )
    report = model_quality.load(str(RESULTS))
    schema = {
        "$ref": "#/components/schemas/ModelQuality",
        "components": spec["components"],
    }
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(report))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors[:10])
