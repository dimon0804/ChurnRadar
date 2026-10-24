"""Логи, настройки, пул и NATS без живой инфраструктуры."""

from __future__ import annotations

import json

import pytest
import structlog

from svckit import bus, db
from svckit.logs import configure_logging, get_logger
from svckit.settings import ServiceSettings


def test_logging_renames_event_and_binds_service(capsys):
    configure_logging("debug", "ingest")
    get_logger().info("hello", x=1)
    record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert record["message"] == "hello" and record["service"] == "ingest" and record["level"] == "info"
    structlog.contextvars.clear_contextvars()


def test_logging_keeps_event_key(capsys):
    configure_logging("warning", event_key="event", force=False)
    log = get_logger("scoring")
    log.info("skipped")
    log.warning("kept")
    lines = capsys.readouterr().out.strip().splitlines()
    record = json.loads(lines[-1])
    assert record["event"] == "kept" and "service" not in record
    assert not any("skipped" in line for line in lines)


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("AUTH_DEV_USER", "admin")
    monkeypatch.setenv("DB_POOL_MAX", "7")
    s = ServiceSettings()
    assert s.auth_dev_user == "admin" and s.db_pool_max == 7
    assert s.metrics_token == "churn-metrics-token"


def test_envelope_shape():
    msg = bus.envelope("source.changed", {"kind": "snapshot"}, "ingest")
    assert set(msg) == {"event_id", "type", "occurred_at", "schema_version", "producer", "payload"}
    assert msg["occurred_at"].endswith("Z") and msg["schema_version"] == 1


async def test_publish_gives_up_without_connection():
    eb = bus.EventBus("nats://127.0.0.1:1", producer="t")
    assert eb.connected is False
    assert await eb.publish("churn.x", "x", {}, retries=1) is None
    await eb.close()


class _FakeJs:
    def __init__(self, exists: bool, race: bool = False) -> None:
        self.exists = exists
        self.race = race
        self.added: list[str] = []

    async def stream_info(self, name: str) -> dict[str, str]:
        if not self.exists:
            raise bus.NotFoundError()
        return {"name": name}

    async def add_stream(self, config) -> None:  # type: ignore[no-untyped-def]
        self.added.append(config.name)
        if self.race:
            self.exists = True
            raise RuntimeError("stream name already in use")
        self.exists = True


async def test_ensure_stream_variants():
    js = _FakeJs(exists=True)
    await bus.ensure_stream(js)  # type: ignore[arg-type]
    assert js.added == []
    js = _FakeJs(exists=False)
    await bus.ensure_stream(js, "S")  # type: ignore[arg-type]
    assert js.added == ["S"]
    js = _FakeJs(exists=False, race=True)
    await bus.ensure_stream(js)  # type: ignore[arg-type]
    assert js.added == ["CHURN"]


class _Conn:
    def __init__(self, value: object) -> None:
        self.value = value

    async def fetchval(self, sql: str) -> object:
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class _Pool:
    def __init__(self, value: object) -> None:
        self.conn = _Conn(value)

    def acquire(self, timeout: float | None = None):  # type: ignore[no-untyped-def]
        conn = self.conn

        class _Ctx:
            async def __aenter__(self) -> _Conn:
                return conn

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _Ctx()


@pytest.mark.parametrize(
    ("pool", "expected"),
    [(None, False), (object(), False), (_Pool(1), True), (_Pool(0), False), (_Pool(OSError("down")), False)],
)
async def test_db_ready(pool, expected):
    assert await db.db_ready(pool) is expected


async def test_create_pool_gives_up_after_timeout():
    with pytest.raises(OSError):
        await db.create_pool("postgresql://u:p@127.0.0.1:1/db", timeout_s=0)


async def test_probes_liveness_and_readiness():
    from fastapi import FastAPI, Request
    from httpx import ASGITransport, AsyncClient

    from svckit.health import install_probes

    state = {"db": False, "nats": True}

    async def readiness(_: Request) -> dict[str, bool]:
        return dict(state)

    app = FastAPI()
    install_probes(app, readiness)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/healthz")).json() == {"status": "ok"}
        resp = await c.get("/readyz")
        assert resp.status_code == 503
        assert resp.json() == {"status": "fail", "checks": {"db": False, "nats": True}}
        state["db"] = True
        resp = await c.get("/readyz")
        assert resp.status_code == 200 and resp.json()["status"] == "ok"
