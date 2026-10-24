"""Системное событие «модель устарела»: по summary["drift"] от scoring, один раз до возврата в норму."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app import repository as repo
from app.config import Config
from app.fanout import Fanout
from app.notify import Snapshot, check_model_drift
from app.rules import feed_path

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)


def drift(status: str, **features: float) -> dict[str, Any]:
    return {"services": 1200, "status": status, "features": features}


def test_check_model_drift_names_moved_features() -> None:
    notice = check_model_drift(drift("alert", prolong_wait=0.41, low_balance=0.31, no_logins=0.12))
    assert notice is not None
    assert notice.event_type == "model_drift"
    assert notice.title == "Модель устарела: 2 признака ушли от эталона"
    assert "prolong_wait, low_balance" in notice.text and "no_logins" not in notice.text
    assert "python -m tools.calibration" in notice.text
    assert notice.lines == ("prolong_wait: PSI 0,41", "low_balance: PSI 0,31")
    assert notice.link == feed_path("model_drift", None) == "/quality"


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        drift("warn", prolong_wait=0.2),
        drift("ok", prolong_wait=0.01),
        {"services": 50, "status": "too_few", "features": {}},
        {"services": 0, "status": "no_reference", "features": {}},
        drift("alert"),  # статус есть, а признаков нет — молчим, а не пишем пустое уведомление
    ],
)
def test_check_model_drift_quiet(value: Any) -> None:
    assert check_model_drift(value) is None


async def _put_summary(conn: Any, value: dict[str, Any] | None) -> None:
    data: dict[str, Any] = {"expected_loss": 1000, "by_level": {"high": 1}, "customers_total": 3}
    if value is not None:
        data["drift"] = value
    await conn.execute(
        "INSERT INTO scoring.summary (id, data, model_version, scored_at) VALUES (1, $1::jsonb, 1, $2) "
        "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, scored_at = EXCLUDED.scored_at",
        json.dumps(data),
        NOW,
    )


async def _scores(fanout: Fanout, conn: Any, now: datetime) -> list[str]:
    cur = Snapshot(now, 1000, 1, 0.1, 3)
    result = await fanout.on_scores(conn, now=now, cur=cur, prev=cur, transitions=[], event_ids=[])
    return [e.kind for e in result.journal]


@pytest.mark.integration
async def test_model_drift_once_until_back_to_normal(db_pool: Any) -> None:
    fanout = Fanout(Config())
    async with db_pool.acquire() as conn:
        await _put_summary(conn, drift("alert", prolong_wait=0.4))
        assert await _scores(fanout, conn, NOW) == ["model_drift"]
        # следующие пересчёты с тем же дрейфом — тишина
        assert await _scores(fanout, conn, NOW + timedelta(minutes=5)) == []
        # маленькая база ничего не доказывает: флаг держим
        await _put_summary(conn, {"services": 10, "status": "too_few", "features": {}})
        assert await _scores(fanout, conn, NOW + timedelta(minutes=10)) == []
        await _put_summary(conn, drift("alert", prolong_wait=0.5))
        assert await _scores(fanout, conn, NOW + timedelta(minutes=15)) == []
        # после перекалибровки PSI вернулся в норму — следующий дрейф снова заметим
        await _put_summary(conn, drift("ok", prolong_wait=0.02))
        assert await _scores(fanout, conn, NOW + timedelta(minutes=20)) == []
        states = await repo.event_states(conn)
        assert states[(repo.JOURNAL_USER, "model_drift")][1] is False
        await _put_summary(conn, drift("alert", low_balance=0.3))
        assert await _scores(fanout, conn, NOW + timedelta(minutes=25)) == ["model_drift"]

        rows = await conn.fetch("SELECT kind, message FROM alerts.feed ORDER BY id")
        assert [r["kind"] for r in rows] == ["model_drift", "model_drift"]
        assert "prolong_wait" in rows[0]["message"] and "low_balance" in rows[1]["message"]


@pytest.mark.integration
async def test_summary_without_drift_is_quiet(db_pool: Any) -> None:
    fanout = Fanout(Config())
    async with db_pool.acquire() as conn:
        await _put_summary(conn, None)
        assert await repo.model_drift(conn) is None
        assert await _scores(fanout, conn, NOW) == []
