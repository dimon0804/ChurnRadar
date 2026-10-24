"""Проверка критических событий по порогам одного подписчика и тексты уведомлений. Без БД и сети."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from .catalog import EventPref
from .rules import BULK_PATH, HIGH_RISK_PATH, money, plural

NBSP = " "


@dataclass(frozen=True, slots=True)
class Snapshot:
    at: datetime
    expected_loss: int  # евроценты
    high_count: int
    risk_share: float  # 0..1
    customers: int


@dataclass(frozen=True, slots=True)
class Transition:
    """Клиент перешёл в высокий риск за этот пересчёт."""

    customer_id: int
    email: str | None
    expected_loss: int
    prev_level: str | None


@dataclass(frozen=True, slots=True)
class Notice:
    event_type: str
    title: str
    text: str
    link: str  # путь в панели
    lines: tuple[str, ...] = ()  # подробности списком (клиенты, цифры)


def pct(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",").replace(",0", "") + f"{NBSP}%"


def period_label(hours: int | None) -> str:
    if not hours:
        return ""
    if hours % 24 == 0:
        days = hours // 24
        return "сутки" if days == 1 else f"{days} {plural(days, ('день', 'дня', 'дней'))}"
    return f"{hours} {plural(hours, ('час', 'часа', 'часов'))}"


def baseline_time(now: datetime, pref: EventPref, last_sent_at: datetime | None) -> datetime:
    """С какого момента считать рост: начало периода, но не раньше прошлого уведомления —
    иначе один и тот же рост приходил бы при каждом пересчёте."""
    start = now - timedelta(hours=pref.period_h or 24)
    if last_sent_at is not None and last_sent_at > start:
        return last_sent_at
    return start


def check_loss_growth(cur: Snapshot, base: Snapshot | None, pref: EventPref) -> Notice | None:
    if base is None or base.expected_loss <= 0:
        return None
    grown = (cur.expected_loss - base.expected_loss) / base.expected_loss * 100
    if grown <= pref.threshold:
        return None
    return Notice(
        "loss_growth",
        f"Ожидаемая потеря выросла на {pct(grown)} за {period_label(pref.period_h)}",
        f"Было {money(base.expected_loss)}, стало {money(cur.expected_loss)}. Ваш порог — {pct(pref.threshold, 0)}.",
        "/",
    )


def check_high_count(cur: Snapshot, base: Snapshot | None, pref: EventPref) -> Notice | None:
    if base is None:
        return None
    grown = cur.high_count - base.high_count
    if grown <= pref.threshold:
        return None
    return Notice(
        "high_count_growth",
        f"В высоком риске на {grown} {plural(grown, ('клиента', 'клиента', 'клиентов'))} больше, "
        f"чем {period_label(pref.period_h)} назад",
        f"Было {base.high_count}, стало {cur.high_count}. Ваш порог — {pref.threshold:g}.",
        HIGH_RISK_PATH,
    )


def check_risk_share(cur: Snapshot, prev: Snapshot | None, pref: EventPref) -> Notice | None:
    """Только пересечение порога снизу вверх: пока доля выше, повторно не сообщаем."""
    if prev is None:
        return None
    limit = pref.threshold / 100
    if not (prev.risk_share < limit <= cur.risk_share):
        return None
    return Notice(
        "risk_share",
        f"Под риском {pct(cur.risk_share * 100)} выручки",
        f"Доля выручки, которую ожидаем потерять, перешла ваш порог {pct(pref.threshold, 0)} "
        f"(было {pct(prev.risk_share * 100)}).",
        "/",
    )


def check_big_customers(transitions: Sequence[Transition], pref: EventPref, top_n: int = 5) -> Notice | None:
    """Одно уведомление на пересчёт, сколько бы крупных клиентов ни перешло в высокий риск."""
    limit = round(pref.threshold * 100)
    big = sorted((t for t in transitions if t.expected_loss >= limit), key=lambda t: -t.expected_loss)
    if not big:
        return None
    lines = tuple(
        f"#{t.customer_id}{f' ({t.email})' if t.email else ''}: ожидаемая потеря {money(t.expected_loss)}"
        for t in big[:top_n]
    )
    if len(big) > top_n:
        lines += (f"…и ещё {len(big) - top_n}",)
    if len(big) == 1:
        t = big[0]
        who = f"#{t.customer_id}" + (f" ({t.email})" if t.email else "")
        return Notice(
            "big_customer_high",
            f"Крупный клиент {who} перешёл в высокий риск",
            f"Ожидаемая потеря {money(t.expected_loss)}, ваш порог — {money(limit)}.",
            f"/customers/{t.customer_id}",
        )
    total = sum(t.expected_loss for t in big)
    moved = plural(len(big), ("крупный клиент перешёл", "крупных клиента перешли", "крупных клиентов перешли"))
    return Notice(
        "big_customer_high",
        f"{len(big)} {moved} в высокий риск",
        f"Вместе это {money(total)} ожидаемой потери, ваш порог на клиента — {money(limit)}.",
        BULK_PATH,
        lines,
    )


def check_stale(
    last_change_at: datetime | None, scored_at: datetime | None, now: datetime, pref: EventPref
) -> Notice | None:
    """Источник прислал изменения позже последнего пересчёта, и прошло больше порога минут."""
    if last_change_at is None or (scored_at is not None and scored_at >= last_change_at):
        return None
    waited = (now - last_change_at).total_seconds() / 60
    if waited <= pref.threshold:
        return None
    return Notice(
        "stale_data",
        f"Оценки не пересчитывались {int(waited)} мин после новых данных",
        "Источник прислал изменения, а scoring их не обработал. Панель показывает устаревшие оценки: "
        "проверьте сервис scoring и шину NATS.",
        "/data",
    )


def digest_notice(cur: Snapshot, day_ago: Snapshot | None, top: Sequence[Transition], events_today: int) -> Notice:
    loss = money(cur.expected_loss)
    if day_ago is not None:
        loss += f" ({'+' if cur.expected_loss >= day_ago.expected_loss else '−'}"
        loss += f"{money(abs(cur.expected_loss - day_ago.expected_loss))} за сутки)"
    lines = (
        f"Клиентов в высоком риске: {cur.high_count} из {cur.customers}",
        f"Ожидаемая потеря: {loss}",
        f"Под риском {pct(cur.risk_share * 100)} выручки",
        f"Критических событий за сутки: {events_today}",
    )
    if top:
        lines += (
            "Самые дорогие под риском:",
            *(f"  #{t.customer_id}{f' ({t.email})' if t.email else ''}: {money(t.expected_loss)}" for t in top),
        )
    return Notice("digest", "Сводка ChurnRadar за сутки", "Коротко о состоянии базы.", BULK_PATH, lines)


def probe_notice() -> Notice:
    return Notice(
        "test",
        "Тестовое уведомление",
        "Если вы это читаете, канал настроен правильно. Настоящие уведомления придут, когда сработает "
        "одно из выбранных событий.",
        "/notifications",
    )
