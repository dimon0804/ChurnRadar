"""Живучесть воркера без NATS и Postgres: повторы, DLQ, отмена, надзор за задачами, публикация."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.config import Config
from app.main import State, shutdown_tasks, supervise
from app.worker import (
    BatchProcessor,
    Worker,
    _consumer_drift,
    _header_safe,
    consumer_config,
    deliveries,
    retry_delay,
)
from test_processor import FakeMsg, FakePool, FakeScorer, _body


class JsMsg(FakeMsg):
    """Сообщение с метаданными JetStream и запоминанием задержки nak."""

    def __init__(self, body: bytes, delivered: int = 1, seq: int = 1, subject: str = "churn.source.changed") -> None:
        super().__init__(body, subject)
        self.metadata = SimpleNamespace(num_delivered=delivered, sequence=SimpleNamespace(stream=seq))
        self.nak_delay: float | None = None

    async def nak(self, delay: float | None = None) -> None:
        await super().nak(delay)
        self.nak_delay = delay


class BrokenPool(FakePool):
    """Обрыв БД на проверке обработанных событий."""

    def acquire(self):  # type: ignore[no-untyped-def]
        raise ConnectionError("connection lost")


class Recorder:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.letters: list[tuple[Any, str]] = []

    async def __call__(self, msg: Any, reason: str) -> bool:
        self.letters.append((msg, reason))
        return self.ok


async def _noop_publish(result: Any) -> None:
    pass


def _processor(pool: Any, scorer: Any, dead_letter: Any = None) -> BatchProcessor:
    return BatchProcessor(
        pool, scorer, _noop_publish, incremental_max=100, dead_letter=dead_letter, max_deliver=3, backoff=(1, 5, 30)
    )


def test_retry_delay_follows_backoff_and_saturates() -> None:
    backoff = (1.0, 5.0, 30.0, 120.0)
    assert [retry_delay(backoff, n) for n in (0, 1, 2, 3, 4, 5, 10)] == [1.0, 1.0, 5.0, 30.0, 120.0, 120.0, 120.0]
    assert retry_delay((), 3) == 0.0


def test_deliveries_without_metadata_is_first() -> None:
    assert deliveries(FakeMsg(b"{}")) == 1
    assert deliveries(JsMsg(b"{}", delivered=7)) == 7


async def test_db_failure_outside_rescore_naks_with_backoff() -> None:
    """Раньше исключение из processed_event_ids убивало consume(); теперь — nak с паузой."""
    scorer = FakeScorer(FakePool())
    processor = _processor(BrokenPool(), scorer)
    msg = JsMsg(_body("changes", [1]), delivered=2)
    assert await processor.handle([msg]) is None
    assert msg.naked and not msg.acked and not msg.termed
    assert msg.nak_delay == 5
    assert scorer.calls == []


async def test_poison_event_goes_to_dlq_after_max_deliver() -> None:
    pool = FakePool()
    letters = Recorder()
    processor = _processor(pool, FakeScorer(pool, fail=True), letters)
    early = JsMsg(_body("changes", [1]), delivered=2)
    last = JsMsg(_body("changes", [2]), delivered=3)
    await processor.handle([early, last])
    assert early.naked and not early.termed and early.nak_delay == 5
    assert last.termed and not last.naked
    assert [m for m, _ in letters.letters] == [last]
    assert "RuntimeError: boom" in letters.letters[0][1]


async def test_dlq_failure_falls_back_to_nak() -> None:
    pool = FakePool()
    processor = _processor(pool, FakeScorer(pool, fail=True), Recorder(ok=False))
    msg = JsMsg(_body("changes", [1]), delivered=5)
    await processor.handle([msg])
    assert msg.naked and not msg.termed
    assert msg.nak_delay == 30


class SlowScorer(FakeScorer):
    def __init__(self, pool: FakePool) -> None:
        super().__init__(pool)
        self.started = asyncio.Event()

    async def run(self, **kwargs: Any) -> Any:
        self.started.set()
        await asyncio.sleep(3600)


async def test_cancel_mid_rescore_naks_immediately_and_propagates() -> None:
    pool = FakePool()
    scorer = SlowScorer(pool)
    processor = _processor(pool, scorer)
    msg = JsMsg(_body("changes", [1]))
    task = asyncio.create_task(processor.handle([msg]))
    await asyncio.wait_for(scorer.started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert msg.naked and not msg.nak_delay and not msg.acked


async def test_supervise_reports_failed_task() -> None:
    state = State()

    async def boom() -> None:
        await asyncio.sleep(0.01)
        raise ConnectionError("db gone")

    async def forever() -> None:
        await asyncio.sleep(3600)

    tasks = [asyncio.create_task(boom(), name="consume"), asyncio.create_task(forever(), name="periodic")]
    assert await asyncio.wait_for(supervise(tasks, asyncio.Event(), state), 2) is True
    assert state.failed_task == "consume"
    await shutdown_tasks(tasks, grace_s=0)
    assert tasks[1].cancelled()


async def test_supervise_stop_signal_and_finished_task() -> None:
    state = State()
    stop = asyncio.Event()

    async def quick() -> None:  # как periodic при RESCORE_INTERVAL_S=0
        return None

    async def forever() -> None:
        await asyncio.sleep(3600)

    tasks = [asyncio.create_task(quick(), name="periodic"), asyncio.create_task(forever(), name="consume")]
    asyncio.get_running_loop().call_later(0.05, stop.set)
    assert await asyncio.wait_for(supervise(tasks, stop, state), 2) is False
    assert state.failed_task is None
    await shutdown_tasks(tasks, grace_s=0)


async def test_shutdown_waits_for_current_batch_within_grace() -> None:
    finished = asyncio.Event()

    async def batch() -> None:
        await asyncio.sleep(0.05)
        finished.set()

    async def stuck() -> None:
        await asyncio.sleep(3600)

    done = asyncio.create_task(batch())
    hung = asyncio.create_task(stuck())
    await asyncio.wait_for(shutdown_tasks([done, hung], grace_s=0.3), 2)
    assert finished.is_set() and not done.cancelled()
    assert hung.cancelled()


class FlakyJs:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls: list[tuple[str, bytes, dict[str, str]]] = []

    async def publish(self, subject: str, data: bytes, headers: dict[str, str], timeout: float) -> None:
        self.calls.append((subject, data, headers))
        if len(self.calls) <= self.failures:
            raise TimeoutError("nats timeout")


def _worker(js: FlakyJs, **overrides: Any) -> Worker:
    base = Config.from_env()
    values = {f: getattr(base, f) for f in base.__dataclass_fields__}
    values.update({"publish_retry_delay_s": 0.0, "publish_attempts": 3, **overrides})
    worker = Worker(Config(**values), FakePool(), FakeScorer(FakePool()))  # type: ignore[arg-type]
    worker.js = js  # type: ignore[assignment]
    return worker


def _result() -> Any:
    from datetime import UTC, datetime

    from app.engine import RunResult

    return RunResult(
        full=True,
        changed_ids=[],
        removed_ids=[],
        summary={"customers_total": 1, "by_level": {}, "expected_loss": 0, "exposure": 0},
        model_version=1,
        scored_at=datetime(2026, 10, 19, 15, tzinfo=UTC),
        duration_ms=1.0,
        customers=1,
    )


async def test_scores_updated_publish_retries_with_same_msg_id() -> None:
    js = FlakyJs(failures=2)
    await _worker(js).publish(_result())
    assert len(js.calls) == 3
    assert len({headers["Nats-Msg-Id"] for _, _, headers in js.calls}) == 1


async def test_publish_gives_up_after_attempts() -> None:
    js = FlakyJs(failures=10)
    await _worker(js).publish(_result())  # не бросает
    assert len(js.calls) == 3


async def test_dead_letter_keeps_body_and_describes_failure() -> None:
    js = FlakyJs(failures=0)
    worker = _worker(js)
    msg = JsMsg(b'{"x": 1}', delivered=10, seq=42)
    assert await worker.dead_letter(msg, "ConnectionError: соединение\nпотеряно") is True
    subject, data, headers = js.calls[0]
    assert subject == "churn.dlq.scoring"
    assert data == b'{"x": 1}'
    assert headers["Nats-Msg-Id"] == "dlq-scoring-42"
    assert headers["Churn-Original-Subject"] == "churn.source.changed"
    assert headers["Churn-Deliveries"] == "10"
    assert headers["Churn-Stream-Seq"] == "42"
    assert "\n" not in headers["Churn-Error"] and headers["Churn-Error"].isascii()


def test_header_safe() -> None:
    assert _header_safe("a\r\nb   c") == "a b c"
    assert _header_safe("x" * 1000, limit=10) == "x" * 10


def test_consumer_drift_detects_old_config() -> None:
    wanted = consumer_config("scoring", 300, 10)
    assert wanted.max_deliver == 11 and not wanted.backoff
    assert _consumer_drift(wanted, wanted) == []
    old = consumer_config("scoring", 300, 10)
    old.max_deliver = -1
    old.backoff = [1.0, 5.0]
    old.filter_subjects = ["churn.source.changed"]
    assert set(_consumer_drift(old, wanted)) == {"max_deliver", "backoff", "filter_subjects"}
