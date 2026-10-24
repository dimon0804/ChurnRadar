"""Монитор дрейфа признаков в пересчёте: summary["drift"], метрики churn_feature_psi*, маркетинг на for_money."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
import riskcore
from prometheus_client import REGISTRY

from app import engine
from app.engine import Scorer
from app.metrics import publish_drift
from conftest import load_snapshot

REFERENCE = datetime(2026, 10, 19, 15, tzinfo=UTC)
LOCK_KEY = 992


def psi_series() -> dict[str, float]:
    """Текущие серии churn_feature_psi из реестра: {feature: value}."""
    return {
        s.labels["feature"]: s.value
        for metric in REGISTRY.collect()
        if metric.name == "churn_feature_psi"
        for s in metric.samples
    }


def test_publish_drift_sets_and_clears_series() -> None:
    publish_drift({"services": 812, "status": "alert", "features": {"prolong_wait": 0.31, "low_balance": 0.02}})
    assert psi_series() == {"prolong_wait": 0.31, "low_balance": 0.02}
    assert REGISTRY.get_sample_value("churn_feature_psi_services") == 812

    # признак выключили — его серия должна исчезнуть, а не застыть на старом значении
    publish_drift({"services": 790, "status": "ok", "features": {"low_balance": 0.03}})
    assert psi_series() == {"low_balance": 0.03}

    # база стала маленькой или эталона нет: PSI не считался, нулей не рисуем
    publish_drift({"services": 40, "status": "too_few", "features": {}})
    assert psi_series() == {}
    assert REGISTRY.get_sample_value("churn_feature_psi_services") == 40
    publish_drift(None)
    assert psi_series() == {}


def test_marketing_reads_money_share_not_score(monkeypatch: pytest.MonkeyPatch) -> None:
    class Result:
        def __init__(self, score: float, money: float) -> None:
            self.risk_score = score
            self._money = money

        def for_money(self) -> Result:
            return Result(self._money, self._money)

    seen: list[float] = []
    monkeypatch.setattr(riskcore, "build_summary", lambda *a: {"customers_total": 2})
    monkeypatch.setattr(riskcore, "build_marketing", lambda results, *a: seen.extend(r.risk_score for r in results))
    engine.build_summary([Result(80, 12.5), Result(40, 3.0)], [], [], riskcore.default_settings(), REFERENCE)
    assert seen == [12.5, 3.0]


def test_drift_failure_does_not_break_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: Any) -> Any:
        raise RuntimeError("битый эталон")

    monkeypatch.setattr(riskcore.drift, "feature_psi", boom)
    monkeypatch.setattr(engine, "build_summary", lambda *a: {"customers_total": 0})
    summary, drift = engine._full_summary([], [], [], riskcore.default_settings(), REFERENCE)
    assert drift is None and "drift" not in summary


def _tiny_reference(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Эталон «все признаки молчат» и порог в одну услугу: на 10 клиентах кейса PSI посчитается."""
    real = riskcore.calibration.tables()
    assert real and real.get("reference"), "model_tables.json без раздела reference"
    codes = list(real["reference"]["features"])
    fake = {**real, "reference": {**real["reference"], "features": {c: [1.0, 0, 0, 0, 0] for c in codes}}}
    monkeypatch.setattr(riskcore.drift, "tables", lambda: fake)
    monkeypatch.setattr(riskcore.drift, "MIN_SERVICES", 1)
    settings = riskcore.default_settings()
    return [c for c in codes if settings.effective_weight(c) > 0]


@pytest.mark.integration
async def test_full_rescore_writes_drift_and_incremental_keeps_it(
    db_pool: Any, sample_data: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    codes = _tiny_reference(monkeypatch)
    async with db_pool.acquire() as conn:
        await load_snapshot(conn, sample_data, REFERENCE)
    scorer = Scorer(db_pool, lock_key=LOCK_KEY)
    result = await scorer.run(full=True)
    assert result is not None
    drift = result.summary["drift"]
    assert drift["status"] in ("ok", "warn", "alert")
    assert drift["services"] > 0
    assert set(drift["features"]) == set(codes)
    worst = max(drift["features"].values())
    assert drift["status"] == ("alert" if worst > 0.25 else "warn" if worst > 0.1 else "ok")
    assert psi_series() == pytest.approx(drift["features"])
    assert REGISTRY.get_sample_value("churn_feature_psi_services") == drift["services"]
    async with db_pool.acquire() as conn:
        stored = json.loads(await conn.fetchval("SELECT data FROM scoring.summary"))
    assert stored["drift"] == drift

    # инкрементальный пересчёт PSI не считает, но и не теряет поле
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE source.accounts SET balance = balance + 1 WHERE id = 1")
    inc = await scorer.run(full=False, account_ids=[1])
    assert inc is not None and not inc.full
    assert inc.summary["drift"] == drift
    async with db_pool.acquire() as conn:
        stored = json.loads(await conn.fetchval("SELECT data FROM scoring.summary"))
    assert stored["drift"] == drift


@pytest.mark.integration
async def test_small_base_gives_too_few_and_no_series(db_pool: Any, sample_data: Any) -> None:
    publish_drift({"services": 900, "status": "alert", "features": {"prolong_wait": 0.4}})
    async with db_pool.acquire() as conn:
        await load_snapshot(conn, sample_data, REFERENCE)
    result = await Scorer(db_pool, lock_key=LOCK_KEY).run(full=True)
    assert result is not None
    assert result.summary["drift"]["status"] == "too_few"
    assert result.summary["drift"]["features"] == {}
    assert psi_series() == {}
