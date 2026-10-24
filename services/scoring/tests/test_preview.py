"""«Что если»: предпросмотр настроек без записи в БД."""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import riskcore

from app import preview as pv
from app import repository as repo
from app.config import Config
from app.engine import Scorer
from app.main import State, create_http_app
from conftest import load_snapshot

REFERENCE = datetime(2026, 10, 19, 15, tzinfo=UTC)
ADMIN = {"X-User-Name": "admin", "X-User-Roles": "admin"}
ANALYST = {"X-User-Name": "analyst", "X-User-Roles": "analyst"}


def _storage(**weights: float) -> dict[str, Any]:
    data = riskcore.default_settings().to_storage()
    for code, weight in weights.items():
        data["weights"][code]["weight"] = weight
    return data


def _input(sample_data: dict[str, Any], proposed: dict[str, Any], *, total: int | None = None) -> pv._Input:
    accounts, services, transactions = riskcore.parse_snapshot(sample_data)
    exact = total is None
    return pv._Input(
        version=3,
        current=riskcore.merge_settings(riskcore.default_settings().to_storage(), version=3),
        proposed=riskcore.merge_settings(proposed, version=4),
        customers_total=len(accounts) if total is None else total,
        exact=exact,
        source=repo.SourceData(accounts, services, transactions),
        as_of=REFERENCE,
    )


async def _post(app, body: dict[str, Any], headers: dict[str, str] | None = None) -> httpx.Response:  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://s") as c:
        return await c.post("/internal/v1/model/preview", json=body, headers=headers)


# --- без БД -----------------------------------------------------------------------------------


def test_same_settings_change_nothing(sample_data) -> None:  # type: ignore[no-untyped-def]
    result = pv.compute(_input(sample_data, riskcore.default_settings().to_storage()))
    assert result["exact"] is True
    assert result["sample_size"] == result["customers_total"] == 10
    assert result["before"] == result["after"]
    assert result["changes"] == [] and result["changes_total"] == 0


def test_heavier_weight_raises_loss_and_lists_movers(sample_data) -> None:  # type: ignore[no-untyped-def]
    result = pv.compute(_input(sample_data, _storage(short_commitment=100, suspended=100)))
    before, after = result["before"], result["after"]
    assert after["expected_loss"] > before["expected_loss"]
    assert sum(after["by_level"].values()) == 10
    assert result["changes_total"] == len(result["changes"]) > 0
    for change in result["changes"]:
        assert change["level_before"] != change["level_after"]
    # сначала те, кто попадает в высокий риск
    groups = [pv._change_rank(c)[0] for c in result["changes"]]
    assert groups == sorted(groups)
    assert result["into_high"] == sum(1 for c in result["changes"] if c["level_after"] == "high")


def test_thresholds_only_move_levels_not_money(sample_data) -> None:  # type: ignore[no-untyped-def]
    proposed = riskcore.default_settings().to_storage()
    proposed["thresholds"] = {"medium": 1.0, "high": 2.0}
    result = pv.compute(_input(sample_data, proposed))
    assert result["after"]["expected_loss"] == result["before"]["expected_loss"]
    assert result["after"]["by_level"]["high"] >= result["before"]["by_level"]["high"]
    assert all(c["score_before"] == c["score_after"] for c in result["changes"])


def test_sample_is_scaled_to_whole_base(sample_data) -> None:  # type: ignore[no-untyped-def]
    exact = pv.compute(_input(sample_data, _storage(prolong_wait=100)))
    sampled = pv.compute(_input(sample_data, _storage(prolong_wait=100), total=1000))
    assert sampled["exact"] is False
    assert sampled["sample_size"] == 10 and sampled["customers_total"] == 1000
    assert sum(sampled["after"]["by_level"].values()) == 1000
    assert sampled["after"]["expected_loss"] == pytest.approx(exact["after"]["expected_loss"] * 100, abs=100)
    assert sampled["after"]["revenue_at_risk_share"] == exact["after"]["revenue_at_risk_share"]


def test_matches_openapi_schema(sample_data) -> None:  # type: ignore[no-untyped-def]
    jsonschema = pytest.importorskip("jsonschema")
    yaml = pytest.importorskip("yaml")
    from conftest import REPO_ROOT

    openapi = yaml.safe_load((REPO_ROOT / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))
    schema = {"$ref": "#/components/schemas/ModelSettingsPreview", "components": openapi["components"]}
    validator = jsonschema.Draft202012Validator(schema)
    for proposed in (_storage(short_commitment=100, suspended=100), _storage(short_commitment=0)):
        for total in (None, 1000):
            body = {**pv.compute(_input(sample_data, proposed, total=total)), "duration_ms": 1.5}
            errors = [f"{list(e.path)}: {e.message}" for e in validator.iter_errors(body)]
            assert not errors, errors
            assert set(body) == set(openapi["components"]["schemas"]["ModelSettingsPreview"]["properties"])


def test_scale_levels_keeps_total() -> None:
    assert pv._scale_levels({"high": 1, "medium": 1, "low": 1, "unknown": 0}, 1000 / 3, 1000) == {
        "high": 334, "medium": 333, "low": 333, "unknown": 0,
    }
    assert sum(pv._scale_levels({"high": 0, "medium": 0, "low": 0, "unknown": 0}, 5, 0).values()) == 0


async def test_only_latest_waiting_request_is_computed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, Any]] = []
    gate = asyncio.Event()

    async def fake_load(conn, proposed, sample_size):  # type: ignore[no-untyped-def]
        calls.append(dict(proposed))
        await gate.wait()
        return proposed

    class _Pool:
        def acquire(self):  # type: ignore[no-untyped-def]
            class _Ctx:
                async def __aenter__(self):  # type: ignore[no-untyped-def]
                    return None

                async def __aexit__(self, *exc):  # type: ignore[no-untyped-def]
                    return False

            return _Ctx()

    monkeypatch.setattr(pv, "_load", fake_load)
    async def no_key(conn):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(pv, "_snapshot_key", no_key)
    monkeypatch.setattr(
        pv,
        "_compare",
        lambda data, before: (
            {"n": data["n"], "exact": True, "sample_size": 0, "customers_total": 0, "changes_total": 0},
            None,
        ),
    )
    previewer = pv.Previewer(_Pool())  # type: ignore[arg-type]
    first = asyncio.create_task(previewer.preview({"n": 1}))
    await asyncio.sleep(0)
    second = asyncio.create_task(previewer.preview({"n": 2}))
    third = asyncio.create_task(previewer.preview({"n": 3}))
    await asyncio.sleep(0)
    gate.set()
    assert (await first)["n"] == 1
    with pytest.raises(pv.PreviewSuperseded):
        await second
    assert (await third)["n"] == 3
    assert [c["n"] for c in calls] == [1, 3]


async def test_endpoint_requires_admin() -> None:
    state = State()
    app = create_http_app(Config.from_env(), state)
    body = {"settings": riskcore.default_settings().to_storage()}
    assert (await _post(app, body)).status_code == 401
    resp = await _post(app, body, ANALYST)
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "forbidden"
    resp = await _post(app, body, ADMIN)
    assert resp.status_code == 503 and resp.json()["error"]["code"] == "scoring_starting"
    assert (await _post(app, {"weights": {}}, ADMIN)).status_code == 422


async def test_endpoint_maps_superseded_and_timeout() -> None:
    state = State()
    app = create_http_app(Config.from_env(), state)

    class _Previewer:
        def __init__(self, exc: Exception) -> None:
            self.exc = exc

        async def preview(self, settings):  # type: ignore[no-untyped-def]
            raise self.exc

    body = {"settings": riskcore.default_settings().to_storage()}
    state.previewer = _Previewer(pv.PreviewSuperseded())  # type: ignore[assignment]
    resp = await _post(app, body, ADMIN)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "preview_superseded"
    state.previewer = _Previewer(TimeoutError())  # type: ignore[assignment]
    resp = await _post(app, body, ADMIN)
    assert resp.status_code == 504 and resp.json()["error"]["code"] == "preview_timeout"


# --- с Postgres -------------------------------------------------------------------------------


async def _scores(pool: Any) -> dict[int, Any]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT customer_id, risk_score, risk_level, expected_loss FROM scoring.customer_scores"
        )
    return {r["customer_id"]: r for r in rows}


async def _summary(pool: Any) -> dict[str, Any]:
    async with pool.acquire() as conn:
        return json.loads(await conn.fetchval("SELECT data FROM scoring.summary WHERE id = 1"))


async def _save_settings(pool: Any, data: dict[str, Any]) -> None:
    """То же, что делает PATCH в api: новая версия настроек."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE scoring.settings SET version = version + 1, data = $1::jsonb WHERE id = 1", json.dumps(data)
        )


def _assert_totals_match(totals: dict[str, Any], summary: dict[str, Any]) -> None:
    assert totals["expected_loss"] == summary["expected_loss"]
    assert totals["exposure"] == summary["exposure"]
    assert totals["services_at_risk"] == summary["services_at_risk"]
    assert totals["by_level"] == summary["by_level"]
    assert totals["horizon_days"] == summary["horizon_days"]
    assert totals["revenue_at_risk_share"] == pytest.approx(summary["revenue_at_risk_share"], abs=1e-12)


@pytest.mark.integration
@pytest.mark.parametrize(
    "change",
    [
        {"weights": {"short_commitment": 100, "suspended": 100}},
        {"weights": {"short_commitment": 0, "autoprolong_off_expiring": 0}},
        {"thresholds": {"medium": 10.0, "high": 30.0}},
        {"horizon_days": 90},
    ],
    ids=["heavier", "lighter", "thresholds", "horizon"],
)
async def test_preview_matches_real_rescore(db_pool, sample_data, change) -> None:  # type: ignore[no-untyped-def]
    """Ключевая проверка: превью совпадает с тем, что получится после сохранения и пересчёта."""
    async with db_pool.acquire() as conn:
        await load_snapshot(conn, sample_data, REFERENCE)
    scorer = Scorer(db_pool, lock_key=992)
    await scorer.run(full=True)
    before_scores, before_summary = await _scores(db_pool), await _summary(db_pool)

    proposed = riskcore.default_settings().to_storage()
    for code, weight in change.get("weights", {}).items():
        proposed["weights"][code]["weight"] = float(weight)
    if "thresholds" in change:
        proposed["thresholds"] = change["thresholds"]
    if "horizon_days" in change:
        proposed["horizon_days"] = change["horizon_days"]

    state = State()
    state.previewer = pv.Previewer(db_pool)
    app = create_http_app(Config.from_env(), state)
    resp = await _post(app, {"settings": proposed}, ADMIN)
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["exact"] is True and result["model_version"] == 1

    # превью ничего не записало
    assert await _scores(db_pool) == before_scores
    assert await _summary(db_pool) == before_summary
    async with db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT version FROM scoring.settings WHERE id = 1") == 1

    _assert_totals_match(result["before"], before_summary)

    await _save_settings(db_pool, proposed)
    await scorer.run(full=True)
    after_scores, after_summary = await _scores(db_pool), await _summary(db_pool)
    _assert_totals_match(result["after"], after_summary)

    moved = {c["id"]: c for c in result["changes"]}
    assert result["changes_total"] == len(moved)
    for cid, row in after_scores.items():
        was = before_scores[cid]
        if cid in moved:
            c = moved[cid]
            assert (c["level_before"], c["level_after"]) == (was["risk_level"], row["risk_level"])
            assert c["expected_loss_before"] == was["expected_loss"]
            assert c["expected_loss_after"] == row["expected_loss"]
            assert c["score_after"] == pytest.approx(row["risk_score"]) if row["risk_score"] is not None \
                else c["score_after"] is None
        else:
            assert row["risk_level"] == was["risk_level"], f"клиент {cid} сменил уровень, но его нет в превью"


@pytest.mark.integration
async def test_preview_sample_on_bigger_base(db_pool, sample_data) -> None:  # type: ignore[no-untyped-def]
    async with db_pool.acquire() as conn:
        await load_snapshot(conn, sample_data, REFERENCE)
    proposed = _storage(prolong_wait=100)
    sampled = await pv.Previewer(db_pool, sample_size=4).preview(copy.deepcopy(proposed))
    assert sampled["exact"] is False
    assert sampled["sample_size"] == 4 and sampled["customers_total"] == 10
    assert sum(sampled["after"]["by_level"].values()) == 10
    # одна и та же выборка от запроса к запросу
    again = await pv.Previewer(db_pool, sample_size=4).preview(copy.deepcopy(proposed))
    assert again["after"] == sampled["after"] and again["changes"] == sampled["changes"]


@pytest.mark.integration
async def test_snapshot_is_reused_until_next_rescore(db_pool, sample_data, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async with db_pool.acquire() as conn:
        await load_snapshot(conn, sample_data, REFERENCE)
    scorer = Scorer(db_pool, lock_key=993)
    await scorer.run(full=True)

    loads = 0
    real_load = pv._load

    async def counting_load(*args):  # type: ignore[no-untyped-def]
        nonlocal loads
        loads += 1
        return await real_load(*args)

    monkeypatch.setattr(pv, "_load", counting_load)
    previewer = pv.Previewer(db_pool)
    first = await previewer.preview(_storage(short_commitment=100))
    second = await previewer.preview(_storage(short_commitment=0))
    assert loads == 1  # ползунок двигается — снимок и «было» те же
    assert second["before"] == first["before"]
    assert second["after"] != first["after"]
    # прямой расчёт без кэша даёт то же самое
    fresh = await pv.Previewer(db_pool).preview(_storage(short_commitment=0))
    assert {k: v for k, v in fresh.items() if k != "duration_ms"} == {
        k: v for k, v in second.items() if k != "duration_ms"
    }

    # данные изменились и пересчитались — снимок перечитывается
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE source.accounts SET balance = balance + 1000 WHERE id = 1")
    await scorer.run(full=True)
    loads = 0
    await previewer.preview(_storage(short_commitment=0))
    assert loads == 1
