"""Каналы и фоновая доставка без сети и БД: httpx.MockTransport и подменённый repository."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app import delivery as delivery_mod
from app import repository as repo
from app.channels import DeliveryError, TelegramChannel, WebhookChannel, build_channels, sign
from app.config import Config
from app.delivery import Dispatcher, backoff_delay
from app.metrics import DROPPED, FAILED, SENT


def client_for(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_webhook_signs_and_sends_batch_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    async with client_for(handler) as client:
        await WebhookChannel(client, "http://hook/x", secret="s3").send({"text": "привет"}, batch_id="b1")
    req = seen[0]
    assert req.headers["X-ChurnRadar-Batch-Id"] == "b1"
    assert req.headers["X-ChurnRadar-Signature"] == sign("s3", req.content)
    assert json.loads(req.content) == {"text": "привет"}


@pytest.mark.parametrize(
    ("status", "retryable"), [(500, True), (503, True), (429, True), (408, True), (404, False), (400, False)]
)
async def test_http_errors_classified(status: int, retryable: bool) -> None:
    async with client_for(lambda _: httpx.Response(status, text="nope")) as client:
        with pytest.raises(DeliveryError) as info:
            await WebhookChannel(client, "http://hook").send({}, batch_id="b")
    assert info.value.retryable is retryable


async def test_network_error_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with client_for(handler) as client:
        with pytest.raises(DeliveryError) as info:
            await WebhookChannel(client, "http://hook").send({}, batch_id="b")
    assert info.value.retryable


async def test_telegram_request_and_no_token_in_errors() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    async with client_for(handler) as client:
        await TelegramChannel(client, "123:SECRET", "-100", api_url="http://tg/").send(
            {"text": "x" * 5000}, batch_id="b"
        )
    assert str(seen[0].url) == "http://tg/bot123:SECRET/sendMessage"
    body = json.loads(seen[0].content)
    assert body["chat_id"] == "-100" and len(body["text"]) == 4000

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    async with client_for(broken) as client:
        with pytest.raises(DeliveryError) as info:
            await TelegramChannel(client, "123:SECRET", "-100", api_url="http://tg").send({"text": "x"}, batch_id="b")
    assert "SECRET" not in str(info.value)


def test_channels_enabled_only_when_configured() -> None:
    client = httpx.AsyncClient()
    assert build_channels(Config(), client) == {}
    assert set(build_channels(Config(telegram_bot_token="t"), client)) == set()  # без chat id канал выключен
    both = build_channels(Config(webhook_url="http://h", telegram_bot_token="t", telegram_chat_id="1"), client)
    assert set(both) == {"webhook", "telegram"}


def test_backoff_delay() -> None:
    assert [backoff_delay((1, 5, 30), n) for n in (1, 2, 3, 4, 9)] == [1, 5, 30, 30, 30]


class FakeChannel:
    name = "webhook"

    def __init__(self, errors: list[DeliveryError | None]) -> None:
        self.errors = errors
        self.calls = 0

    async def send(self, payload: dict[str, Any], *, batch_id: str) -> None:
        self.calls += 1
        error = self.errors.pop(0) if self.errors else None
        if error is not None:
            raise error


@pytest.fixture
def fake_repo(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"queue": [], "sent": [], "failed": []}

    async def claim(pool: Any, limit: int, lease_s: float) -> list[repo.Delivery]:
        batch, state["queue"] = state["queue"][:limit], state["queue"][limit:]
        return batch

    async def mark_sent(pool: Any, delivery_id: int) -> None:
        state["sent"].append(delivery_id)

    async def mark_failed(pool: Any, delivery_id: int, error: str, *, retry_in_s: float | None) -> None:
        state["failed"].append((delivery_id, retry_in_s))

    monkeypatch.setattr(delivery_mod.repo, "claim_deliveries", claim)
    monkeypatch.setattr(delivery_mod.repo, "mark_sent", mark_sent)
    monkeypatch.setattr(delivery_mod.repo, "mark_failed", mark_failed)
    return state


def _delivery(attempts: int = 0, channel: str = "webhook") -> repo.Delivery:
    return repo.Delivery(id=attempts + 1, channel=channel, payload={"text": "t"}, attempts=attempts, batch_id="b")


async def test_dispatcher_retries_then_gives_up(fake_repo: dict[str, Any]) -> None:
    failed_before = FAILED.labels(channel="webhook")._value.get()
    dropped_before = DROPPED.labels(channel="webhook")._value.get()
    channel = FakeChannel([DeliveryError("down"), DeliveryError("down")])
    dispatcher = Dispatcher(None, {"webhook": channel}, backoff_s=(1, 5), max_attempts=3)  # type: ignore[arg-type]

    fake_repo["queue"] = [_delivery(attempts=0)]
    await dispatcher.run_once()
    assert fake_repo["failed"][-1] == (1, 1)  # первая неудача — пауза 1 с

    fake_repo["queue"] = [_delivery(attempts=2)]
    await dispatcher.run_once()
    assert fake_repo["failed"][-1] == (3, None)  # третья попытка из трёх — бросаем
    assert FAILED.labels(channel="webhook")._value.get() - failed_before == 2
    assert DROPPED.labels(channel="webhook")._value.get() - dropped_before == 1


async def test_dispatcher_success_and_permanent_error(fake_repo: dict[str, Any]) -> None:
    sent_before = SENT.labels(channel="webhook")._value.get()
    channel = FakeChannel([None, DeliveryError("404", retryable=False)])
    dispatcher = Dispatcher(None, {"webhook": channel}, max_attempts=8)  # type: ignore[arg-type]
    fake_repo["queue"] = [_delivery(attempts=0), _delivery(attempts=1)]
    assert await dispatcher.run_once() == 2
    assert fake_repo["sent"] == [1]
    assert fake_repo["failed"] == [(2, None)]
    assert SENT.labels(channel="webhook")._value.get() - sent_before == 1


async def test_dispatcher_drops_disabled_channel(fake_repo: dict[str, Any]) -> None:
    dispatcher = Dispatcher(None, {})  # type: ignore[arg-type]
    fake_repo["queue"] = [_delivery(channel="telegram")]
    await dispatcher.run_once()
    assert fake_repo["failed"] == [(1, None)]
