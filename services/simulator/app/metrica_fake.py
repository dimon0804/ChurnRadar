"""Фейковый Logs API Яндекс Метрики для демо: правдоподобные визиты клиентов из source.accounts.

Повторяет то, чем пользуется коннектор ingest (пути, параметры, статусы, поля, TSV с заголовком):
evaluate → создание запроса логов → status created/processed → скачивание частей → clean.
Настоящий токен и адрес https://api-metrika.yandex.net в настройках ingest переключают
коннектор на боевую Метрику, код не меняется.

Визиты детерминированы: один и тот же клиент в один и тот же день даёт одни и те же визиты,
поэтому повторная выгрузка периода ничего не «прыгает». Поведение задаётся профилем клиента
относительно опорного времени данных (source.clock, иначе сейчас): у клиентов кейса профиль
закреплён, у сгенерированных выбирается по id.
"""

from __future__ import annotations

import hashlib
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

SUPPORTED_FIELDS = (
    "ym:s:visitID",
    "ym:s:counterID",
    "ym:s:date",
    "ym:s:dateTime",
    "ym:s:clientID",
    "ym:s:startURL",
    "ym:s:endURL",
    "ym:s:pageViews",
    "ym:s:visitDuration",
    "ym:s:goalsID",
    "ym:s:parsedParamsKey1",
    "ym:s:parsedParamsKey2",
)
MAX_DAYS = 366
TZ = timedelta(hours=3)  # часовой пояс счётчика в демо — Москва
PANEL = "https://panel.hosting.example"
SITE = "https://hosting.example"

# Профили клиентов кейса (data/sample): у 2, 3, 7, 8 все услуги уже удалены.
CASE_PROFILES = {
    1: "fading",    # раньше заходил почти каждый день, последнюю неделю почти пропал
    2: "gone",
    3: "gone",
    4: "gone",      # больше месяца не заходил в панель
    5: "regular",
    6: "cancel",    # на днях смотрел страницу удаления услуги
    7: "gone",
    8: "none",
    9: "regular",
    10: "pricing",  # сравнивает тарифы
}
GENERATED_PROFILES = (("regular", 50), ("fading", 12), ("gone", 10), ("cancel", 8), ("pricing", 5), ("none", 15))


def client_id_for(account_id: int) -> int:
    """ClientID браузера клиента в демо: стабильный, похож на настоящий (UInt64)."""
    digest = hashlib.sha256(f"client:{account_id}".encode()).digest()
    return 1_600_000_000_000_000_000 + int.from_bytes(digest[:7], "big")


def profile_for(account_id: int) -> str:
    if account_id in CASE_PROFILES:
        return CASE_PROFILES[account_id]
    rng = random.Random(f"profile:{account_id}")
    names, weights = zip(*GENERATED_PROFILES, strict=True)
    return rng.choices(names, weights=weights)[0]


def _rng(*parts: object) -> random.Random:
    return random.Random(":".join(str(p) for p in parts))


@dataclass(frozen=True, slots=True)
class Visit:
    visit_id: int
    local: datetime
    client_id: int
    start_url: str
    end_url: str
    page_views: int
    duration: int
    linked: bool
    account_id: int
    goals: tuple[int, ...] = ()


def visits_for(account_id: int, day: date, anchor_day: date) -> list[Visit]:
    """Визиты клиента за день (в часовом поясе счётчика) при опорном дне anchor_day."""
    profile = profile_for(account_id)
    ago = (anchor_day - day).days  # сколько дней до опорного; отрицательное — день после него
    rng = _rng("visits", account_id, day.isoformat())
    if profile == "none":
        return []
    if profile == "gone":
        # заходил, а последние 30 дней — ни разу; последний визит ровно 32 дня назад
        rate = 0.5 if ago > 32 else 0.0
    elif profile == "fading":
        rate = 0.6 if ago >= 7 else 0.05
    else:
        rate = 0.5
    count = 0
    if rng.random() < rate or (profile == "gone" and ago == 32):
        count = 1 + (rng.random() < 0.3)
    cancel_day = profile == "cancel" and ago in (2, 6)
    pricing_day = profile == "pricing" and ago in (1, 3, 9)
    if (cancel_day or pricing_day) and count == 0:
        count = 1
    visits: list[Visit] = []
    for n in range(count):
        local = datetime(day.year, day.month, day.day) + timedelta(seconds=rng.randrange(7 * 3600, 23 * 3600))
        start = f"{PANEL}/services"
        end = rng.choice((f"{PANEL}/services", f"{PANEL}/servers/console", f"{PANEL}/support"))
        if rng.random() < 0.15:
            end = f"{PANEL}/billing/topup"
        if cancel_day and n == 0:
            end = f"{PANEL}/services/{account_id * 10 + 1}/delete"
        if pricing_day and n == 0:
            start = f"{SITE}/pricing"
        visits.append(
            Visit(
                visit_id=int.from_bytes(hashlib.sha256(f"v:{account_id}:{day}:{n}".encode()).digest()[:7], "big"),
                local=local,
                client_id=client_id_for(account_id),
                start_url=start,
                end_url=end,
                page_views=rng.randint(1, 12),
                duration=rng.randint(20, 1800),
                # примерно каждый седьмой визит — до входа в кабинет, номер аккаунта неизвестен,
                # связать его можно только через таблицу соответствий clientID
                linked=rng.random() >= 0.15,
                account_id=account_id,
            )
        )
    return visits


def _array(values: list[str], quoted: bool) -> str:
    if quoted:
        return "[" + ",".join("'" + v.replace("\\", "\\\\").replace("'", "\\'") + "'" for v in values) + "]"
    return "[" + ",".join(values) + "]"


def tsv_value(visit: Visit, name: str, counter_id: int) -> str:
    params = visit.linked
    match name:
        case "ym:s:visitID":
            return str(visit.visit_id)
        case "ym:s:counterID":
            return str(counter_id)
        case "ym:s:date":
            return visit.local.date().isoformat()
        case "ym:s:dateTime":
            return visit.local.strftime("%Y-%m-%d %H:%M:%S")
        case "ym:s:clientID":
            return str(visit.client_id)
        case "ym:s:startURL":
            return visit.start_url
        case "ym:s:endURL":
            return visit.end_url
        case "ym:s:pageViews":
            return str(visit.page_views)
        case "ym:s:visitDuration":
            return str(visit.duration)
        case "ym:s:goalsID":
            return _array([str(g) for g in visit.goals], quoted=False)
        case "ym:s:parsedParamsKey1":
            return _array(["account_id"] if params else [], quoted=True)
        case "ym:s:parsedParamsKey2":
            return _array([str(visit.account_id)] if params else [], quoted=True)
    return ""


@dataclass(slots=True)
class LogRequest:
    request_id: int
    counter_id: int
    date1: date
    date2: date
    fields: list[str]
    created: float
    status: str = "created"
    parts: list[str] = field(default_factory=list)
    size: int = 0

    def to_api(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "counter_id": self.counter_id,
            "source": "visits",
            "date1": self.date1.isoformat(),
            "date2": self.date2.isoformat(),
            "fields": self.fields,
            "status": self.status,
            "size": self.size,
            "parts": [{"part_number": n, "size": len(p.encode())} for n, p in enumerate(self.parts)],
            "attribution": "LASTSIGN",
        }


class MetricaError(Exception):
    def __init__(self, status: int, error_type: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type
        self.message = message

    def to_api(self) -> dict[str, Any]:
        return {"errors": [{"error_type": self.error_type, "message": self.message}], "code": self.status,
                "message": self.message}


class FakeMetrica:
    def __init__(
        self,
        *,
        accounts: Callable[[], Awaitable[list[int]]],
        anchor: Callable[[], Awaitable[datetime]],
        counter_id: int,
        processing_s: float = 1.0,
        rows_per_part: int = 5000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._accounts = accounts
        self._anchor = anchor
        self.counter_id = counter_id
        self.processing_s = processing_s
        self.rows_per_part = rows_per_part
        self._clock = clock
        self._requests: dict[int, LogRequest] = {}
        self._next_id = 7_000_001

    def _check_counter(self, counter_id: int) -> None:
        if counter_id != self.counter_id:
            raise MetricaError(403, "access_denied", f"Нет доступа к счётчику {counter_id}")

    @staticmethod
    def _parse_period(date1: str, date2: str) -> tuple[date, date]:
        try:
            first = date.fromisoformat(date1[:10])
            last = date.fromisoformat(date2[:10])
        except ValueError as exc:
            raise MetricaError(400, "invalid_parameter", "date1 и date2 — даты в формате YYYY-MM-DD") from exc
        if last < first:
            raise MetricaError(400, "invalid_parameter", "date2 раньше date1")
        return first, last

    @staticmethod
    def _parse_fields(raw: str) -> list[str]:
        fields = [f.strip() for f in raw.split(",") if f.strip()]
        unknown = [f for f in fields if f not in SUPPORTED_FIELDS]
        if not fields or unknown:
            raise MetricaError(400, "invalid_parameter", f"Неизвестные поля: {', '.join(unknown) or 'пусто'}")
        return fields

    def evaluate(self, counter_id: int, date1: str, date2: str, fields: str, source: str) -> dict[str, Any]:
        self._check_counter(counter_id)
        first, last = self._parse_period(date1, date2)
        self._parse_fields(fields)
        days = (last - first).days + 1
        return {"log_request_evaluation": {"possible": days <= MAX_DAYS, "max_possible_day_quantity": MAX_DAYS}}

    def create(self, counter_id: int, date1: str, date2: str, fields: str, source: str) -> dict[str, Any]:
        self._check_counter(counter_id)
        if source != "visits":
            raise MetricaError(400, "invalid_parameter", "В демо доступен только source=visits")
        first, last = self._parse_period(date1, date2)
        request = LogRequest(self._next_id, counter_id, first, last, self._parse_fields(fields), self._clock())
        self._next_id += 1
        self._requests[request.request_id] = request
        return {"log_request": request.to_api()}

    def _get(self, counter_id: int, request_id: int) -> LogRequest:
        self._check_counter(counter_id)
        request = self._requests.get(request_id)
        if request is None:
            raise MetricaError(404, "not_found", f"Запрос логов {request_id} не найден")
        return request

    async def status(self, counter_id: int, request_id: int) -> dict[str, Any]:
        request = self._get(counter_id, request_id)
        if request.status == "created" and self._clock() - request.created >= self.processing_s:
            await self._prepare(request)
        return {"log_request": request.to_api()}

    async def _prepare(self, request: LogRequest) -> None:
        anchor = await self._anchor()
        anchor_day = (anchor + TZ).date()
        accounts = await self._accounts()
        lines: list[str] = []
        day = request.date1
        while day <= request.date2:
            for account_id in accounts:
                for visit in visits_for(account_id, day, anchor_day):
                    lines.append("\t".join(tsv_value(visit, f, request.counter_id) for f in request.fields))
            day += timedelta(days=1)
        header = "\t".join(request.fields)
        step = max(1, self.rows_per_part)
        chunks = [lines[i : i + step] for i in range(0, len(lines), step)] or [[]]
        request.parts = ["\n".join([header, *chunk]) + "\n" for chunk in chunks]
        request.size = sum(len(p.encode()) for p in request.parts)
        request.status = "processed"

    def download(self, counter_id: int, request_id: int, part: int) -> str:
        request = self._get(counter_id, request_id)
        if request.status != "processed":
            raise MetricaError(400, "invalid_parameter", f"Логи ещё не готовы (статус {request.status})")
        if not 0 <= part < len(request.parts):
            raise MetricaError(404, "not_found", f"Нет части {part}")
        return request.parts[part]

    def clean(self, counter_id: int, request_id: int) -> dict[str, Any]:
        request = self._get(counter_id, request_id)
        if request.status != "processed":
            raise MetricaError(400, "invalid_parameter", f"Очистить можно только готовые логи (статус {request.status})")
        request.status = "cleaned_by_user"
        request.parts = []
        return {"log_request": request.to_api()}

    async def client_ids_csv(self) -> str:
        """Соответствия clientID → аккаунт для демо запасного пути (загрузка через «Данные»)."""
        rows = ["client_id,account_id"]
        rows += [f"{client_id_for(a)},{a}" for a in await self._accounts()]
        return "\n".join(rows) + "\n"
