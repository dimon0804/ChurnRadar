from __future__ import annotations

import random
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from app.generator import generate
from app.stream import MUTATION_WEIGHTS, Batch, World

REF = datetime(2026, 10, 19, 15, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = REF

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def world() -> tuple[World, Clock]:
    clock = Clock()
    data = generate(300, 5, REF)
    w = World(random.Random(1), clock=clock, demo_day_seconds=10)
    w.load(data["accounts"], data["services"], max(t["id"] for t in data["transactions"]))
    return w, clock


def test_batches_are_valid_source_change_batches(world, openapi_validator) -> None:  # type: ignore[no-untyped-def]
    w, clock = world
    validator = openapi_validator("SourceChangeBatch")
    known_accounts = set(w.accounts)
    known_services = set(w.services)
    tx_ids: list[int] = []
    kinds: Counter[str] = Counter()
    for _ in range(400):
        clock.now += timedelta(seconds=2)
        batch = w.tick(5)
        payload = batch.to_payload()
        assert not list(validator.iter_errors(payload))
        known_accounts |= {a["id"] for a in payload["accounts"]}
        known_services |= {s["id"] for s in payload["services"]}
        assert {s["ownerId"] for s in payload["services"]} <= known_accounts
        assert {t["serviceId"] for t in payload["transactions"]} <= known_services
        tx_ids += [t["id"] for t in payload["transactions"]]
        kinds.update(batch.kinds)
        for a in payload["accounts"]:
            assert a["balance"] >= 0 and a["bonusBalance"] >= 0
            assert a["withdrawBalance"] <= a["balance"]
    assert len(tx_ids) == len(set(tx_ids))
    assert tx_ids == sorted(tx_ids)
    assert set(MUTATION_WEIGHTS) <= set(kinds)
    assert kinds["delete"] > 0


def test_tick_makes_one_to_five_changes(world) -> None:  # type: ignore[no-untyped-def]
    w, _ = world
    for _ in range(50):
        assert 1 <= len(w.tick(5).kinds) <= 5


def test_prolong_wait_deleted_after_five_demo_days(world) -> None:  # type: ignore[no-untyped-def]
    w, clock = world
    service = next(s for s in w.services.values() if s["status"] == "active" and not s["autoProlong"])
    w._to_wait(service, clock())
    clock.now += timedelta(seconds=49)
    w._expire_waiting(Batch())
    assert service["status"] == "prolong_wait"
    clock.now += timedelta(seconds=2)
    batch = Batch()
    w._expire_waiting(batch)
    assert service["status"] == "deleted"
    assert service["id"] in batch.services


def test_renewal_without_money_moves_to_prolong_wait(world) -> None:  # type: ignore[no-untyped-def]
    from app.stream import Batch

    w, _ = world
    service = next(s for s in w.services.values() if s["status"] == "active")
    service["autoProlong"] = True
    account = w.accounts[service["ownerId"]]
    account.update(balance=0, bonusBalance=0, withdrawBalance=0)
    w.service_ids = [service["id"]]
    batch = Batch()
    assert w._m_renewal(batch)
    assert service["status"] == "prolong_wait"
    assert batch.transactions[-1]["status"] == "not_performed"
    assert int(service["id"]) in w.deadlines


def test_renewal_with_money_extends_expiry(world) -> None:  # type: ignore[no-untyped-def]
    from app.stream import Batch
    from app.timeutil import parse_iso

    w, _ = world
    service = next(s for s in w.services.values() if s["status"] == "active")
    service["autoProlong"] = True
    account = w.accounts[service["ownerId"]]
    account["balance"] = service["price"] * 3
    before_expiry = parse_iso(service["expiresAt"])
    before_balance = account["balance"]
    w.service_ids = [service["id"]]
    batch = Batch()
    assert w._m_renewal(batch)
    assert parse_iso(service["expiresAt"]) > before_expiry
    assert account["balance"] == before_balance - service["price"]
    assert batch.transactions[-1]["status"] == "performed"


def test_empty_world_produces_nothing() -> None:
    w = World(random.Random(1), clock=Clock())
    assert w.tick().empty
