"""Коннекторы без базы: HTTP-повторы, разбор ответов биллинга и TSV Метрики, опции, валидация API."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from app.connectors import billing, metrica
from app.connectors.http import RetryPolicy, SourceClient, SourceError, retry_after_seconds
from app.connectors.models import allowed_hosts, check_base_url, validate_options
from tests.conftest import ADMIN, ANALYST

FAST = RetryPolicy(attempts=3, base_delay_s=0.01, max_delay_s=0.05)


async def no_sleep(_: float) -> None:
    return None


def make_client(handler, **kwargs) -> tuple[SourceClient, httpx.AsyncClient]:
    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SourceClient(raw, "http://billing.test/v1/", source="test", policy=FAST, sleep=no_sleep, **kwargs), raw


# --- HTTP ---------------------------------------------------------------------------------


async def test_retries_on_503_then_succeeds():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"ok": True})

    client, raw = make_client(handler, token="secret-token")
    async with raw:
        assert await client.get_json("/accounts") == {"ok": True}
    assert client.attempts == 3
    assert str(calls[0].url) == "http://billing.test/v1/accounts"
    assert calls[0].headers["Authorization"] == "Bearer secret-token"


async def test_auth_error_is_not_retried_and_hides_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="no")

    client, raw = make_client(handler, token="secret-token")
    async with raw:
        with pytest.raises(SourceError) as info:
            await client.get_json("/accounts")
    assert client.attempts == 1
    assert "токен" in str(info.value)
    assert "secret-token" not in str(info.value)


async def test_timeout_is_retried_until_attempts_run_out():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client, raw = make_client(handler)
    async with raw:
        with pytest.raises(SourceError) as info:
            await client.get_json("/accounts")
    assert client.attempts == 3
    assert info.value.retryable


async def test_retry_after_is_honoured():
    delays: list[float] = []

    async def sleep(seconds: float) -> None:
        delays.append(seconds)

    responses = iter([httpx.Response(429, headers={"Retry-After": "0.04"}), httpx.Response(200, json={})])
    raw = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: next(responses)))
    client = SourceClient(raw, "http://x.test", source="t", policy=RetryPolicy(3, 1.0, 10.0), sleep=sleep)
    async with raw:
        await client.get_json("/a")
    assert client.attempts == 2
    # Retry-After «0.04» не число секунд по RFC — берём экспоненту; целые секунды уважаем
    assert retry_after_seconds("7") == 7.0
    assert retry_after_seconds("Wed, 21 Oct 2026 07:28:00 GMT", datetime(2026, 10, 21, 7, 27, 50, tzinfo=UTC)) == 10.0
    assert retry_after_seconds("garbage") is None
    assert len(delays) == 1


# --- биллинг ------------------------------------------------------------------------------

ACCOUNT = {"id": 1, "email": "a@x.test", "balance": 10, "withdrawBalance": 0, "totalReplenished": 10,
           "bonusBalance": 0, "bonusState": "locked"}


def test_parse_item_requires_updated_at_and_dto_shape():
    item = billing.parse_item("accounts", 0, {**ACCOUNT, "updatedAt": "2026-10-20T10:00:00Z"})
    assert item.record.id == 1 and item.updated_at == datetime(2026, 10, 20, 10, tzinfo=UTC)
    with pytest.raises(SourceError, match="updatedAt"):
        billing.parse_item("accounts", 3, ACCOUNT)
    with pytest.raises(SourceError, match=r"accounts\[2\] не по контракту"):
        billing.parse_item("accounts", 2, {**ACCOUNT, "balance": -5, "updatedAt": "2026-10-20T10:00:00Z"})


async def test_pull_follows_cursor_and_sends_updated_since():
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append({"path": request.url.path, **params})
        if request.url.path.endswith("/accounts") and "cursor" not in params:
            return httpx.Response(200, json={"items": [{**ACCOUNT, "updatedAt": "2026-10-20T10:00:00Z"}],
                                             "next_cursor": "p2"})
        if request.url.path.endswith("/accounts"):
            return httpx.Response(200, json={"items": [{**ACCOUNT, "id": 2, "email": "b@x.test",
                                                        "updatedAt": "2026-10-20T11:00:00Z"}], "next_cursor": None})
        return httpx.Response(200, json={"items": [], "next_cursor": None})

    client, raw = make_client(handler)
    async with raw:
        pulled = await billing.pull(client, {"accounts": "2026-10-20T09:00:00Z"}, page_size=1, max_records=100)
    assert [i.record.id for i in pulled.items["accounts"]] == [1, 2]
    assert seen[0]["updated_since"] == "2026-10-20T09:00:00Z" and seen[0]["limit"] == "1"
    assert seen[1]["cursor"] == "p2"
    assert [s["path"] for s in seen][-2:] == ["/v1/services", "/v1/transactions"]
    assert not pulled.truncated


async def test_pull_stops_at_max_records():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [{**ACCOUNT, "updatedAt": "2026-10-20T10:00:00Z"}],
                                         "next_cursor": "more"})

    client, raw = make_client(handler)
    async with raw:
        pulled = await billing.pull(client, {}, page_size=1, max_records=3)
    assert len(pulled.items["accounts"]) == 3 and pulled.truncated


async def test_pull_rejects_response_without_items():
    client, raw = make_client(lambda r: httpx.Response(200, json={"data": []}))
    async with raw:
        with pytest.raises(SourceError, match="items"):
            await billing.pull(client, {}, page_size=10, max_records=10)


def _item(minutes: int) -> billing.Item:
    return billing.Item(datetime(2026, 10, 20, 10, minutes, tzinfo=UTC), object())


def test_watermark_moves_to_newest_or_waits_for_orphan():
    assert billing._watermark(None, [_item(1), _item(5)], []) == "2026-10-20T10:05:00Z"
    assert billing._watermark("2026-10-20T09:00:00Z", [], []) == "2026-10-20T09:00:00Z"
    # сирота в 10:03 — следующий запуск начнёт с неё
    assert billing._watermark(None, [_item(1), _item(5)], [_item(3)]) == "2026-10-20T10:03:00Z"
    # курсор уже на сироте — не крутимся на месте
    assert billing._watermark("2026-10-20T10:03:00Z", [_item(5)], [_item(3)]) == "2026-10-20T10:05:00Z"


# --- Метрика ------------------------------------------------------------------------------


def test_parse_array_variants():
    assert metrica.parse_array("[]") == []
    assert metrica.parse_array("") == []
    assert metrica.parse_array("[1,22,333]") == ["1", "22", "333"]
    assert metrica.parse_array("['account_id','x']") == ["account_id", "x"]
    assert metrica.parse_array(r"['it\'s','a,b']") == ["it's", "a,b"]


def test_parse_tsv_unescapes_and_flags_broken_rows():
    text = "ym:s:visitID\tym:s:startURL\n1\thttps://x.test/a\\tb\n2\n"
    rows = list(metrica.parse_tsv(text))
    assert rows[0] == {"ym:s:visitID": "1", "ym:s:startURL": "https://x.test/a\tb"}
    assert rows[1] == {}


def _row(dt: str, *, account: str | None = None, client: str = "555", start: str = "https://p.test/services",
         end: str = "https://p.test/services", goals: str = "[]") -> dict[str, str]:
    return {
        "ym:s:visitID": "1",
        "ym:s:dateTime": dt,
        "ym:s:clientID": client,
        "ym:s:startURL": start,
        "ym:s:endURL": end,
        "ym:s:goalsID": goals,
        "ym:s:parsedParamsKey1": "['account_id']" if account else "[]",
        "ym:s:parsedParamsKey2": f"['{account}']" if account else "[]",
    }


def test_aggregate_links_by_param_then_client_id_and_classifies_pages():
    classifier = metrica.Classifier(
        {"cancel": ["/delete"], "payment": ["/billing"], "pricing": ["/pricing"]}, {"payment": [77]}
    )
    rows = [
        _row("2026-10-18 10:00:00", account="7"),
        _row("2026-10-18 23:30:00", account="7", end="https://p.test/services/5/DELETE"),
        _row("2026-10-17 09:00:00", client="555", goals="[77]"),
        _row("2026-10-17 09:00:00", client="999"),  # никому не принадлежит
        {},
    ]
    agg = metrica.aggregate(rows, param="account_id", client_map={555: 8}, classifier=classifier,
                            tz=timedelta(hours=3))
    assert (agg.visits, agg.linked, agg.by_param, agg.by_client_id, agg.bad_rows) == (4, 3, 2, 1, 1)
    day7 = agg.days[(7, date(2026, 10, 18))]
    assert (day7.visits, day7.cancel, day7.payment) == (2, 1, 0)
    # 23:30 по Москве — 20:30 UTC
    assert day7.last_visit_at == datetime(2026, 10, 18, 20, 30, tzinfo=UTC)
    assert day7.last_cancel_at == day7.last_visit_at
    assert agg.days[(8, date(2026, 10, 17))].payment == 1
    assert agg.accounts == 2


def test_period_first_run_and_incremental_with_refresh():
    tz = timedelta(hours=3)
    as_of = datetime(2026, 10, 19, 15, tzinfo=UTC)
    today = date(2026, 10, 24)
    assert metrica.period(as_of, today, {}, lookback=35, refresh=3, tz=tz) == (date(2026, 9, 14), date(2026, 10, 18))
    start, end = metrica.period(as_of, today, {"last_date": "2026-10-18"}, lookback=35, refresh=3, tz=tz)
    assert (start, end) == (date(2026, 10, 16), date(2026, 10, 18))
    # опорное время впереди сегодняшнего дня: Метрика не отдаёт будущее и текущий день
    future = datetime(2026, 12, 1, tzinfo=UTC)
    assert metrica.period(future, today, {}, lookback=2, refresh=3, tz=tz) == (date(2026, 10, 22), date(2026, 10, 23))


# --- опции и адреса -----------------------------------------------------------------------


def test_options_defaults_and_errors():
    assert validate_options("billing_http", {}) == {"page_size": 500, "max_records": 50_000}
    opts = validate_options("yandex_metrica", {"counter_id": 42})
    assert opts["counter_id"] == 42 and opts["lookback_days"] == 35 and "/delete" in opts["pages"]["cancel"]
    with pytest.raises(ValueError, match="page_size"):
        validate_options("billing_http", {"page_size": 5000})
    with pytest.raises(ValueError, match="неизвестные группы"):
        validate_options("yandex_metrica", {"pages": {"blog": ["/blog"]}})
    with pytest.raises(ValueError):
        validate_options("billing_http", {"surprise": 1})


def test_base_url_checks():
    assert check_base_url("https://api-metrika.yandex.net", frozenset()) is None
    assert "http" in check_base_url("ftp://x", frozenset())
    assert "пароль" in check_base_url("https://u:p@x.test", frozenset())
    hosts = allowed_hosts("simulator, api-metrika.yandex.net")
    assert check_base_url("http://simulator:8000/demo", hosts) is None
    assert "CONNECTOR_ALLOWED_HOSTS" in check_base_url("http://169.254.169.254/", hosts)


# --- API без базы -------------------------------------------------------------------------


async def test_connectors_api_requires_admin(client):
    assert (await client.get("/api/v1/source/connectors", headers=ANALYST)).status_code == 403
    assert (await client.get("/api/v1/source/connectors")).status_code == 401


async def test_create_validates_before_db(client):
    bad_id = {"id": "Bad Id", "kind": "billing_http", "title": "x", "base_url": "http://b.test"}
    assert (await client.post("/api/v1/source/connectors", json=bad_id, headers=ADMIN)).status_code == 422
    raw_token = {"id": "b1", "kind": "billing_http", "title": "x", "base_url": "http://b.test",
                 "token_env": "DATABASE_URL"}
    # токен только из переменных CONNECTOR_TOKEN_*: так через коннектор не утечёт DATABASE_URL
    assert (await client.post("/api/v1/source/connectors", json=raw_token, headers=ADMIN)).status_code == 422
    bad_url = {"id": "b1", "kind": "billing_http", "title": "x", "base_url": "file:///etc/passwd"}
    resp = await client.post("/api/v1/source/connectors", json=bad_url, headers=ADMIN)
    assert resp.status_code == 422 and "http" in resp.json()["error"]["message"]
