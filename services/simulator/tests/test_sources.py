"""Демо-источники симулятора: API биллинга (режим pull) и фейковый Logs API Метрики."""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from app.billing_feed import BadCursor, BillingFeed, decode_cursor, encode_cursor
from app.config import Config
from app.controller import Controller
from app.generator import generate
from app.main import create_app
from app.metrica_fake import FakeMetrica, client_id_for, profile_for, visits_for
from app.stream import World

NOW = datetime(2026, 10, 19, 15, tzinfo=UTC)
ADMIN = {"X-User-Name": "dima", "X-User-Roles": "admin"}
FIELDS = "ym:s:visitID,ym:s:dateTime,ym:s:clientID,ym:s:startURL,ym:s:endURL,ym:s:goalsID," \
         "ym:s:parsedParamsKey1,ym:s:parsedParamsKey2"


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def loaded_world(clock: Clock) -> World:
    data = generate(20, 7, NOW)
    world = World(random.Random(5), clock=clock)
    world.load(data["accounts"], data["services"], max(t["id"] for t in data["transactions"]))
    return world


# --- лента биллинга -----------------------------------------------------------------------


def test_feed_pages_by_updated_at_and_cursor():
    clock = Clock()
    world = loaded_world(clock)
    feed = BillingFeed(world, clock)
    feed.reset()
    first = feed.page("accounts", updated_since=None, cursor=None, limit=7)
    assert len(first["items"]) == 7 and first["next_cursor"]
    assert all(item["updatedAt"] == "2026-10-19T15:00:00.000Z" for item in first["items"])
    seen = [i["id"] for i in first["items"]]
    cursor = first["next_cursor"]
    while cursor:
        page = feed.page("accounts", updated_since=None, cursor=cursor, limit=7)
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
    assert seen == sorted(world.accounts)

    clock.now = NOW + timedelta(minutes=1)
    batch = world.tick(5)
    feed.record(batch)
    since = feed.page("accounts", updated_since=NOW + timedelta(seconds=30), cursor=None, limit=1000)
    assert {i["id"] for i in since["items"]} == set(batch.accounts)
    assert since["next_cursor"] is None
    tx = feed.page("transactions", updated_since=None, cursor=None, limit=1000)
    assert [t["id"] for t in tx["items"]] == [t["id"] for t in batch.transactions]


def test_feed_stamps_strictly_increase_and_since_is_inclusive():
    clock = Clock()
    world = loaded_world(clock)
    feed = BillingFeed(world, clock)
    feed.reset()
    feed.record(world.tick(3))  # та же миллисекунда — отметка сдвигается на 1 мс
    stamps = sorted({i["updatedAt"] for i in feed.page("services", updated_since=None, cursor=None, limit=1000)["items"]})
    assert stamps[0] == "2026-10-19T15:00:00.000Z"
    page = feed.page("services", updated_since=NOW, cursor=None, limit=1000)
    assert len(page["items"]) == len(world.services)


def test_cursor_roundtrip_and_garbage():
    assert decode_cursor(encode_cursor("2026-10-19T15:00:00.000Z", 42)) == ("2026-10-19T15:00:00.000Z", 42)
    with pytest.raises(BadCursor):
        decode_cursor("@@@")


# --- режим pull ---------------------------------------------------------------------------


def _config(**overrides) -> Config:  # type: ignore[no-untyped-def]
    return replace(Config.from_env(), ingest_url="http://ingest:8000", running=False, auth_dev_user=None,
                   demo_billing_token="b-token", demo_metrica_token="m-token", **overrides)


async def test_pull_mode_keeps_changes_for_the_connector():
    posted: list[httpx.Request] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: posted.append(r) or httpx.Response(202)))
    clock = Clock()
    ctrl = Controller(_config(mode="pull"), client, world=loaded_world(clock))
    ctrl.feed.reset()
    clock.now = NOW + timedelta(minutes=1)
    changes = await ctrl.step()
    assert changes > 0 and posted == []
    assert ctrl.state()["mode"] == "pull"
    fresh = ctrl.feed.page("accounts", updated_since=NOW + timedelta(seconds=1), cursor=None, limit=1000)
    services = ctrl.feed.page("services", updated_since=NOW + timedelta(seconds=1), cursor=None, limit=1000)
    assert fresh["items"] or services["items"]
    await client.aclose()


async def test_mode_switch_and_billing_endpoint():
    clock = Clock()
    ingest = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(202, json={"accepted": 1})))
    config = _config()
    ctrl = Controller(config, ingest, world=loaded_world(clock))
    ctrl.feed.reset()
    metrica = FakeMetrica(accounts=_ids([1]), anchor=_anchor(NOW), counter_id=100500, processing_s=0)
    app = create_app(config, ctrl, metrica)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sim") as c:
            switched = await c.put("/api/v1/simulator", json={"mode": "pull"}, headers=ADMIN)
            assert switched.json()["mode"] == "pull"
            assert (await c.put("/api/v1/simulator", json={"mode": "both"}, headers=ADMIN)).status_code == 422

            assert (await c.get("/demo/billing/v1/accounts")).status_code == 401
            auth = {"Authorization": "Bearer b-token"}
            page = await c.get("/demo/billing/v1/accounts", params={"limit": 5}, headers=auth)
            assert page.status_code == 200 and len(page.json()["items"]) == 5
            assert "updatedAt" in page.json()["items"][0]
            assert (await c.get("/demo/billing/v1/invoices", headers=auth)).status_code == 404
            assert (await c.get("/demo/billing/v1/accounts", params={"cursor": "@@"}, headers=auth)).status_code == 400
            naive = await c.get("/demo/billing/v1/accounts", params={"updated_since": "2026-10-19T15:00:00"},
                                headers=auth)
            assert naive.status_code == 400

            csv = await c.get("/api/v1/simulator/metrica/client-ids.csv", headers=ADMIN)
            assert csv.text.splitlines() == ["client_id,account_id", f"{client_id_for(1)},1"]
    await ingest.aclose()


# --- фейковая Метрика ---------------------------------------------------------------------


def _ids(ids: list[int]):  # type: ignore[no-untyped-def]
    async def load() -> list[int]:
        return ids

    return load


def _anchor(moment: datetime):  # type: ignore[no-untyped-def]
    async def load() -> datetime:
        return moment

    return load


def test_case_profiles_give_expected_behaviour():
    anchor = date(2026, 10, 19)
    days = [anchor - timedelta(days=n) for n in range(1, 36)]

    def visits(account: int) -> list:
        return [v for d in days for v in visits_for(account, d, anchor)]

    cancel = [v for v in visits(6) if "/delete" in v.end_url]
    assert len(cancel) == 2
    gone = visits(4)
    assert gone and max(v.local for v in gone).date() == anchor - timedelta(days=32)
    fading = visits(1)
    assert sum(1 for v in fading if (anchor - v.local.date()).days < 7) <= 1
    assert sum(1 for v in fading if (anchor - v.local.date()).days >= 7) >= 8
    assert visits(8) == []
    assert profile_for(123456) in {"regular", "fading", "gone", "cancel", "pricing", "none"}
    # детерминированность: повторная выгрузка периода даёт то же самое
    assert visits(6) == visits(6)


async def test_logs_api_flow():
    metrica = FakeMetrica(accounts=_ids([1, 4, 6]), anchor=_anchor(NOW), counter_id=100500, processing_s=0,
                          rows_per_part=10)
    app = create_app(_config(), Controller(_config(), httpx.AsyncClient(), world=World(clock=Clock())), metrica)
    base = "/demo/metrica/management/v1/counter/100500"
    auth = {"Authorization": "OAuth m-token"}
    params = {"date1": "2026-09-14", "date2": "2026-10-18", "fields": FIELDS, "source": "visits"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sim") as c:
        assert (await c.post(f"{base}/logrequests", params=params)).status_code == 403
        assert (await c.post("/demo/metrica/management/v1/counter/1/logrequests", params=params,
                             headers=auth)).status_code == 403
        evaluation = (await c.get(f"{base}/logrequests/evaluate", params=params, headers=auth)).json()
        assert evaluation["log_request_evaluation"]["possible"] is True
        bad = await c.post(f"{base}/logrequests", params={**params, "fields": "ym:s:nope"}, headers=auth)
        assert bad.status_code == 400 and bad.json()["errors"][0]["error_type"] == "invalid_parameter"

        created = (await c.post(f"{base}/logrequests", params=params, headers=auth)).json()["log_request"]
        assert created["status"] == "created"
        rid = created["request_id"]
        info = (await c.get(f"{base}/logrequest/{rid}", headers=auth)).json()["log_request"]
        assert info["status"] == "processed" and len(info["parts"]) >= 2
        lines: list[str] = []
        for part in info["parts"]:
            text = (await c.get(f"{base}/logrequest/{rid}/part/{part['part_number']}/download", headers=auth)).text
            header, *rows = text.strip("\n").split("\n")
            assert header == FIELDS.replace(",", "\t")
            lines += rows
        cancel = [r for r in lines if "/delete" in r]
        assert len(cancel) == 2
        assert all(str(client_id_for(6)) in r for r in cancel)
        assert any("['account_id']\t['6']" in r for r in lines)
        assert all(len(r.split("\t")) == 8 for r in lines)

        cleaned = (await c.post(f"{base}/logrequest/{rid}/clean", headers=auth)).json()["log_request"]
        assert cleaned["status"] == "cleaned_by_user" and cleaned["parts"] == []
        gone = await c.get(f"{base}/logrequest/{rid}/part/0/download", headers=auth)
        assert gone.status_code == 400
