"""Валидация снимка и пакета изменений, права, формат ошибок (без БД)."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime

import pytest

from app.config import settings
from app.main import create_app
from app.repository import snapshot_history_rows
from app.schemas import SERVICE, SourceService
from tests.conftest import ADMIN, ANALYST


def assert_error(resp, status: int, code: str):
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"}
    err = body["error"]
    assert err["code"] == code
    assert isinstance(err["message"], str) and err["message"]
    assert err["request_id"]
    assert resp.headers["x-request-id"] == err["request_id"]
    return err


async def put(client, body, headers=ADMIN):
    return await client.put("/api/v1/source/snapshot", json=body, headers=headers)


async def test_snapshot_requires_user(client, snapshot):
    assert_error(await put(client, snapshot, headers={}), 401, "unauthorized")


async def test_snapshot_requires_admin(client, snapshot):
    assert_error(await put(client, snapshot, headers=ANALYST), 403, "forbidden")


async def test_events_require_admin(client):
    resp = await client.post("/api/v1/source/events", json={}, headers=ANALYST)
    assert_error(resp, 403, "forbidden")


async def test_roles_header_is_case_and_space_tolerant(client):
    resp = await client.post(
        "/api/v1/source/events", json={}, headers={"X-User-Name": "boss", "X-User-Roles": "analyst, Admin"}
    )
    assert resp.status_code == 202


async def test_broken_owner_reference(client, snapshot):
    snapshot["services"][0]["ownerId"] = 999_999
    err = assert_error(await put(client, snapshot), 422, "validation_error")
    assert any(d["type"] == "broken_reference" and d["field"] == "services.0.ownerId" for d in err["details"])


async def test_broken_service_reference(client, snapshot):
    snapshot["transactions"][5]["serviceId"] = 777_777
    err = assert_error(await put(client, snapshot), 422, "validation_error")
    assert any(d["field"] == "transactions.5.serviceId" for d in err["details"])


async def test_duplicate_email_case_insensitive(client, snapshot):
    snapshot["accounts"][1]["email"] = snapshot["accounts"][0]["email"].upper()
    err = assert_error(await put(client, snapshot), 422, "validation_error")
    assert any(d["field"] == "accounts.email" and d["type"] == "duplicate" for d in err["details"])


async def test_duplicate_ids(client, snapshot):
    snapshot["services"].append(dict(snapshot["services"][0]))
    err = assert_error(await put(client, snapshot), 422, "validation_error")
    assert any(d["field"] == "services.id" for d in err["details"])


@pytest.mark.parametrize(
    ("section", "index", "field"),
    [
        ("accounts", 0, "balance"),
        ("accounts", 2, "withdrawBalance"),
        ("accounts", 3, "bonusBalance"),
        ("accounts", 4, "totalReplenished"),
        ("services", 0, "price"),
        ("transactions", 0, "amount"),
    ],
)
async def test_negative_money_rejected(client, snapshot, section, index, field):
    snapshot[section][index][field] = -1
    err = assert_error(await put(client, snapshot), 422, "validation_error")
    assert any(d["field"] == f"{section}.{index}.{field}" for d in err["details"])


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("services", "status", "frozen"),
        ("services", "paymentTerm", "decade"),
        ("accounts", "bonusState", "maybe"),
        ("transactions", "type", "refund"),
        ("services", "createdAt", "вчера"),
        ("accounts", "balance", 10.5),
    ],
)
async def test_bad_enum_or_type(client, snapshot, section, field, value):
    snapshot[section][0][field] = value
    assert_error(await put(client, snapshot), 422, "validation_error")


async def test_expiry_before_creation(client, snapshot):
    snapshot["services"][0]["expiresAt"] = "2020-01-01T00:00:00Z"
    err = assert_error(await put(client, snapshot), 422, "validation_error")
    assert any(d["type"] == "invalid_range" for d in err["details"])


async def test_missing_section(client, snapshot):
    del snapshot["transactions"]
    assert_error(await put(client, snapshot), 422, "validation_error")


async def test_invalid_json(client):
    resp = await client.put(
        "/api/v1/source/snapshot", content=b"{not json", headers={**ADMIN, "Content-Type": "application/json"}
    )
    assert_error(resp, 422, "validation_error")


@pytest.fixture
async def small_limits_client(monkeypatch, app):
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setattr(settings, "max_body_bytes", 64 * 1024)
    monkeypatch.setattr(settings, "max_compressed_body_bytes", 8 * 1024)
    small = create_app(with_lifespan=False)
    small.state.pool = app.state.pool
    small.state.bus = app.state.bus
    async with AsyncClient(transport=ASGITransport(app=small), base_url="http://test") as c:
        yield c


async def test_body_too_large(small_limits_client):
    big = b"[" + b"0," * (64 * 1024 // 2) + b"0]"
    resp = await small_limits_client.put(
        "/api/v1/source/snapshot", content=big, headers={**ADMIN, "Content-Type": "application/json"}
    )
    err = assert_error(resp, 413, "payload_too_large")
    assert "0.0625 МБ" in err["message"]


async def test_gzip_bomb_rejected(small_limits_client):
    bomb = gzip.compress(b" " * (10 * 1024 * 1024), compresslevel=9)
    resp = await small_limits_client.put(
        "/api/v1/source/snapshot", content=bomb, headers={**ADMIN, "Content-Encoding": "gzip"}
    )
    assert_error(resp, 413, "payload_too_large")


async def test_unknown_content_encoding(client, snapshot):
    resp = await client.put(
        "/api/v1/source/snapshot", content=b"x", headers={**ADMIN, "Content-Encoding": "br"}
    )
    assert_error(resp, 415, "unsupported_media_type")


async def test_gzip_snapshot_is_validated(client, snapshot):
    snapshot["services"][0]["ownerId"] = 999_999
    body = gzip.compress(json.dumps(snapshot).encode())
    resp = await client.put(
        "/api/v1/source/snapshot", content=body,
        headers={**ADMIN, "Content-Encoding": "gzip", "Content-Type": "application/json"},
    )
    err = assert_error(resp, 422, "validation_error")
    assert any(d["field"] == "services.0.ownerId" for d in err["details"])


async def test_gzip_events_batch(client, sample, app):
    body = gzip.compress(json.dumps({"accounts": [sample["accounts"][0]] * 2}).encode())
    resp = await client.post(
        "/api/v1/source/events", content=body, headers={**ADMIN, "Content-Encoding": "gzip"}
    )
    err = assert_error(resp, 422, "validation_error")
    assert err["details"][0]["type"] == "duplicate"


async def test_snapshot_schema_error_paths(client, snapshot):
    snapshot["accounts"][2]["withdrawBalance"] = "много"
    err = assert_error(await put(client, snapshot), 422, "validation_error")
    assert err["details"][0]["field"] == "accounts.2.withdrawBalance"
    assert err["details"][0]["location"] == "body"


async def test_snapshot_rejects_non_json_content_type(client, snapshot):
    resp = await client.put(
        "/api/v1/source/snapshot", content=json.dumps(snapshot), headers={**ADMIN, "Content-Type": "text/plain"}
    )
    assert_error(resp, 422, "validation_error")


async def test_events_duplicate_in_batch(client, sample):
    acc = sample["accounts"][0]
    resp = await client.post("/api/v1/source/events", json={"accounts": [acc, acc]}, headers=ADMIN)
    assert_error(resp, 422, "validation_error")


async def test_events_negative_balance(client, sample):
    acc = dict(sample["accounts"][0], balance=-5)
    resp = await client.post("/api/v1/source/events", json={"accounts": [acc]}, headers=ADMIN)
    assert_error(resp, 422, "validation_error")


async def test_events_empty_batch(client, app):
    resp = await client.post("/api/v1/source/events", json={}, headers=ADMIN)
    assert resp.status_code == 202
    assert resp.json() == {"accepted": 0}
    assert app.state.bus.published == []


async def test_request_id_is_echoed(client, snapshot):
    resp = await put(client, snapshot, headers={"X-Request-Id": "req-42"})
    assert resp.json()["error"]["request_id"] == "req-42"


async def test_unknown_route_uses_error_format(client):
    assert_error(await client.get("/nope"), 404, "not_found")


async def test_builtin_docs_disabled(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await client.get(path)).status_code == 404


async def test_metrics_requires_token(client):
    assert_error(await client.get("/metrics"), 401, "unauthorized")
    assert_error(await client.get("/metrics", headers={"Authorization": "Bearer wrong"}), 401, "unauthorized")
    ok = await client.get("/metrics", headers={"Authorization": "Bearer churn-metrics-token"})
    assert ok.status_code == 200
    assert "ingest_snapshots_total" in ok.text
    assert "http_request_duration_seconds" in ok.text


async def test_healthz_is_liveness_only(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}


async def test_readyz_reports_db_failure(client):
    resp = await client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json() == {"status": "fail", "checks": {"db": False, "nats": True}}


def _svc(id_: int, status: str, expires: str) -> SourceService:
    return SERVICE.validate_python(
        {
            "id": id_, "name": f"s{id_}", "ownerId": 1, "paymentTerm": "month", "autoProlong": True,
            "createdAt": "2026-01-01T00:00:00Z", "expiresAt": expires, "status": status, "price": 100,
        }
    )


def test_history_initial_load_estimates_deletions():
    as_of = datetime(2026, 10, 19, 15, tzinfo=UTC)
    rows = snapshot_history_rows(
        {},
        [
            _svc(1, "deleted", "2026-10-01T00:00:00Z"),  # удалена 06.10 — оценка
            _svc(2, "deleted", "2026-10-17T00:00:00Z"),  # 22.10 позже as_of — не пишем
            _svc(3, "active", "2026-11-01T00:00:00Z"),
        ],
        as_of,
    )
    assert rows == [(1, 1, "prolong_wait", "deleted", datetime(2026, 10, 6, tzinfo=UTC), True)]


def test_history_diff_against_previous_snapshot():
    as_of = datetime(2026, 10, 19, 15, tzinfo=UTC)
    rows = snapshot_history_rows(
        {1: (1, "active"), 2: (1, "prolong_wait")},
        [_svc(1, "active", "2026-11-01T00:00:00Z"), _svc(2, "deleted", "2026-10-10T00:00:00Z")],
        as_of,
    )
    assert rows == [(2, 1, "prolong_wait", "deleted", as_of, False)]
