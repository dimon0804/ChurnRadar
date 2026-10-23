"""Состояние сервиса: данные, настройки, результаты расчёта, симулятор, события."""

import asyncio
import logging
import random
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from . import metrics
from .config import Config
from .events import EventBus
from .model_settings import SettingsManager
from .scoring import CustomerResult, full_summary, score_all, score_customer, short_summary
from .simulator import generate, mutate
from .store import DataStore
from .timeutil import iso, utcnow

log = logging.getLogger("mock-core")


class Core:
    def __init__(self, config: Config):
        self.config = config
        self.store = DataStore()
        self.settings = SettingsManager(config.settings_path)
        self.bus = EventBus(config.nats_url, producer=config.service_name, enabled=config.nats_enabled)
        self.results: Dict[int, CustomerResult] = {}
        self.scored_at = None
        self.lock = asyncio.Lock()
        self._summary_cache: Optional[Dict[str, Any]] = None
        self.sim_running = False
        self.sim_interval_ms = config.simulator_interval_ms
        self.sim_events = 0
        self._sim_task: Optional[asyncio.Task] = None
        self._rng = random.Random()

    # --- жизненный цикл ---

    async def start(self) -> None:
        self.settings.load()
        self.store.load_file(self.config.snapshot_path)
        log.info("снимок загружен из %s: %s", self.config.snapshot_path, self.store.counts())
        self._rescore_full("startup")
        self.bus.start()
        if self.config.simulator_autostart:
            self.set_simulator(running=True)

    async def stop(self) -> None:
        self.set_simulator(running=False)
        await self.bus.stop()

    @property
    def ready(self) -> bool:
        return self.store.loaded and self.scored_at is not None

    # --- пересчёт ---

    def _after_scoring(self, kind: str, started: float) -> float:
        duration = time.perf_counter() - started
        self.scored_at = utcnow()
        self._summary_cache = None
        metrics.scoring_duration.observe(duration)
        metrics.scoring_runs.labels(kind=kind).inc()
        metrics.update_scores(self.summary_counts(), self.settings.version, self.scored_at.timestamp())
        return duration

    def _rescore_full(self, kind: str) -> float:
        started = time.perf_counter()
        self.results = score_all(self.store, self.settings.snapshot())
        return self._after_scoring(kind, started)

    def _rescore_accounts(self, account_ids: Iterable[int]) -> Tuple[List[int], float]:
        started = time.perf_counter()
        settings = self.settings.snapshot()
        changed: List[int] = []
        for aid in sorted(set(account_ids)):
            if aid not in self.store.accounts:
                continue
            before = self.results.get(aid)
            after = score_customer(self.store, aid, settings)
            self.results[aid] = after
            if before is None or before.fingerprint() != after.fingerprint():
                changed.append(aid)
        return changed, self._after_scoring("incremental", started)

    def summary_counts(self) -> Dict[str, Any]:
        data = short_summary(self.results)
        data["services_at_risk"] = sum(r.services_at_risk for r in self.results.values())
        return data

    def _publish_scores(self, changed: List[int], full: bool, duration: float) -> None:
        summary = short_summary(self.results)
        self.bus.publish(
            "scores.updated",
            {
                "model_version": self.settings.version,
                "scored_at": iso(self.scored_at),
                "changed_customer_ids": [] if full else changed,
                "full": full,
                "duration_ms": round(duration * 1000, 3),
                "summary": summary,
            },
        )

    def full_summary(self) -> Dict[str, Any]:
        if self._summary_cache is None:
            self._summary_cache = full_summary(self.store, self.results, self.settings.snapshot())
        return self._summary_cache

    # --- операции, меняющие состояние ---

    async def patch_settings(self, patch, user: Optional[str]) -> Dict[str, Any]:
        async with self.lock:
            self.settings.patch(patch, user)
            return self._settings_changed(user)

    async def reset_settings(self, user: Optional[str]) -> Dict[str, Any]:
        async with self.lock:
            self.settings.reset(user)
            return self._settings_changed(user)

    def _settings_changed(self, user: Optional[str]) -> Dict[str, Any]:
        self.bus.publish("settings.changed", {"version": self.settings.version, "updated_by": user})
        duration = self._rescore_full("settings")
        self._publish_scores([], True, duration)
        return self.settings.to_api()

    async def replace_snapshot(self, snapshot) -> Dict[str, Any]:
        async with self.lock:
            snapshot_id = self.store.replace_snapshot(snapshot)
            counts = self.store.counts()
            self.bus.publish(
                "source.changed",
                {"kind": "snapshot", "snapshot_id": snapshot_id, "counts": counts, "account_ids": []},
            )
            duration = self._rescore_full("snapshot")
            self._publish_scores([], True, duration)
            return {"snapshot_id": snapshot_id, "counts": counts}

    async def apply_changes(self, batch) -> int:
        async with self.lock:
            touched = self.store.apply_changes(batch)
            accepted = len(batch.accounts) + len(batch.services) + len(batch.transactions)
            self._changes_applied(
                touched,
                {
                    "accounts": len(batch.accounts),
                    "services": len(batch.services),
                    "transactions": len(batch.transactions),
                },
            )
            return accepted

    def _changes_applied(self, touched: Set[int], counts: Dict[str, int]) -> None:
        self.bus.publish(
            "source.changed",
            {"kind": "changes", "snapshot_id": self.store.snapshot_id, "counts": counts, "account_ids": sorted(touched)},
        )
        changed, duration = self._rescore_accounts(touched)
        self._publish_scores(changed, False, duration)

    # --- симулятор ---

    def simulator_state(self) -> Dict[str, Any]:
        return {"running": self.sim_running, "interval_ms": self.sim_interval_ms, "events_emitted": self.sim_events}

    def set_simulator(self, running: Optional[bool] = None, interval_ms: Optional[int] = None) -> None:
        if interval_ms is not None:
            self.sim_interval_ms = interval_ms
        if running is None:
            return
        self.sim_running = running
        if running and (self._sim_task is None or self._sim_task.done()):
            self._sim_task = asyncio.create_task(self._sim_loop())
        elif not running and self._sim_task is not None:
            self._sim_task.cancel()
            self._sim_task = None

    async def _sim_loop(self) -> None:
        while self.sim_running:
            await asyncio.sleep(self.sim_interval_ms / 1000)
            try:
                await self.simulator_tick()
            except Exception:  # noqa: BLE001 - один неудачный шаг не должен останавливать поток
                log.exception("ошибка шага симулятора")

    async def simulator_tick(self) -> None:
        async with self.lock:
            if not self.store.accounts:
                return
            account_id, counts = mutate(self.store, self._rng)
            self.sim_events += 1
            metrics.simulator_events.inc()
            self._changes_applied({account_id}, counts)

    async def generate(self, accounts: int, seed: Optional[int]) -> None:
        async with self.lock:
            settings = self.settings.snapshot()
            started = time.perf_counter()

            def build():
                store = generate(accounts, seed, self.store.reference_time)
                return store, score_all(store, settings)

            store, results = await asyncio.to_thread(build)
            self.store, self.results = store, results
            self.bus.publish(
                "source.changed",
                {"kind": "snapshot", "snapshot_id": store.snapshot_id, "counts": store.counts(), "account_ids": []},
            )
            duration = self._after_scoring("snapshot", started)
            self._publish_scores([], True, duration)
