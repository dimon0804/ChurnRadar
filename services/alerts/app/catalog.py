"""Каталог критических событий и персональные настройки уведомлений. Без БД и сети."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CHANNELS = ("panel", "email", "telegram")
DIGEST_CHANNELS = ("email", "telegram")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True, slots=True)
class EventDef:
    code: str
    title: str
    description: str
    unit: str  # pct | eur | count | min
    default_threshold: float
    min_threshold: float
    max_threshold: float
    # для событий «рост за период»: окно по умолчанию в часах (None — окна нет)
    default_period_h: int | None = None
    default_enabled: bool = True


EVENTS: tuple[EventDef, ...] = (
    EventDef(
        "loss_growth",
        "Ожидаемая потеря по базе выросла",
        "Сумма, которую мы, судя по всему, потеряем из-за ухода клиентов, выросла больше чем на порог за период.",
        "pct",
        10,
        1,
        500,
        default_period_h=24,
    ),
    EventDef(
        "big_customer_high",
        "Крупный клиент перешёл в высокий риск",
        "Клиент, на котором мы можем потерять больше порога, попал в группу высокого риска.",
        "eur",
        500,
        1,
        10_000_000,
    ),
    EventDef(
        "risk_share",
        "Доля выручки под риском превысила порог",
        "Какую часть выручки ближайшего горизонта ожидаем потерять; сообщаем, когда доля пересекает порог снизу вверх.",
        "pct",
        40,
        1,
        100,
    ),
    EventDef(
        "high_count_growth",
        "Клиентов высокого риска стало заметно больше",
        "Число клиентов в группе высокого риска выросло больше чем на порог за период.",
        "count",
        10,
        1,
        1_000_000,
        default_period_h=24,
    ),
    EventDef(
        "stale_data",
        "Данные пришли, а пересчёта нет",
        "Источник присылал изменения, но оценки риска не пересчитывались дольше порога: что-то сломалось в конвейере.",
        "min",
        15,
        5,
        10_080,
        default_enabled=False,
    ),
)
EVENT_BY_CODE = {e.code: e for e in EVENTS}
PERIODS_H = (1, 6, 24, 72, 168)


@dataclass(frozen=True, slots=True)
class EventPref:
    enabled: bool
    threshold: float
    period_h: int | None
    channels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Prefs:
    username: str
    email: str | None = None
    timezone: str = "Europe/Moscow"
    quiet_from: time | None = None
    quiet_to: time | None = None
    digest_enabled: bool = False
    digest_time: time = time(9, 0)
    digest_channels: tuple[str, ...] = ("email",)
    mute_until: datetime | None = None
    events: dict[str, EventPref] = field(default_factory=dict)

    def event(self, code: str) -> EventPref:
        return self.events.get(code) or default_event_pref(EVENT_BY_CODE[code])

    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo("UTC")


def default_event_pref(definition: EventDef) -> EventPref:
    return EventPref(
        enabled=definition.default_enabled,
        threshold=definition.default_threshold,
        period_h=definition.default_period_h,
        channels=("panel",),
    )


def default_prefs(username: str) -> Prefs:
    return Prefs(username=username, events={e.code: default_event_pref(e) for e in EVENTS})


class PrefsError(ValueError):
    """Настройки не прошли проверку; details — список {field, message} для ответа 422."""

    def __init__(self, details: list[dict[str, str]]) -> None:
        super().__init__("Настройки уведомлений не прошли проверку")
        self.details = details


def _time(raw: Any, name: str, errors: list[dict[str, str]]) -> time | None:
    if raw in (None, ""):
        return None
    try:
        hh, mm = str(raw).split(":")[:2]
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        errors.append({"field": name, "message": "Время в формате ЧЧ:ММ"})
        return None


def _channels(raw: Any, allowed: tuple[str, ...], name: str, errors: list[dict[str, str]]) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(c, str) for c in raw):
        errors.append({"field": name, "message": "Список каналов"})
        return ()
    bad = [c for c in raw if c not in allowed]
    if bad:
        errors.append({"field": name, "message": f"Неизвестный канал: {', '.join(bad)}"})
    return tuple(c for c in allowed if c in raw)


def parse_prefs(username: str, body: dict[str, Any]) -> Prefs:
    """Тело PUT /api/v1/alerts/preferences → Prefs. Ошибки копятся и уходят одним PrefsError."""
    errors: list[dict[str, str]] = []
    email = (body.get("email") or "").strip() or None
    if email is not None and not EMAIL_RE.match(email):
        errors.append({"field": "email", "message": "Похоже, в адресе почты опечатка"})
    timezone = str(body.get("timezone") or "Europe/Moscow")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        errors.append({"field": "timezone", "message": "Неизвестный часовой пояс"})
    quiet_from = _time(body.get("quiet_from"), "quiet_from", errors)
    quiet_to = _time(body.get("quiet_to"), "quiet_to", errors)
    if (quiet_from is None) != (quiet_to is None):
        errors.append({"field": "quiet_to", "message": "Укажите и начало, и конец тихих часов"})
    digest_time = _time(body.get("digest_time"), "digest_time", errors) or time(9, 0)
    digest_channels = _channels(body.get("digest_channels", ["email"]), DIGEST_CHANNELS, "digest_channels", errors)

    events: dict[str, EventPref] = {}
    raw_events = body.get("events") or {}
    if not isinstance(raw_events, dict):
        errors.append({"field": "events", "message": "Объект с настройками по типам событий"})
        raw_events = {}
    for code in raw_events:
        if code not in EVENT_BY_CODE:
            errors.append({"field": f"events.{code}", "message": "Неизвестный тип события"})
    for definition in EVENTS:
        raw = raw_events.get(definition.code)
        base = default_event_pref(definition)
        if raw is None:
            events[definition.code] = base
            continue
        if not isinstance(raw, dict):
            errors.append({"field": f"events.{definition.code}", "message": "Ожидался объект"})
            continue
        prefix = f"events.{definition.code}"
        threshold = raw.get("threshold", base.threshold)
        if not isinstance(threshold, int | float) or isinstance(threshold, bool):
            errors.append({"field": f"{prefix}.threshold", "message": "Порог — число"})
            threshold = base.threshold
        elif not definition.min_threshold <= threshold <= definition.max_threshold:
            errors.append(
                {
                    "field": f"{prefix}.threshold",
                    "message": f"Порог от {definition.min_threshold:g} до {definition.max_threshold:g}",
                }
            )
        period = base.period_h
        if definition.default_period_h is not None:
            period = raw.get("period_h", base.period_h)
            if period not in PERIODS_H:
                errors.append({"field": f"{prefix}.period_h", "message": "Период: 1, 6, 24, 72 или 168 часов"})
                period = base.period_h
        events[definition.code] = EventPref(
            enabled=bool(raw.get("enabled", base.enabled)),
            threshold=float(threshold),
            period_h=period,
            channels=_channels(raw.get("channels", list(base.channels)), CHANNELS, f"{prefix}.channels", errors),
        )

    digest_enabled = bool(body.get("digest_enabled", False))
    uses_email = ("email" in digest_channels and digest_enabled) or any(
        p.enabled and "email" in p.channels for p in events.values()
    )
    if uses_email and email is None:
        errors.append({"field": "email", "message": "Чтобы получать письма, укажите адрес почты"})
    if errors:
        raise PrefsError(errors)
    return Prefs(
        username=username,
        email=email,
        timezone=timezone,
        quiet_from=quiet_from,
        quiet_to=quiet_to,
        digest_enabled=digest_enabled,
        digest_time=digest_time,
        digest_channels=digest_channels,
        events=events,
    )


def prefs_to_storage(prefs: Prefs) -> dict[str, Any]:
    return {
        code: {"enabled": p.enabled, "threshold": p.threshold, "period_h": p.period_h, "channels": list(p.channels)}
        for code, p in prefs.events.items()
    }


def prefs_from_row(row: dict[str, Any]) -> Prefs:
    stored = row.get("events") or {}
    events: dict[str, EventPref] = {}
    for definition in EVENTS:
        raw = stored.get(definition.code)
        base = default_event_pref(definition)
        if not isinstance(raw, dict):
            events[definition.code] = base
            continue
        events[definition.code] = EventPref(
            enabled=bool(raw.get("enabled", base.enabled)),
            threshold=float(raw.get("threshold", base.threshold)),
            period_h=raw.get("period_h", base.period_h),
            channels=tuple(c for c in CHANNELS if c in (raw.get("channels") or [])),
        )
    return Prefs(
        username=row["username"],
        email=row.get("email"),
        timezone=row.get("timezone") or "Europe/Moscow",
        quiet_from=row.get("quiet_from"),
        quiet_to=row.get("quiet_to"),
        digest_enabled=bool(row.get("digest_enabled")),
        digest_time=row.get("digest_time") or time(9, 0),
        digest_channels=tuple(row.get("digest_channels") or ("email",)),
        mute_until=row.get("mute_until"),
        events=events,
    )


def prefs_to_json(prefs: Prefs) -> dict[str, Any]:
    def hhmm(value: time | None) -> str | None:
        return None if value is None else value.strftime("%H:%M")

    return {
        "username": prefs.username,
        "email": prefs.email,
        "timezone": prefs.timezone,
        "quiet_from": hhmm(prefs.quiet_from),
        "quiet_to": hhmm(prefs.quiet_to),
        "digest_enabled": prefs.digest_enabled,
        "digest_time": hhmm(prefs.digest_time),
        "digest_channels": list(prefs.digest_channels),
        "mute_until": None if prefs.mute_until is None else prefs.mute_until.isoformat(),
        "events": {
            code: {"enabled": p.enabled, "threshold": p.threshold, "period_h": p.period_h, "channels": list(p.channels)}
            for code, p in prefs.events.items()
        },
    }


def catalog_json() -> list[dict[str, Any]]:
    return [
        {
            "code": e.code,
            "title": e.title,
            "description": e.description,
            "unit": e.unit,
            "default_threshold": e.default_threshold,
            "min_threshold": e.min_threshold,
            "max_threshold": e.max_threshold,
            "default_period_h": e.default_period_h,
            "default_enabled": e.default_enabled,
        }
        for e in EVENTS
    ]


def in_quiet_hours(prefs: Prefs, now: datetime) -> datetime | None:
    """Если сейчас тихие часы пользователя — момент их окончания (UTC-aware), иначе None."""
    if prefs.quiet_from is None or prefs.quiet_to is None or prefs.quiet_from == prefs.quiet_to:
        return None
    local = now.astimezone(prefs.tz())
    start, end, current = prefs.quiet_from, prefs.quiet_to, local.time()
    crosses_midnight = start > end
    inside = (current >= start or current < end) if crosses_midnight else (start <= current < end)
    if not inside:
        return None
    end_local = local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if end_local <= local:
        end_local += timedelta(days=1)
    return end_local
