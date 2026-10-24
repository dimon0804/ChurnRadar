"""Лимиты тела и распаковка gzip на маленьком приложении FastAPI."""

from __future__ import annotations

import gzip
import json
import random
import zlib
from dataclasses import field

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from pydantic import TypeAdapter
from pydantic.dataclasses import dataclass

from svckit.body import RequestBodyMiddleware, is_json_content_type, parse_json_body, read_body
from svckit.errors import RequestIdMiddleware, install_error_handlers

LIMIT = 64 * 1024
COMPRESSED_LIMIT = 8 * 1024


@dataclass(slots=True, kw_only=True)
class Item:
    id: int
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class Batch:
    items: list[Item]


BATCH = TypeAdapter(Batch)


def build_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(RequestBodyMiddleware, max_bytes=LIMIT, max_compressed_bytes=COMPRESSED_LIMIT)
    app.add_middleware(RequestIdMiddleware)

    @app.post("/raw")
    async def raw(request: Request) -> dict[str, int]:
        body = await read_body(request)
        return {"size": len(body)}

    @app.post("/batch")
    async def batch(request: Request) -> dict[str, int]:
        parsed = parse_json_body(request, await read_body(request), BATCH)
        return {"items": len(parsed.items)}

    @app.post("/model")
    async def model(body: Batch) -> dict[str, int]:
        return {"items": len(body.items)}

    return app


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=build_app()), base_url="http://t") as c:
        yield c


def assert_error(resp, status: int, code: str) -> dict:
    assert resp.status_code == status, resp.text
    err = resp.json()["error"]
    assert err["code"] == code
    assert err["message"]
    assert err["request_id"] == resp.headers["x-request-id"]
    return err


GZ = {"Content-Encoding": "gzip", "Content-Type": "application/json"}


async def test_plain_body_passes(client):
    resp = await client.post("/raw", content=b"x" * 1000)
    assert resp.json() == {"size": 1000}


async def test_gzip_is_inflated(client):
    payload = json.dumps({"items": [{"id": i} for i in range(100)]}).encode()
    resp = await client.post("/batch", content=gzip.compress(payload), headers=GZ)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items": 100}


async def test_gzip_reaches_fastapi_body_params(client):
    payload = json.dumps({"items": [{"id": 1}, {"id": 2}]}).encode()
    resp = await client.post("/model", content=gzip.compress(payload), headers=GZ)
    assert resp.json() == {"items": 2}


async def test_multi_member_gzip(client):
    data = b"a" * 3000
    resp = await client.post("/raw", content=gzip.compress(data) + gzip.compress(data), headers=GZ)
    assert resp.json() == {"size": 6000}


async def test_chunked_gzip_stream(client):
    blob = gzip.compress(b"z" * 50_000)

    async def chunks():
        for i in range(0, len(blob), 7):
            yield blob[i : i + 7]

    resp = await client.post("/raw", content=chunks(), headers={"Content-Encoding": "gzip"})
    assert resp.json() == {"size": 50_000}


async def test_plain_over_limit_by_header(client):
    err = assert_error(await client.post("/raw", content=b"x" * (LIMIT + 1)), 413, "payload_too_large")
    assert "МБ" in err["message"]


async def test_plain_over_limit_streamed(client):
    async def chunks():
        for _ in range(LIMIT // 1024 + 2):
            yield b"x" * 1024

    assert_error(await client.post("/raw", content=chunks()), 413, "payload_too_large")


async def test_compressed_over_limit(client):
    blob = gzip.compress(random.Random(1).randbytes(COMPRESSED_LIMIT * 2))  # случайное не сжимается
    assert len(blob) > COMPRESSED_LIMIT
    err = assert_error(await client.post("/raw", content=blob, headers=GZ), 413, "payload_too_large")
    assert "Сжатое" in err["message"]


@pytest.mark.parametrize("path", ["/raw", "/model"])
async def test_gzip_bomb_is_cut(client, path):
    bomb = gzip.compress(b"\0" * (LIMIT * 50), compresslevel=9)
    assert len(bomb) < COMPRESSED_LIMIT
    err = assert_error(await client.post(path, content=bomb, headers=GZ), 413, "payload_too_large")
    assert "распаковки" in err["message"]


async def test_broken_gzip(client):
    assert_error(await client.post("/raw", content=b"not gzip at all", headers=GZ), 400, "bad_request")


async def test_truncated_gzip(client):
    blob = gzip.compress(b"y" * 5000)[:-10]
    assert_error(await client.post("/raw", content=blob, headers=GZ), 400, "bad_request")


async def test_unsupported_encoding(client):
    resp = await client.post("/raw", content=zlib.compress(b"x"), headers={"Content-Encoding": "br"})
    assert_error(resp, 415, "unsupported_media_type")


async def test_validation_errors_look_like_fastapi(client):
    resp = await client.post("/batch", json={"items": [{"id": "x"}]})
    err = assert_error(resp, 422, "validation_error")
    assert err["details"][0]["field"] == "items.0.id"
    assert err["details"][0]["location"] == "body"


async def test_invalid_json(client):
    resp = await client.post("/batch", content=b"{nope", headers={"Content-Type": "application/json"})
    assert_error(resp, 422, "validation_error")


async def test_non_json_content_type(client):
    resp = await client.post("/batch", content=b"{}", headers={"Content-Type": "text/plain"})
    assert_error(resp, 422, "validation_error")


def test_json_content_types():
    assert is_json_content_type(None)
    assert is_json_content_type("application/json; charset=utf-8")
    assert is_json_content_type("application/merge-patch+json")
    assert not is_json_content_type("text/plain")
    assert not is_json_content_type("application/xml")
