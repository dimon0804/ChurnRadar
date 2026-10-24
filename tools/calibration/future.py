"""Генерация базы с известными сегментами и прогон «будущего» на горизонт.

Два сценария будущего:

* ``stream`` — живой поток симулятора (``stream.World.tick``) без изменений, только время
  идёт по модельным часам, а один демо-день равен настоящим суткам. Частота изменений
  подобрана под размер базы (см. ``stream_changes_per_tick``).
* ``calendar`` — услуги продлеваются точно в день окончания срока. Решение клиента
  (заплатить, пополнить баланс, отказаться) зависит от скрытого поведения его сегмента,
  которое модель не видит. Механика списаний, ожидания оплаты и удаления через 5 дней
  берётся из ``stream.World``, фоновые изменения (пополнения, переключение автопродления,
  выключение и включение серверов) — те же функции живого потока.
"""

from __future__ import annotations

import heapq
import random
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ._deps import generator, stream, timeutil

LIVE = frozenset(stream.LIVE)
GONE = frozenset({"deleted", "blocked"})
GRACE = timedelta(days=5)
DAY = timedelta(days=1)
PERIOD_DAYS = {"hour": 1 / 24, "week": 7.0, "month": 30.0, "quarter_year": 91.0, "half_year": 182.0, "year": 365.0}

# Фоновые изменения сценария calendar: те же функции и веса, что в живом потоке.
BACKGROUND_KINDS = ("topup", "autoprolong_off", "autoprolong_on", "suspend", "resume")


# --- генерация ------------------------------------------------------------------------------------


@contextmanager
def _recording_segments() -> Iterator[dict[int, str]]:
    """Подменяет построитель генератора на время вызова и запоминает сегмент владельца услуги.

    Набор данных от этого не меняется: подкласс не трогает генератор случайных чисел.
    Клиенты без услуг (сегмент «новые») в словарь не попадают, модель их и не оценивает.
    """
    seen: dict[int, str] = {}
    original = generator._Builder

    class Recording(original):  # type: ignore[misc, valid-type]
        def service(self, owner_id: int, seg: Any) -> tuple[int, float]:
            seen.setdefault(owner_id, seg.name)
            return super().service(owner_id, seg)

    generator._Builder = Recording
    try:
        yield seen
    finally:
        generator._Builder = original


def generate_with_segments(accounts: int, seed: int, reference: datetime) -> tuple[dict[str, Any], dict[int, str]]:
    with _recording_segments() as seen:
        data = generator.generate(accounts, seed, reference)
    return data, dict(seen)


# --- часы и результат -----------------------------------------------------------------------------


class SimClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@dataclass(slots=True)
class FutureResult:
    scenario: str
    final_status: dict[int, str]
    events: Counter[str] = field(default_factory=Counter)


def _live_at_start(services: list[dict[str, Any]]) -> list[int]:
    return [int(s["id"]) for s in services if s["status"] in LIVE]


def _max_tx_id(data: Mapping[str, Any]) -> int:
    return max((int(t["id"]) for t in data["transactions"]), default=0)


# --- сценарий stream ------------------------------------------------------------------------------


def stream_changes_per_tick(live_services: int, changes_per_service_day: float, step: timedelta) -> int:
    """Параметр max_changes для World.tick, чтобы в среднем шло нужное число изменений за шаг.

    tick делает randint(1, max_changes) изменений, среднее (1 + max_changes) / 2.
    """
    mean = live_services * changes_per_service_day * step / DAY
    return max(1, round(2 * mean - 1))


def simulate_stream(
    data: Mapping[str, Any],
    *,
    seed: int,
    horizon_days: int,
    changes_per_service_day: float,
    step: timedelta = timedelta(hours=1),
) -> FutureResult:
    start = timeutil.parse_iso(data["reference_time"])
    clock = SimClock(start)
    world = stream.World(random.Random(seed), clock=clock, demo_day_seconds=DAY.total_seconds())
    world.load(data["accounts"], data["services"], _max_tx_id(data))
    tracked = _live_at_start(data["services"])
    max_changes = stream_changes_per_tick(len(tracked), changes_per_service_day, step)
    events: Counter[str] = Counter()
    end = start + timedelta(days=horizon_days)
    while clock.now + step <= end:
        clock.advance(step)
        events.update(world.tick(max_changes).kinds)
    return FutureResult("stream", {sid: world.services[sid]["status"] for sid in tracked}, events)


# --- сценарий calendar ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Behavior:
    """Скрытое поведение клиента. Вероятности manual, topup и cancel заданы на 30 дней."""

    manual: float  # продлит вручную при выключенном автопродлении
    topup: float  # пополнит баланс, если на автопродление не хватает
    wait_pay: float  # оплатит ждущую услугу, вероятность за сутки
    cancel: float  # откажется от услуги при продлении независимо от денег


SEGMENT_BEHAVIOR: dict[str, Behavior] = {
    "loyal": Behavior(manual=0.90, topup=0.90, wait_pay=0.35, cancel=0.01),
    "risky": Behavior(manual=0.55, topup=0.50, wait_pay=0.15, cancel=0.05),
    "churning": Behavior(manual=0.20, topup=0.15, wait_pay=0.05, cancel=0.15),
    "newcomer": Behavior(manual=0.60, topup=0.60, wait_pay=0.20, cancel=0.03),
}
DEFAULT_BEHAVIOR = SEGMENT_BEHAVIOR["newcomer"]


def _beta(rng: random.Random, mean: float, concentration: float) -> float:
    if concentration <= 0:
        return mean
    return rng.betavariate(mean * concentration, (1 - mean) * concentration)


def draw_behavior(segments: Mapping[int, str], rng: random.Random, concentration: float) -> dict[int, Behavior]:
    """Индивидуальное поведение: Beta вокруг среднего сегмента. concentration ≤ 0 — без разброса."""
    result: dict[int, Behavior] = {}
    for account_id in sorted(segments):
        base = SEGMENT_BEHAVIOR[segments[account_id]]
        result[account_id] = Behavior(
            manual=_beta(rng, base.manual, concentration),
            topup=_beta(rng, base.topup, concentration),
            wait_pay=_beta(rng, base.wait_pay, concentration),
            cancel=_beta(rng, base.cancel, concentration),
        )
    return result


class CalendarWorld(stream.World):
    def __init__(self, rng: random.Random, clock: Callable[[], datetime], behavior: Mapping[int, Behavior]) -> None:
        super().__init__(rng, clock=clock, demo_day_seconds=DAY.total_seconds())
        self.behavior = behavior
        self.expiry: dict[int, datetime] = {}
        self.queue: list[tuple[datetime, int]] = []
        self.leaving: set[int] = set()
        self.events: Counter[str] = Counter()

    def load(self, accounts: list[dict[str, Any]], services: list[dict[str, Any]], max_tx_id: int) -> None:
        super().load(accounts, services, max_tx_id)
        # По регламенту ждущую услугу удаляют через 5 дней после окончания срока.
        for sid, service in self.services.items():
            expires = timeutil.parse_iso(service["expiresAt"])
            if service["status"] == "prolong_wait":
                self.deadlines[sid] = expires + GRACE
            elif service["status"] in ("active", "suspended"):
                self._schedule(sid, expires)

    def _schedule(self, sid: int, expires: datetime) -> None:
        self.expiry[sid] = expires
        heapq.heappush(self.queue, (expires, sid))

    def _who(self, service: dict[str, Any]) -> Behavior:
        return self.behavior.get(int(service["ownerId"]), DEFAULT_BEHAVIOR)

    def _top_up(self, account: dict[str, Any], price: int) -> None:
        bonus = account["bonusBalance"] if account["bonusState"] == "unlocked" else 0
        shortfall = max(0, price - account["balance"] - bonus)
        if shortfall:
            amount = shortfall + self.rng.choice((0, 500, 1000, 2000))
            account["balance"] += amount
            account["totalReplenished"] += amount

    def _renew(self, batch: stream.Batch, service: dict[str, Any], start: datetime, now: datetime) -> None:
        term = service["paymentTerm"]
        expires = timeutil.add_terms(start, term)
        while expires <= now:
            expires = timeutil.add_terms(expires, term)
        service["status"] = "active" if service["status"] == "prolong_wait" else service["status"]
        service["expiresAt"] = timeutil.iso(expires)
        self.deadlines.pop(int(service["id"]), None)
        self._schedule(int(service["id"]), expires)
        self._add_tx(batch, service, "prolong", performed=True)
        self._touch_service(batch, service)

    def _on_expiry(self, batch: stream.Batch, service: dict[str, Any], expires: datetime, now: datetime) -> None:
        rng = self.rng
        who = self._who(service)
        share = PERIOD_DAYS[service["paymentTerm"]] / 30
        account = self.accounts[int(service["ownerId"])]
        price = int(service["price"])
        if rng.random() < 1 - (1 - who.cancel) ** share:
            self.leaving.add(int(service["id"]))
            self._to_wait(service, now)
            self.events["cancel"] += 1
        elif service["autoProlong"]:
            if self._charge(account, price):
                self._renew(batch, service, expires, now)
                self.events["auto_renew"] += 1
            elif rng.random() < who.topup**share:
                self._top_up(account, price)
                self._charge(account, price)
                self._renew(batch, service, expires, now)
                self.events["topup_renew"] += 1
            else:
                self._add_tx(batch, service, "prolong", performed=False)
                self._to_wait(service, now)
                self.events["auto_failed"] += 1
        elif rng.random() < who.manual**share:
            self._top_up(account, price)
            self._charge(account, price)
            self._renew(batch, service, expires, now)
            self.events["manual_renew"] += 1
        else:
            self._to_wait(service, now)
            self.events["manual_skip"] += 1
        self._touch_account(batch, account)

    def _pay_waiting(self, batch: stream.Batch, now: datetime) -> None:
        for sid in list(self.deadlines):
            service = self.services[sid]
            if sid in self.leaving or service["status"] != "prolong_wait":
                continue
            if self.rng.random() < self._who(service).wait_pay:
                account = self.accounts[int(service["ownerId"])]
                price = int(service["price"])
                self._top_up(account, price)
                self._charge(account, price)
                self._renew(batch, service, now, now)
                self.events["wait_paid"] += 1

    def _background(self, batch: stream.Batch, count: int) -> None:
        weights = [stream.MUTATION_WEIGHTS[k] for k in BACKGROUND_KINDS]
        for _ in range(count):
            kind = self.rng.choices(BACKGROUND_KINDS, weights=weights)[0]
            if getattr(self, f"_m_{kind}")(batch):
                self.events[kind] += 1

    def step(self, background_mean: float, *, daily: bool) -> None:
        now = self.clock()
        batch = stream.Batch()
        before = len(self.deadlines)
        self._expire_waiting(batch)
        self.events["delete"] += before - len(self.deadlines)
        while self.queue and self.queue[0][0] <= now:
            expires, sid = heapq.heappop(self.queue)
            service = self.services[sid]
            if self.expiry.get(sid) != expires or service["status"] not in ("active", "suspended"):
                continue
            self._on_expiry(batch, service, expires, now)
        if daily:
            self._pay_waiting(batch, now)
        whole = int(background_mean)
        self._background(batch, whole + (1 if self.rng.random() < background_mean - whole else 0))


def simulate_calendar(
    data: Mapping[str, Any],
    segments: Mapping[int, str],
    *,
    seed: int,
    horizon_days: int,
    background_per_service_day: float,
    concentration: float,
    step: timedelta = timedelta(hours=1),
) -> FutureResult:
    rng = random.Random(seed)
    behavior = draw_behavior(segments, random.Random(seed + 1), concentration)
    start = timeutil.parse_iso(data["reference_time"])
    clock = SimClock(start)
    world = CalendarWorld(rng, clock, behavior)
    world.load(data["accounts"], data["services"], _max_tx_id(data))
    tracked = _live_at_start(data["services"])
    background_mean = len(tracked) * background_per_service_day * step / DAY
    steps_per_day = max(1, round(DAY / step))
    end = start + timedelta(days=horizon_days)
    index = 0
    while clock.now + step <= end:
        clock.advance(step)
        index += 1
        world.step(background_mean, daily=index % steps_per_day == 0)
    return FutureResult("calendar", {sid: world.services[sid]["status"] for sid in tracked}, world.events)
