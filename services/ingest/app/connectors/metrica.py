"""Коннектор «Яндекс Метрика»: визиты из Logs API → дневные агрегаты по аккаунту → source.web_activity.

Порядок работы с Logs API (https://yandex.ru/dev/metrika/ru/logs/practice/quick-start):
  1. GET  /management/v1/counter/{id}/logrequests/evaluate — можно ли выгрузить период;
  2. POST /management/v1/counter/{id}/logrequests?date1&date2&fields&source=visits — создать запрос;
  3. GET  /management/v1/counter/{id}/logrequest/{request_id} — ждать status = processed;
  4. GET  .../logrequest/{request_id}/part/{part_number}/download — скачать части (TSV с заголовком);
  5. POST .../logrequest/{request_id}/clean — удалить подготовленные логи (квота 10 ГБ на счётчик).
Авторизация — заголовок «Authorization: OAuth <токен>». Данные за текущий день недоступны,
поэтому выгружаем по вчерашний день включительно.

Связь визита с аккаунтом. Сайт вызывает ym(счётчик, 'setUserID', id) — это связывает UserID
с ClientID внутри Метрики. В списке полей визитов Logs API отдельного поля UserID нет, поэтому
номер аккаунта сайт передаёт ещё и параметром визита: ym(счётчик, 'params', {account_id: id}).
Параметры приходят в полях ym:s:parsedParamsKey1 (ключ) и ym:s:parsedParamsKey2 (значение).
Запасной путь — таблица соответствий clientID → аккаунт, которую загружают через наш API.

Сырые визиты не сохраняем: из каждой выгрузки считаем дневные агрегаты, храним их 90 дней.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg
from svckit.outbox import add_event

from app.repository import OUTBOX_TABLE, SUBJECT_SOURCE_CHANGED

from .http import SourceClient, SourceError

FIELDS = (
    "ym:s:visitID",
    "ym:s:dateTime",
    "ym:s:clientID",
    "ym:s:startURL",
    "ym:s:endURL",
    "ym:s:goalsID",
    "ym:s:parsedParamsKey1",
    "ym:s:parsedParamsKey2",
)
RETENTION_DAYS = 90
FAILED_STATUSES = frozenset({"canceled", "processing_failed", "cleaned_by_user", "cleaned_automatically_as_too_old"})


@dataclass(slots=True)
class DayStats:
    visits: int = 0
    payment: int = 0
    cancel: int = 0
    pricing: int = 0
    last_visit_at: datetime | None = None
    last_cancel_at: datetime | None = None

    def add(self, moment: datetime, groups: set[str]) -> None:
        self.visits += 1
        if "payment" in groups:
            self.payment += 1
        if "pricing" in groups:
            self.pricing += 1
        if "cancel" in groups:
            self.cancel += 1
            if self.last_cancel_at is None or moment > self.last_cancel_at:
                self.last_cancel_at = moment
        if self.last_visit_at is None or moment > self.last_visit_at:
            self.last_visit_at = moment


@dataclass(slots=True)
class Aggregate:
    days: dict[tuple[int, date], DayStats] = field(default_factory=dict)
    visits: int = 0
    linked: int = 0
    by_param: int = 0
    by_client_id: int = 0
    bad_rows: int = 0

    @property
    def accounts(self) -> int:
        return len({account for account, _ in self.days})


@dataclass(slots=True)
class MetricaResult:
    cursor: dict[str, Any]
    records: int
    details: dict[str, Any]
    account_ids: set[int]


# --- разбор TSV ---------------------------------------------------------------------------

_UNESCAPE = {"t": "\t", "n": "\n", "r": "\r", "\\": "\\", "0": "\0", "'": "'", '"': '"', "b": "\b", "f": "\f"}


def unescape(value: str) -> str:
    """TSV Logs API экранирует табуляцию, перевод строки и обратный слэш обратным слэшем."""
    if "\\" not in value:
        return value
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            out.append(_UNESCAPE.get(value[i + 1], value[i + 1]))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_array(value: str) -> list[str]:
    """Массив из TSV: [1,2,3] или ['a','b'] (кавычки внутри экранированы обратным слэшем)."""
    value = value.strip()
    if not value or value == "[]":
        return []
    if not (value.startswith("[") and value.endswith("]")):
        return [value]
    body = value[1:-1]
    items: list[str] = []
    current: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(body):
        ch = body[i]
        if quote:
            if ch == "\\" and i + 1 < len(body):
                current.append(body[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            else:
                current.append(ch)
        elif ch in ("'", '"'):
            quote = ch
        elif ch == ",":
            items.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    items.append("".join(current).strip())
    return items


def parse_tsv(text: str) -> Iterator[dict[str, str]]:
    lines = iter(text.splitlines())
    header = next(lines, "")
    names = header.split("\t")
    for line in lines:
        if not line:
            continue
        values = line.split("\t")
        if len(values) != len(names):
            yield {}
            continue
        yield {name: unescape(v) for name, v in zip(names, values, strict=True)}


# --- агрегирование ------------------------------------------------------------------------


class Classifier:
    """К каким группам страниц (оплата, удаление, тарифы) относится визит: по адресам входа
    и выхода (подстрока без учёта регистра) и по достигнутым целям."""

    def __init__(self, pages: dict[str, list[str]], goals: dict[str, list[int]]) -> None:
        self._pages = {g: [p.lower() for p in patterns if p] for g, patterns in pages.items()}
        self._goals = {g: {str(n) for n in numbers} for g, numbers in goals.items()}

    def groups(self, urls: Iterable[str], goal_ids: Iterable[str]) -> set[str]:
        lowered = [u.lower() for u in urls if u]
        goals = set(goal_ids)
        found = {g for g, patterns in self._pages.items() if any(p in u for p in patterns for u in lowered)}
        found |= {g for g, numbers in self._goals.items() if numbers & goals}
        return found


def account_from_params(keys: list[str], values: list[str], param: str) -> int | None:
    for key, value in zip(keys, values, strict=False):
        if key == param:
            digits = value.strip().strip("'\"")
            if digits.isdigit() and 0 < len(digits) <= 18:
                return int(digits)
    return None


def aggregate(
    rows: Iterable[dict[str, str]],
    *,
    param: str,
    client_map: dict[int, int],
    classifier: Classifier,
    tz: timedelta,
    into: Aggregate | None = None,
) -> Aggregate:
    agg = into or Aggregate()
    for row in rows:
        if not row:
            agg.bad_rows += 1
            continue
        agg.visits += 1
        try:
            local = datetime.strptime(row["ym:s:dateTime"], "%Y-%m-%d %H:%M:%S")
        except (KeyError, ValueError):
            agg.bad_rows += 1
            continue
        account = account_from_params(
            parse_array(row.get("ym:s:parsedParamsKey1", "")),
            parse_array(row.get("ym:s:parsedParamsKey2", "")),
            param,
        )
        if account is not None:
            agg.by_param += 1
        else:
            client = row.get("ym:s:clientID", "").strip()
            account = client_map.get(int(client)) if client.isdigit() else None
            if account is None:
                continue
            agg.by_client_id += 1
        agg.linked += 1
        moment = (local - tz).replace(tzinfo=UTC)
        groups = classifier.groups(
            (row.get("ym:s:startURL", ""), row.get("ym:s:endURL", "")),
            parse_array(row.get("ym:s:goalsID", "")),
        )
        agg.days.setdefault((account, local.date()), DayStats()).add(moment, groups)
    return agg


# --- Logs API -----------------------------------------------------------------------------


class LogsApi:
    def __init__(self, client: SourceClient, counter_id: int, *, sleep: Any = asyncio.sleep) -> None:
        self._client = client
        self._base = f"/management/v1/counter/{counter_id}"
        self._sleep = sleep

    def _params(self, date1: date, date2: date) -> dict[str, str]:
        return {"date1": date1.isoformat(), "date2": date2.isoformat(), "fields": ",".join(FIELDS), "source": "visits"}

    async def evaluate(self, date1: date, date2: date) -> dict[str, Any]:
        body = await self._client.get_json(f"{self._base}/logrequests/evaluate", self._params(date1, date2))
        return (body or {}).get("log_request_evaluation") or {}

    async def create(self, date1: date, date2: date) -> int:
        response = await self._client.request("POST", f"{self._base}/logrequests", self._params(date1, date2))
        body = self._client.json(response, "logrequests")
        request_id = ((body or {}).get("log_request") or {}).get("request_id")
        if request_id is None:
            raise SourceError("Метрика не вернула request_id созданного запроса логов")
        return int(request_id)

    async def status(self, request_id: int) -> dict[str, Any]:
        body = await self._client.get_json(f"{self._base}/logrequest/{request_id}")
        return (body or {}).get("log_request") or {}

    async def wait(self, request_id: int, *, poll_s: float, max_wait_s: float) -> dict[str, Any]:
        deadline = time.monotonic() + max_wait_s
        while True:
            info = await self.status(request_id)
            state = info.get("status")
            if state == "processed":
                return info
            if state in FAILED_STATUSES:
                raise SourceError(f"Метрика не подготовила логи: статус {state}")
            if time.monotonic() >= deadline:
                raise SourceError(f"Метрика готовит логи дольше {int(max_wait_s)} с (статус {state}), повторим позже",
                                  retryable=True)
            await self._sleep(poll_s)

    async def download(self, request_id: int, part: int) -> str:
        response = await self._client.request(
            "GET", f"{self._base}/logrequest/{request_id}/part/{part}/download", accept="text/plain, */*"
        )
        return response.text

    async def clean(self, request_id: int) -> None:
        await self._client.request("POST", f"{self._base}/logrequest/{request_id}/clean")


def period(as_of: datetime, today: date, cursor: dict[str, Any], *, lookback: int, refresh: int, tz: timedelta) -> tuple[date, date]:
    """Какие дни выгружать: по вчерашний (относительно опорного времени и сегодняшнего дня) включительно.
    Первый раз — на глубину lookback, дальше — с последнего дня, перечитывая refresh дней:
    визиты в Метрике дописываются до трёх суток."""
    local_today = min((as_of + tz).date(), today)
    end = local_today - timedelta(days=1)
    last = cursor.get("last_date")
    if last:
        start = date.fromisoformat(last) - timedelta(days=max(refresh - 1, 0))
        start = max(start, end - timedelta(days=lookback - 1))
    else:
        start = end - timedelta(days=lookback - 1)
    return start, end


async def run_metrica(
    pool: asyncpg.Pool,
    client: SourceClient,
    cursor: dict[str, Any],
    options: dict[str, Any],
    *,
    as_of: datetime,
    today: date | None = None,
    sleep: Any = asyncio.sleep,
) -> MetricaResult:
    counter = options.get("counter_id")
    if not counter:
        raise SourceError("Не задан номер счётчика Метрики (options.counter_id или CONNECTOR_METRICA_COUNTER)")
    tz = timedelta(hours=float(options["tz_offset_hours"]))
    start, end = period(
        as_of, today or datetime.now(UTC).date(), cursor,
        lookback=options["lookback_days"], refresh=options["refresh_days"], tz=tz,
    )
    agg = Aggregate()
    request_id: int | None = None
    parts = 0
    if start <= end:
        api = LogsApi(client, int(counter), sleep=sleep)
        evaluation = await api.evaluate(start, end)
        if evaluation and evaluation.get("possible") is False:
            limit = evaluation.get("max_possible_day_quantity")
            raise SourceError(f"Метрика не может выгрузить {start}…{end} одним запросом (максимум дней: {limit})")
        client_map = await _client_map(pool)
        classifier = Classifier(options["pages"], options["goals"])
        request_id = await api.create(start, end)
        try:
            info = await api.wait(request_id, poll_s=options["poll_interval_s"], max_wait_s=options["max_wait_s"])
            for part in sorted(p.get("part_number", 0) for p in info.get("parts") or [{"part_number": 0}]):
                text = await api.download(request_id, part)
                aggregate(parse_tsv(text), param=options["account_param"], client_map=client_map,
                          classifier=classifier, tz=tz, into=agg)
                parts += 1
        finally:
            if parts:
                # квота на счётчик общая: подготовленные логи после скачивания удаляем
                try:
                    await api.clean(request_id)
                except SourceError:
                    pass
    changed = await save(pool, agg, start, end, as_of=as_of, tz=tz)
    new_cursor = dict(cursor)
    if start <= end:
        new_cursor["last_date"] = end.isoformat()
    return MetricaResult(
        cursor=new_cursor,
        records=agg.linked,
        details={
            "period": [start.isoformat(), end.isoformat()] if start <= end else None,
            "request_id": request_id,
            "parts": parts,
            "visits": agg.visits,
            "linked": agg.linked,
            "by_param": agg.by_param,
            "by_client_id": agg.by_client_id,
            "accounts": agg.accounts,
            "bad_rows": agg.bad_rows,
            "changed_accounts": len(changed),
        },
        account_ids=changed,
    )


async def _client_map(pool: asyncpg.Pool) -> dict[int, int]:
    rows = await pool.fetch("SELECT client_id, account_id FROM source.metrica_client_ids")
    return {int(r["client_id"]): r["account_id"] for r in rows}


_ACTIVITY_SQL = """
SELECT account_id,
       max(last_visit_at)                                              AS last_visit_at,
       (min(day)::timestamp - make_interval(secs => $2)) AT TIME ZONE 'UTC' AS first_visit_at,
       coalesce(sum(visits) FILTER (WHERE day > $1::date - 7), 0)::int          AS visits_7d,
       coalesce(sum(visits) FILTER (WHERE day > $1::date - 30), 0)::int         AS visits_30d,
       coalesce(sum(payment_visits) FILTER (WHERE day > $1::date - 30), 0)::int AS payment_visits_30d,
       coalesce(sum(cancel_visits) FILTER (WHERE day > $1::date - 30), 0)::int  AS cancel_visits_30d,
       coalesce(sum(pricing_visits) FILTER (WHERE day > $1::date - 30), 0)::int AS pricing_visits_30d,
       max(last_cancel_at)                                             AS last_cancel_visit_at
FROM source.web_visits_daily
WHERE day <= $1::date
GROUP BY account_id
"""
ACTIVITY_COLS = (
    "account_id", "last_visit_at", "first_visit_at", "visits_7d", "visits_30d", "payment_visits_30d",
    "cancel_visits_30d", "pricing_visits_30d", "last_cancel_visit_at",
)
DAILY_COLS = ("account_id", "day", "visits", "payment_visits", "cancel_visits", "pricing_visits", "last_visit_at",
              "last_cancel_at")


async def save(pool: asyncpg.Pool, agg: Aggregate, start: date, end: date, *, as_of: datetime, tz: timedelta) -> set[int]:
    """Одна транзакция: заменить дни периода, срезать старше 90 дней, пересчитать сводку
    web_activity на опорное время и положить в outbox событие по клиентам, у которых она поменялась."""
    as_of_day = (as_of + tz).date()
    async with pool.acquire() as conn, conn.transaction():
        if start <= end:
            await conn.execute("DELETE FROM source.web_visits_daily WHERE day BETWEEN $1 AND $2", start, end)
            # дни вне запрошенного периода не пишем: их строки уже лежат в базе от прошлых выгрузок
            rows = [
                (account, day, s.visits, s.payment, s.cancel, s.pricing, s.last_visit_at, s.last_cancel_at)
                for (account, day), s in agg.days.items()
                if start <= day <= end
            ]
            if rows:
                await conn.copy_records_to_table(
                    "web_visits_daily", schema_name="source", columns=DAILY_COLS, records=rows
                )
        await conn.execute(
            "DELETE FROM source.web_visits_daily WHERE day < $1", as_of_day - timedelta(days=RETENTION_DAYS)
        )
        fresh = {r["account_id"]: tuple(r[c] for c in ACTIVITY_COLS) for r in await conn.fetch(
            _ACTIVITY_SQL, as_of_day, tz.total_seconds()
        )}
        old = {r["account_id"]: tuple(r[c] for c in ACTIVITY_COLS) for r in await conn.fetch(
            f"SELECT {', '.join(ACTIVITY_COLS)} FROM source.web_activity"
        )}
        changed = {a for a in fresh.keys() | old.keys() if fresh.get(a) != old.get(a)}
        await conn.execute("DELETE FROM source.web_activity")
        if fresh:
            await conn.copy_records_to_table(
                "web_activity", schema_name="source", columns=(*ACTIVITY_COLS, "as_of"),
                records=[(*row, as_of) for row in fresh.values()],
            )
        existing: set[int] = set()
        if changed:
            existing = {r["id"] for r in await conn.fetch(
                "SELECT id FROM source.accounts WHERE id = ANY($1::bigint[])", list(changed)
            )}
        if existing:
            await add_event(conn, OUTBOX_TABLE, SUBJECT_SOURCE_CHANGED, "source.changed", {
                "kind": "changes",
                "snapshot_id": f"web_{uuid.uuid4().hex[:16]}",
                "counts": {"accounts": 0, "services": 0, "transactions": 0},
                "account_ids": sorted(existing),
            })
    return existing


async def summary(pool: asyncpg.Pool) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        SELECT (SELECT count(*) FROM source.metrica_client_ids)      AS client_ids,
               (SELECT max(updated_at) FROM source.metrica_client_ids) AS client_ids_updated_at,
               (SELECT count(*) FROM source.web_activity)             AS accounts_with_activity,
               (SELECT max(last_visit_at) FROM source.web_activity)   AS last_visit_at,
               (SELECT max(computed_at) FROM source.web_activity)     AS computed_at
        """
    )
    return dict(row) if row else {}


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
