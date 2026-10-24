"""Шаг потока при сбоях ingest: повторы и перезагрузка мира из БД."""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest

from app.config import Config
from app.controller import Controller, IngestError
from app.generator import generate
from app.stream import World

NOW = datetime(2026, 10, 19, 15, tzinfo=UTC)


class Ingest:
    """Отвечает по очереди заданными статусами (или бросает исключение), потом 202."""

    def __init__(self, *answers: int | Exception) -> None:
        self.answers = list(answers)
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        answer = self.answers.pop(0) if self.answers else 202
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(answer, json={"accepted": 1})


class Db:
    """Источник правды вместо Postgres: то, что ingest принял, и счётчик перезагрузок."""

    def __init__(self) -> None:
        data = generate(30, 5, NOW)
        self.accounts = data["accounts"]
        self.services = data["services"]
        self.max_tx = max((t["id"] for t in data["transactions"]), default=0)
        self.loads = 0

    async def load(self, world: World) -> None:
        self.loads += 1
        world.load(self.accounts, self.services, self.max_tx)


def _controller(ingest: Ingest, db: Db | None = None, **overrides) -> Controller:  # type: ignore[no-untyped-def]
    config = replace(
        Config.from_env(),
        ingest_url="http://ingest:8000",
        running=False,
        events_retries=2,
        events_retry_backoff_s=0,
        **overrides,
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(ingest))
    world = World(random.Random(3), clock=lambda: NOW)
    return Controller(config, client, world=world, state_loader=db.load if db else None)


async def test_retries_transient_errors_with_the_same_batch() -> None:
    ingest = Ingest(503, httpx.ReadTimeout("slow"))
    db = Db()
    ctrl = _controller(ingest, db)
    changes = await ctrl.step()
    assert changes >= 1
    assert len(ingest.bodies) == 3
    assert ingest.bodies[0] == ingest.bodies[1] == ingest.bodies[2]
    assert ctrl.world.loaded and db.loads == 1
    assert ctrl.events_emitted == changes


@pytest.mark.parametrize(
    "answers",
    [
        (503, 503, 503),
        (httpx.ConnectError("down"),) * 3,
        (httpx.ReadTimeout("slow"),) * 3,
        (422,),
    ],
    ids=["5xx", "connect", "timeout", "4xx"],
)
async def test_lost_batch_reloads_world_from_db(answers) -> None:  # type: ignore[no-untyped-def]
    ingest = Ingest(*answers)
    db = Db()
    ctrl = _controller(ingest, db)
    with pytest.raises(IngestError):
        await ctrl.step()
    assert len(ingest.bodies) == len(answers)  # 4xx не повторяется
    assert not ctrl.world.loaded
    assert ctrl.events_emitted == 0

    # следующий шаг начинает с состояния БД, а не с потерянного пакета
    assert await ctrl.step() >= 1
    assert db.loads == 2 and ctrl.world.loaded


async def test_lost_batch_without_loader_waits_for_generate() -> None:
    ctrl = _controller(Ingest(500))
    db = Db()
    ctrl.world.load(db.accounts, db.services, db.max_tx)
    with pytest.raises(IngestError):
        await ctrl.step()
    assert not ctrl.world.loaded
    assert await ctrl.step() == 0  # без БД мир пуст до новой генерации


async def test_cancel_during_post_reloads_world() -> None:
    started = asyncio.Event()

    async def hang(request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.sleep(10)
        return httpx.Response(202)

    db = Db()
    config = replace(Config.from_env(), ingest_url="http://ingest:8000", running=False)
    ctrl = Controller(
        config,
        httpx.AsyncClient(transport=httpx.MockTransport(hang)),
        world=World(random.Random(3), clock=lambda: NOW),
        state_loader=db.load,
    )
    task = asyncio.create_task(ctrl.step())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not ctrl.world.loaded
