"""Валидация снимка и пакета изменений, права, формат ошибок (без БД)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.config import settings
from app.repository import snapshot_history_rows
from app.schemas import SourceService
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


async def test_body_too_large(client):
    big = b"[" + b"0," * (settings.max_body_bytes // 2) + b"0]"
    resp = await client.put(
        "/api/v1/source/snapshot", content=big, headers={**ADMIN, "Content-Type": "application/json"}
    )
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"


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


async def test_healthz_reports_db_failure(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 503
    assert resp.json()["checks"]["db"] is False


def _svc(id_: int, status: str, expires: str) -> SourceService:
    return SourceService.model_validate(
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
