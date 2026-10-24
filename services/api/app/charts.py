"""Данные для графиков панели. Всё агрегируется в Postgres, в браузер уходят готовые ряды.

* distribution — распределение score по десяти корзинам с разбивкой по уровням и деньги по уровням;
* scatter — точки «риск × ожидаемая потеря» (не больше SCATTER_MAX, самые дорогие);
* reason — всё о причине риска: сколько клиентов, сколько денег, как распределён вклад, что делать;
* customer — графики карточки: баланс против графика продлений, платежи по месяцам, score в истории;
* history — как менялись главные цифры сводки (scoring пишет точку после пересчёта).
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

import asyncpg
from svckit.errors import ApiError

from app.queries import BIGINT_MAX, check_score_range, iso_z, parse_levels, where_clause

ReasonScope = Literal["main", "any"]

BUCKET_WIDTH = 10
BUCKETS = 10
SCORED_LEVELS = ("high", "medium", "low")
LEVELS = ("high", "medium", "low", "unknown")
SCATTER_MAX = 400
SCATTER_DEFAULT = 300
# Корзины вклада причины в score клиента, пунктов: [0, 5), [5, 10), ... [50, 100].
CONTRIBUTION_EDGES = (0, 5, 10, 20, 30, 50, 100)
TOP_CUSTOMERS = 5
TOP_ACTIONS = 3
PAYMENT_MONTHS = 12
RUNWAY_LIMIT_DAYS = 365
RUNWAY_TAIL_DAYS = 7
SCORE_POINTS = 20
HISTORY_MAX = 500

# Причины без оценки: признаков модели за ними нет, описание держим здесь.
UNKNOWN_REASONS: dict[str, dict[str, str]] = {
    "all_services_gone": {
        "title": "Уже ушёл",
        "action": "Предложить вернуться",
        "plain": "Все услуги клиента удалены или заблокированы. Риск ухода не считаем: клиент уже ушёл, "
        "его можно только вернуть.",
    },
    "no_live_services": {
        "title": "Нет услуг",
        "action": "Помочь с первым заказом",
        "plain": "Клиент зарегистрировался, но ничего не заказал. Терять пока нечего, зато можно помочь "
        "с первым заказом.",
    },
    "no_paid_services": {
        "title": "Нет платных услуг",
        "action": "Предложить платный тариф",
        "plain": "У клиента есть только бесплатные услуги, денег он не приносит, поэтому риск не считаем.",
    },
}

# Признаки модели простыми словами: что видит модель и почему это тревожный знак.
PLAIN: dict[str, str] = {
    "prolong_wait": "Срок оплаты услуги уже прошёл, она ждёт продления. Если клиент не заплатит "
    "за несколько дней, услугу удалят.",
    "autoprolong_off_expiring": "Скоро заканчивается оплаченный период, а автопродление выключено. "
    "Без ручной оплаты услуга просто закончится.",
    "insufficient_funds": "Автопродление включено, но денег на балансе не хватит на ближайшие списания. "
    "Продление сорвётся, если клиент не пополнит счёт.",
    "suspended": "Сервер клиента остановлен. Часто так выглядит первый шаг к отказу от услуги.",
    "failed_payments": "Платежи за продление не проходят: карта отклонена или денег не хватило.",
    "short_commitment": "Услуга оплачивается на короткий срок, например по часам или по неделям. "
    "От неё легко отказаться в любой момент.",
    "churn_history": "Клиент уже отказывался от части услуг. Кто ушёл один раз, уходит снова чаще других.",
    "inactivity": "Клиент давно ничего не оплачивал, хотя по сроку своих услуг уже должен был.",
    "cancel_page_views": "Клиент заходил на страницу удаления или отмены услуги: прикидывает, как уйти. "
    "Данные из Яндекс Метрики.",
    "panel_absence": "Клиент давно не заходил в панель управления: сервис ему, похоже, больше не нужен. "
    "Данные из Яндекс Метрики.",
    "web_activity_drop": "Клиент стал заметно реже заходить на сайт, чем обычно. Данные из Яндекс Метрики.",
    "bonus_dependency": "Услуги оплачиваются в основном бонусами. Когда бонусы закончатся, "
    "клиенту придётся платить своими, и он может уйти.",
    "withdraw_pressure": "Большую часть баланса можно вывести. Клиент держит деньги у нас, "
    "но в любой момент может их забрать.",
}

_BY_LEVEL_SQL = """
SELECT risk_level, count(*) AS customers, COALESCE(sum(monthly_revenue), 0)::bigint AS monthly_revenue,
       COALESCE(sum(expected_loss), 0)::bigint AS expected_loss
FROM scoring.customer_scores GROUP BY risk_level
"""
_BUCKETS_SQL = f"""
SELECT LEAST(GREATEST(floor(risk_score / {BUCKET_WIDTH})::int, 0), {BUCKETS - 1}) AS bucket, risk_level,
       count(*) AS customers, COALESCE(sum(expected_loss), 0)::bigint AS expected_loss
FROM scoring.customer_scores WHERE risk_score IS NOT NULL GROUP BY 1, 2
"""
_META_SQL = "SELECT scored_at, model_version FROM scoring.summary WHERE id = 1"
_AS_OF_SQL = "SELECT COALESCE((SELECT reference_time FROM source.clock WHERE id = 1), now()) AS as_of"


def _meta(row: Any) -> dict[str, Any]:
    if row is None:
        return {"scored_at": None, "model_version": 0}
    return {"scored_at": iso_z(row["scored_at"]), "model_version": row["model_version"]}


# --- распределение -----------------------------------------------------------------------------


def build_distribution(level_rows: list[Any], bucket_rows: list[Any]) -> dict[str, Any]:
    buckets = [
        {
            "from": i * BUCKET_WIDTH,
            "to": (i + 1) * BUCKET_WIDTH,
            "customers": 0,
            "expected_loss": 0,
            "by_level": dict.fromkeys(SCORED_LEVELS, 0),
        }
        for i in range(BUCKETS)
    ]
    for row in bucket_rows:
        bucket = buckets[int(row["bucket"])]
        bucket["customers"] += row["customers"]
        bucket["expected_loss"] += row["expected_loss"]
        if row["risk_level"] in bucket["by_level"]:
            bucket["by_level"][row["risk_level"]] += row["customers"]
    found = {row["risk_level"]: row for row in level_rows}
    levels = [
        {
            "level": level,
            "customers": found[level]["customers"] if level in found else 0,
            "monthly_revenue": found[level]["monthly_revenue"] if level in found else 0,
            "expected_loss": found[level]["expected_loss"] if level in found else 0,
        }
        for level in LEVELS
    ]
    return {"buckets": buckets, "levels": levels}


async def distribution(pool: asyncpg.Pool) -> dict[str, Any]:
    async with pool.acquire() as conn:
        level_rows = await conn.fetch(_BY_LEVEL_SQL)
        bucket_rows = await conn.fetch(_BUCKETS_SQL)
        meta = await conn.fetchrow(_META_SQL)
    return {**build_distribution(list(level_rows), list(bucket_rows)), **_meta(meta)}


# --- точки «риск × потеря» ---------------------------------------------------------------------


async def scatter(
    pool: asyncpg.Pool,
    *,
    level: str | None,
    search: str | None,
    reason: str | None,
    reason_scope: ReasonScope,
    limit: int,
    score_min: float | None = None,
    score_max: float | None = None,
) -> dict[str, Any]:
    """Самые дорогие клиенты с оценкой под текущими фильтрами списка; total — сколько их всего."""
    check_score_range(score_min, score_max)
    where, args = where_clause(
        parse_levels(level), search, reason=reason, reason_scope=reason_scope, score_min=score_min, score_max=score_max
    )
    where = f"{where} AND risk_score IS NOT NULL" if where else " WHERE risk_score IS NOT NULL"
    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT count(*) FROM scoring.customer_scores{where}", *args)
        rows = await conn.fetch(
            "SELECT customer_id, email, risk_score, risk_level, expected_loss, monthly_revenue "
            f"FROM scoring.customer_scores{where} ORDER BY expected_loss DESC, customer_id ASC LIMIT ${len(args) + 1}",
            *args,
            limit,
        )
        meta = await conn.fetchrow(_META_SQL)
    points = [
        {
            "id": r["customer_id"],
            "email": r["email"],
            "risk_score": r["risk_score"],
            "risk_level": r["risk_level"],
            "expected_loss": r["expected_loss"],
            "monthly_revenue": r["monthly_revenue"],
        }
        for r in rows
    ]
    return {"points": points, "total": total, "shown": len(points), **_meta(meta)}


# --- причина -----------------------------------------------------------------------------------


def reason_info(code: str) -> dict[str, Any] | None:
    """Название, описание и группа причины: из реестра признаков riskcore или из списка «нет оценки»."""
    if code in UNKNOWN_REASONS:
        known = UNKNOWN_REASONS[code]
        return {"title": known["title"], "plain": known["plain"], "description": None, "group": None,
                "default_action": known["action"], "feature": False}
    from riskcore.registry import get_feature

    feature = get_feature(code)
    if feature is None:
        return None
    return {
        "title": feature.title,
        "plain": PLAIN.get(code) or feature.description,
        "description": feature.description,
        "group": feature.group,
        "default_action": None,
        "feature": True,
    }


def reason_condition(reason: str, scope: ReasonScope, position: int) -> str:
    """Условие «у клиента есть причина» для параметра $position. Колонки — из миграции 009."""
    if scope == "main":
        return f"main_reason = ${position}"
    return f"reason_codes ? ${position}"


# Вклад причины у клиента: её пункты в detail.features. Потеря на причину = потеря клиента ×
# вклад / score (score = сумма вкладов), так же считает riskcore в сводке (top_reasons).
_REASON_ROWS = """
WITH c AS (
    SELECT customer_id, email, risk_level, risk_score, monthly_revenue, expected_loss, main_reason,
           (SELECT (f ->> 'contribution')::float8 FROM jsonb_array_elements(detail -> 'features') f
             WHERE f ->> 'code' = $1 LIMIT 1) AS points
    FROM scoring.customer_scores WHERE {cond}
)
"""
_REASON_TOTALS = """
SELECT count(*) AS customers,
       count(*) FILTER (WHERE main_reason = $1) AS primary_customers,
       COALESCE(sum(monthly_revenue), 0)::bigint AS monthly_revenue,
       COALESCE(sum(expected_loss), 0)::bigint AS customers_expected_loss,
       COALESCE(sum(CASE WHEN risk_score > 0 AND points IS NOT NULL
                         THEN expected_loss * points / risk_score ELSE 0 END), 0)::float8 AS expected_loss,
       count(*) FILTER (WHERE risk_level = 'high') AS high,
       count(*) FILTER (WHERE risk_level = 'medium') AS medium,
       count(*) FILTER (WHERE risk_level = 'low') AS low,
       count(*) FILTER (WHERE risk_level = 'unknown') AS unknown,
       avg(points) AS avg_points
FROM c
"""
_REASON_BUCKETS = """
SELECT width_bucket(points, $2::float8[]) AS bucket, count(*) AS customers FROM c
WHERE points IS NOT NULL GROUP BY 1
"""
_REASON_TOP = """
SELECT customer_id, email, risk_level, risk_score, points,
       CASE WHEN risk_score > 0 AND points IS NOT NULL THEN round(expected_loss * points / risk_score)::bigint
            ELSE 0 END AS reason_loss
FROM c ORDER BY reason_loss DESC, expected_loss DESC, customer_id LIMIT $2
"""
# Действие у каждого клиента своё, с его услугой и датой («Включить автопродление: напомнить
# письмом, что vps-1 заканчивается 31.10»). Группируем по общей части до двоеточия.
_REASON_ACTIONS = """
SELECT btrim(split_part(r ->> 'action', ':', 1)) AS action, count(*) AS customers
FROM scoring.customer_scores cs, jsonb_array_elements(cs.reasons) r
WHERE r ->> 'code' = $1 AND COALESCE(r ->> 'action', '') <> '' AND {cond}
GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT $2
"""


def contribution_buckets(rows: list[Any]) -> list[dict[str, Any]]:
    """width_bucket по внутренним границам: 0 — меньше первой, i — между границами i и i+1."""
    inner = CONTRIBUTION_EDGES[1:-1]
    counts: dict[int, int] = defaultdict(int)
    for row in rows:
        counts[int(row["bucket"])] += row["customers"]
    return [
        {"from": CONTRIBUTION_EDGES[i], "to": CONTRIBUTION_EDGES[i + 1], "customers": counts.get(i, 0)}
        for i in range(len(inner) + 1)
    ]


async def reason(pool: asyncpg.Pool, code: str, scope: ReasonScope) -> dict[str, Any]:
    info = reason_info(code)
    async with pool.acquire() as conn:
        cond = reason_condition(code, scope, 1)
        prefix = _REASON_ROWS.format(cond=cond)
        totals = await conn.fetchrow(prefix + _REASON_TOTALS, code)
        if info is None and not totals["customers"]:
            raise ApiError(404, "not_found", f"Причины «{code}» нет")
        buckets = await conn.fetch(prefix + _REASON_BUCKETS, code, list(map(float, CONTRIBUTION_EDGES[1:-1])))
        top = await conn.fetch(prefix + _REASON_TOP, code, TOP_CUSTOMERS)
        actions = await conn.fetch(_REASON_ACTIONS.format(cond=reason_condition(code, scope, 1)), code, TOP_ACTIONS)
        meta = await conn.fetchrow(_META_SQL)
    info = info or {"title": code, "plain": None, "description": None, "group": None,
                    "default_action": None, "feature": False}
    action_rows = [{"title": r["action"], "customers": r["customers"]} for r in actions]
    if not action_rows and info["default_action"]:
        action_rows = [{"title": info["default_action"], "customers": totals["customers"]}]
    return {
        "code": code,
        "scope": scope,
        "title": info["title"],
        "plain": info["plain"],
        "description": info["description"],
        "group": info["group"],
        "customers": totals["customers"],
        "primary_customers": totals["primary_customers"],
        "monthly_revenue": totals["monthly_revenue"],
        "expected_loss": round(totals["expected_loss"]),
        "customers_expected_loss": totals["customers_expected_loss"],
        "avg_contribution": None if totals["avg_points"] is None else float(totals["avg_points"]),
        "by_level": {level: totals[level] for level in LEVELS},
        "contribution": contribution_buckets(list(buckets)),
        "actions": action_rows,
        "top_customers": [
            {
                "id": r["customer_id"],
                "email": r["email"],
                "risk_level": r["risk_level"],
                "risk_score": r["risk_score"],
                "contribution": r["points"],
                "expected_loss": r["reason_loss"],
            }
            for r in top
        ],
        **_meta(meta),
    }


# --- карточка клиента --------------------------------------------------------------------------


def _months_back(as_of: datetime, count: int) -> list[date]:
    first = date(as_of.year, as_of.month, 1)
    out = []
    for i in range(count - 1, -1, -1):
        index = first.year * 12 + first.month - 1 - i
        out.append(date(index // 12, index % 12 + 1, 1))
    return out


def payments_by_month(rows: list[Any], as_of: datetime, months: int = PAYMENT_MONTHS) -> list[dict[str, Any]]:
    found = {(r["month"].year, r["month"].month): r for r in rows}
    out = []
    for month in _months_back(as_of, months):
        row = found.get((month.year, month.month))
        out.append(
            {
                "month": month.isoformat()[:7],
                "paid": int(row["paid"] or 0) if row else 0,
                "payments": int(row["payments"] or 0) if row else 0,
                "failed": int(row["failed"] or 0) if row else 0,
            }
        )
    return out


def runway_window(as_of: datetime, runs_out_at: datetime | None, horizon_days: int) -> int:
    """Сколько дней показывать: до момента, когда кончатся деньги, и ещё неделю; не меньше горизонта."""
    days = horizon_days
    if runs_out_at is not None:
        days = max(days, math.ceil((runs_out_at - as_of).total_seconds() / 86_400) + RUNWAY_TAIL_DAYS)
    return max(1, min(days, RUNWAY_LIMIT_DAYS))


def build_runway(
    services: list[Any],
    spendable: int,
    as_of: datetime,
    days: int,
) -> dict[str, Any]:
    """Баланс по дням против графика автопродлений. Продления списываются по очереди во времени,
    как в riskcore.money.shortfall_moment; первое, на которое денег уже нет, — момент, когда
    кончатся деньги. Дальше продления считаются неоплаченными, баланс не уходит в минус."""
    from riskcore.money import add_periods, renewal_plan

    moments: list[tuple[datetime, int, int]] = []
    for service in services:
        plan = renewal_plan(service, as_of, days)
        if plan is None:
            continue
        count = 0
        while (moment := add_periods(plan.first, plan.term, count)) <= plan.end:
            moments.append((moment, plan.price, plan.service_id))
            count += 1
    moments.sort()

    start = as_of.astimezone(UTC).date()
    per_day: dict[date, dict[str, Any]] = {}
    balance = spendable
    runs_out_at: datetime | None = None
    for moment, price, _service_id in moments:
        day = per_day.setdefault(moment.astimezone(UTC).date(), {"renewals": 0, "paid": 0, "unpaid": 0})
        day["renewals"] += 1
        if runs_out_at is None and price <= balance:
            balance -= price
            day["paid"] += price
        else:
            if runs_out_at is None:
                runs_out_at = moment
            day["unpaid"] += price

    points = []
    balance = spendable
    for offset in range(days + 1):
        day = start + timedelta(days=offset)
        info = per_day.get(day, {"renewals": 0, "paid": 0, "unpaid": 0})
        balance -= info["paid"]
        points.append({"date": day.isoformat(), "balance": balance, **info})
    return {
        "start_balance": spendable,
        "days": days,
        "runs_out_at": None if runs_out_at is None else iso_z(runs_out_at),
        "renewals": len(moments),
        "points": points,
    }


_CUSTOMER_SQL = """
SELECT cs.balance, cs.bonus_balance, cs.bonus_state, cs.risk_score, cs.scored_at, cs.detail -> 'money' AS money,
       (SELECT (data ->> 'horizon_days')::int FROM scoring.summary WHERE id = 1) AS horizon_days
FROM scoring.customer_scores cs WHERE cs.customer_id = $1
"""
_SERVICES_SQL = (
    "SELECT id, name, owner_id, payment_term, auto_prolong, created_at, expires_at, status, price "
    "FROM source.services WHERE owner_id = $1"
)
_PAYMENTS_SQL = """
SELECT date_trunc('month', COALESCE(t.performed_at, t.created_at), 'UTC') AS month,
       sum(t.amount) FILTER (WHERE t.status = 'performed') AS paid,
       count(*) FILTER (WHERE t.status = 'performed' AND t.amount > 0) AS payments,
       count(*) FILTER (WHERE t.status = 'not_performed') AS failed
FROM source.transactions t JOIN source.services s ON s.id = t.service_id
WHERE s.owner_id = $1 AND COALESCE(t.performed_at, t.created_at) <= $2
  AND COALESCE(t.performed_at, t.created_at) >= $3
GROUP BY 1
"""
_SCORE_POINTS_SQL = """
SELECT sent_at AS at, risk_score_at_send AS risk_score FROM scoring.customer_emails
WHERE customer_id = $1 AND status = 'sent' AND risk_score_at_send IS NOT NULL AND sent_at IS NOT NULL
ORDER BY sent_at DESC LIMIT $2
"""


def _service(row: Any) -> Any:
    from riskcore.types import Service

    return Service(
        row["id"], row["name"], row["owner_id"], row["payment_term"], row["auto_prolong"],
        row["created_at"], row["expires_at"], row["status"], row["price"],
    )


async def customer(pool: asyncpg.Pool, customer_id: int) -> dict[str, Any]:
    if customer_id < 1 or customer_id > BIGINT_MAX:
        raise ApiError(404, "not_found", "Клиент не найден")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_CUSTOMER_SQL, customer_id)
        if row is None:
            raise ApiError(404, "not_found", f"Клиент {customer_id} не найден или ещё не оценён")
        as_of = (await conn.fetchrow(_AS_OF_SQL))["as_of"].astimezone(UTC)
        services = await conn.fetch(_SERVICES_SQL, customer_id)
        first_month = _months_back(as_of, PAYMENT_MONTHS)[0]
        payments = await conn.fetch(
            _PAYMENTS_SQL, customer_id, as_of, datetime(first_month.year, first_month.month, 1, tzinfo=UTC)
        )
        emails = await conn.fetch(_SCORE_POINTS_SQL, customer_id, SCORE_POINTS)

    money = row["money"] or {}
    spendable = row["balance"] + (row["bonus_balance"] if row["bonus_state"] == "unlocked" else 0)
    stored_out = money.get("funds_run_out_at")
    runs_out = datetime.fromisoformat(stored_out.replace("Z", "+00:00")) if stored_out else None
    days = runway_window(as_of, runs_out, int(row["horizon_days"] or 30))
    runway = build_runway([_service(s) for s in services], spendable, as_of, days)
    # момент из карточки — расчёт модели на время оценки; график строим на текущие данные
    runway["model_runs_out_at"] = stored_out

    score_points = [{"at": iso_z(e["at"]), "risk_score": e["risk_score"], "source": "email"} for e in emails]
    score_points.reverse()
    if score_points and row["risk_score"] is not None:
        score_points.append({"at": iso_z(row["scored_at"]), "risk_score": row["risk_score"], "source": "now"})

    return {
        "customer_id": customer_id,
        "as_of": iso_z(as_of),
        "runway": runway,
        "payments": payments_by_month(list(payments), as_of),
        "score_history": score_points,
    }


# --- история сводки ----------------------------------------------------------------------------

_HISTORY_SQL = """
SELECT scored_at, model_version, full_run, customers_total, high, medium, low, unknown, services_at_risk,
       expected_loss, exposure, monthly_revenue_total, revenue_at_risk_share
FROM (SELECT * FROM scoring.summary_history ORDER BY scored_at DESC, id DESC LIMIT $1) h
ORDER BY scored_at, model_version
"""


async def history(pool: asyncpg.Pool, limit: int) -> dict[str, Any]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(_HISTORY_SQL, limit)
    return {
        "items": [
            {
                "at": iso_z(r["scored_at"]),
                "model_version": r["model_version"],
                "full": r["full_run"],
                "customers_total": r["customers_total"],
                "by_level": {level: r[level] for level in LEVELS},
                "services_at_risk": r["services_at_risk"],
                "expected_loss": r["expected_loss"],
                "exposure": r["exposure"],
                "monthly_revenue_total": r["monthly_revenue_total"],
                "revenue_at_risk_share": r["revenue_at_risk_share"],
            }
            for r in rows
        ]
    }
