"""Журнал действий: middleware записывает изменяющие запросы, секреты не попадают в details."""

import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from prometheus_client import CollectorRegistry

from svckit.audit import (
    AuditMiddleware,
    actor_from_headers,
    client_ip,
    make_counter,
    redact,
    rule,
    skip,
)
from svckit.errors import RequestIdMiddleware


class FakePool:
    def __init__(self, fail: bool = False) -> None:
        self.rows: list[tuple] = []
        self.fail = fail

    async def execute(self, sql: str, *args):
        if self.fail:
            raise RuntimeError("db down")
        assert sql.startswith("INSERT INTO audit.events")
        self.rows.append(args)


def build(pool: FakePool, registry: CollectorRegistry | None = None) -> FastAPI:
    app = FastAPI()
    app.state.pool = pool
    app.add_middleware(
        AuditMiddleware,
        service="api",
        rules=[
            rule(
                "PUT",
                r"/api/v1/customers/(?P<id>\d+)/action",
                "customer.action.set",
                "customer",
                object_group="id",
            ),
            skip("POST", r"/api/v1/model/settings/preview"),
        ],
        errors=make_counter(registry) if registry else None,
    )
    app.add_middleware(RequestIdMiddleware)

    @app.put("/api/v1/customers/{cid}/action")
    async def put_action(cid: int, request: Request):
        await request.json()
        return {"ok": True}

    @app.post("/api/v1/model/settings/preview")
    async def preview():
        return {"ok": True}

    @app.post("/api/v1/other")
    async def other():
        return JSONResponse({"error": "no"}, status_code=403)

    @app.post("/api/v1/bad")
    async def bad():
        return JSONResponse({"error": "bad"}, status_code=422)

    @app.get("/api/v1/read")
    async def read():
        return {"ok": True}

    return app


HEADERS = {
    "X-User-Name": "token:aeza-crm",
    "X-User-Roles": "analyst",
    "X-Auth-Method": "token",
    "X-Real-Ip": "10.1.2.3",
}


async def call(app: FastAPI, method: str, path: str, **kw):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(method, path, **kw)


async def test_records_matched_action_with_actor_ip_request_id_and_object():
    pool = FakePool()
    app = build(pool)
    r = await call(
        app,
        "PUT",
        "/api/v1/customers/42/action",
        json={"status": "snoozed", "password": "x"},
        headers={**HEADERS, "X-Request-Id": "rid-1"},
    )
    assert r.status_code == 200
    (row,) = pool.rows
    service, actor, actor_type, role, action, otype, oid, success, ip, rid, details = row
    assert (service, actor, actor_type, role) == ("api", "token:aeza-crm", "token", "analyst")
    assert (action, otype, oid, success, ip, rid) == (
        "customer.action.set",
        "customer",
        "42",
        True,
        "10.1.2.3",
        "rid-1",
    )
    assert json.loads(details) == {"request": {"status": "snoozed", "password": "***"}}


async def test_skips_reads_previews_and_failed_validation_but_records_forbidden_and_unknown():
    pool = FakePool()
    app = build(pool)
    await call(app, "GET", "/api/v1/read", headers=HEADERS)
    await call(app, "POST", "/api/v1/model/settings/preview", headers=HEADERS)
    await call(app, "POST", "/api/v1/bad", headers=HEADERS)
    assert pool.rows == []
    await call(app, "POST", "/api/v1/other", headers={"X-User-Name": "ann"})
    (row,) = pool.rows
    assert row[1:3] == ("ann", "user")
    assert row[4] == "http.post" and row[7] is False
    assert json.loads(row[10]) == {"status": 403, "path": "/api/v1/other"}


async def test_audit_failure_does_not_break_response_and_is_counted():
    registry = CollectorRegistry()
    app = build(FakePool(fail=True), registry)
    r = await call(app, "PUT", "/api/v1/customers/1/action", json={}, headers=HEADERS)
    assert r.status_code == 200
    assert registry.get_sample_value("audit_write_errors_total") == 1


def test_redact_and_headers():
    long = "x" * 1000
    out = redact({"api_token": "cr_x", "nested": {"Secret": 1}, "text": long, "items": list(range(60))})
    assert out["api_token"] == "***" and out["nested"]["Secret"] == "***"
    assert len(out["text"]) < 400 and out["items"][-1] == "… ещё 10"
    assert client_ip({"x-forwarded-for": "1.1.1.1, 2.2.2.2"}, "9.9.9.9") == "1.1.1.1"
    assert client_ip({}, None) is None
    anon = actor_from_headers({})
    assert (anon.name, anon.type) == ("anonymous", "anonymous")
