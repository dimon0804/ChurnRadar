"""Еженедельный отчёт для руководства: данные и HTML. PDF собирает report_pdf.py.

В отчёте: риск и ожидаемая потеря, MRR/ARPU/LTV (те же числа, что /analytics/marketing),
динамика потерь за четыре недели (alerts.metric_snapshots — их пишет alerts после каждого
пересчёта), прогноз по дням, топ клиентов под риском, главные причины и что сделали за
неделю: письма, рассылки, отметки по клиентам и помогло ли.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import structlog

from app import mailer, marketing

log = structlog.get_logger()

PERIOD_DAYS = 7
TOP_CUSTOMERS = 10
TOP_REASONS = 5

_TOP_SQL = """
SELECT customer_id, email, risk_score, risk_level, expected_loss, monthly_revenue, reasons
FROM scoring.customer_scores
WHERE risk_level IN ('high', 'medium')
ORDER BY expected_loss DESC, risk_score DESC NULLS LAST, customer_id
LIMIT $1
"""

_DYNAMICS_SQL = """
SELECT DISTINCT ON (at::date) at::date AS day, expected_loss, high_count, risk_share, customers
FROM alerts.metric_snapshots
WHERE at > now() - make_interval(days => $1)
ORDER BY at::date, at DESC
"""

_ACTIONS_SQL = """
SELECT
  (SELECT count(*) FROM scoring.mail_campaigns WHERE created_at > now() - make_interval(days => $1)) AS campaigns,
  (SELECT count(*) FROM scoring.customer_actions_log
    WHERE changed_at > now() - make_interval(days => $1) AND status = 'resolved') AS resolved,
  (SELECT count(*) FROM scoring.customer_actions_log
    WHERE changed_at > now() - make_interval(days => $1) AND status = 'snoozed') AS snoozed
"""


def money(cents: int | float | None) -> str:
    """Евроценты → «€12 345». Обычный пробел: PDF и почтовые клиенты не теряют его."""
    if cents is None:
        return "—"
    value = round(float(cents) / 100)
    return ("−" if value < 0 else "") + "€" + f"{abs(value):,}".replace(",", " ")


def pct(share: float | None, digits: int = 1) -> str:
    if share is None:
        return "—"
    return f"{share * 100:.{digits}f}".replace(".", ",") + "%"


def num(value: float | int | None, digits: int = 0) -> str:
    if value is None:
        return "—"
    text = f"{value:,.{digits}f}".replace(",", " ")
    return text.replace(".", ",")


def change(now: float | None, before: float | None) -> float | None:
    if now is None or before in (None, 0):
        return None
    return (now - before) / before


@dataclass
class Kpi:
    title: str
    value: str
    hint: str = ""


async def _safe_fetch(pool: asyncpg.Pool, sql: str, *args: Any) -> list[Any]:
    """Таблицы alerts может не быть (сервис alerts не развёрнут) — тогда без динамики."""
    try:
        async with pool.acquire() as conn:
            return list(await conn.fetch(sql, *args))
    except (asyncpg.UndefinedTableError, asyncpg.InvalidSchemaNameError):
        return []


async def collect(pool: asyncpg.Pool, cache: Any, now: datetime | None = None) -> dict[str, Any]:
    """Все числа отчёта из базы. cache — SummaryCache api."""
    summary = await cache.get(pool)
    base, _ = await cache.marketing(pool)
    mkt = marketing.build_response(base, summary, await marketing.get_settings(pool))
    async with pool.acquire() as conn:
        top = await conn.fetch(_TOP_SQL, TOP_CUSTOMERS)
        acts = await conn.fetchrow(_ACTIONS_SQL, PERIOD_DAYS)
    dynamics = await _safe_fetch(pool, _DYNAMICS_SQL, 28)
    mail = await mailer.stats(pool, PERIOD_DAYS)
    return build(summary, mkt, [dict(r) for r in top], [dict(r) for r in dynamics], mail, dict(acts), now)


def build(
    summary: dict[str, Any],
    mkt: dict[str, Any],
    top: list[dict[str, Any]],
    dynamics: list[dict[str, Any]],
    mail: dict[str, Any],
    acts: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Отчёт как словарь: его же рендерят HTML и PDF, по нему же проверяют тесты."""
    now = now or datetime.now(UTC)
    by_level = summary.get("by_level") or {}
    history = [
        {"day": d["day"].isoformat() if hasattr(d["day"], "isoformat") else str(d["day"]),
         "expected_loss": int(d["expected_loss"]), "high_count": int(d["high_count"]),
         "risk_share": float(d["risk_share"])}
        for d in dynamics
    ]
    week_ago = None
    if history:
        cutoff = (now - timedelta(days=PERIOD_DAYS)).date().isoformat()
        older = [h for h in history if h["day"] <= cutoff]
        week_ago = older[-1] if older else history[0]
    loss = int(summary.get("expected_loss") or 0)
    loss_change = change(loss, week_ago["expected_loss"]) if week_ago and len(history) > 1 else None
    high_change = (
        change(by_level.get("high"), week_ago["high_count"]) if week_ago and len(history) > 1 else None
    )
    forecast = [
        {"date": p["date"], "expected_loss": int(p.get("expected_loss") or 0)}
        for p in summary.get("timeline") or []
        if p.get("kind") == "forecast"
    ]
    reasons = [
        {"title": r.get("title") or r.get("code"), "customers": int(r.get("customers") or 0),
         "expected_loss": int(r.get("expected_loss") or 0)}
        for r in (summary.get("top_reasons") or [])[:TOP_REASONS]
    ]
    customers = []
    for row in top:
        row_reasons = row.get("reasons") or []
        first = row_reasons[0] if row_reasons else {}
        customers.append({
            "id": row["customer_id"],
            "email": row["email"],
            "risk_score": None if row["risk_score"] is None else round(float(row["risk_score"]), 1),
            "risk_level": row["risk_level"],
            "expected_loss": int(row["expected_loss"]),
            "monthly_revenue": int(row["monthly_revenue"]),
            "reason": first.get("title") if isinstance(first, dict) else None,
        })
    churn = mkt.get("churn") or {}
    return {
        "generated_at": now.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "period_from": (now - timedelta(days=PERIOD_DAYS)).date().isoformat(),
        "period_to": now.date().isoformat(),
        "scored_at": summary.get("scored_at"),
        "model_version": summary.get("model_version"),
        "horizon_days": summary.get("horizon_days") or 30,
        "kpi": {
            "customers_total": int(summary.get("customers_total") or 0),
            "high": int(by_level.get("high") or 0),
            "medium": int(by_level.get("medium") or 0),
            "expected_loss": loss,
            "expected_loss_change": loss_change,
            "high_change": high_change,
            "exposure": int(summary.get("exposure") or 0),
            "revenue_at_risk_share": summary.get("revenue_at_risk_share"),
            "mrr": int(mkt.get("mrr") or 0),
            "arpu": int(mkt.get("arpu") or 0),
            "ltv": int(mkt.get("ltv") or 0),
            "ltv_at_risk": int(mkt.get("ltv_at_risk") or 0),
            "churn_monthly": churn.get("customer_monthly"),
            "expected_mrr_loss": int(churn.get("expected_mrr_loss") or 0),
        },
        "history": history,
        "forecast": forecast,
        "reasons": reasons,
        "customers": customers,
        "done": {
            "emails_sent": int(mail.get("sent") or 0),
            "emails_failed": int(mail.get("failed") or 0),
            "customers_contacted": int(mail.get("customers") or 0),
            "helped": int(mail.get("helped") or 0),
            "helped_share": mail.get("helped_share"),
            "campaigns": int(acts.get("campaigns") or 0),
            "resolved": int(acts.get("resolved") or 0),
            "snoozed": int(acts.get("snoozed") or 0),
        },
    }


def kpis(report: dict[str, Any]) -> list[Kpi]:
    k = report["kpi"]
    loss_hint = f"за {report['horizon_days']} дней"
    if k["expected_loss_change"] is not None:
        sign = "+" if k["expected_loss_change"] >= 0 else ""
        loss_hint += f", {sign}{pct(k['expected_loss_change'])} к прошлой неделе"
    return [
        Kpi("Ожидаемая потеря", money(k["expected_loss"]), loss_hint),
        Kpi("Под высоким риском", num(k["high"]), f"клиентов, ещё {num(k['medium'])} — средний риск"),
        Kpi("Выручка под риском", pct(k["revenue_at_risk_share"]), f"из MRR {money(k['mrr'])}"),
        Kpi("ARPU", money(k["arpu"]), "в месяц на активного клиента"),
        Kpi("LTV", money(k["ltv"]), f"под риском {money(k['ltv_at_risk'])}"),
        Kpi("Отток в месяц", pct(k["churn_monthly"]), f"ожидаем потерять {money(k['expected_mrr_loss'])} MRR"),
    ]


def done_lines(report: dict[str, Any]) -> list[str]:
    d = report["done"]
    lines = [
        f"Писем клиентам отправлено: {num(d['emails_sent'])} (клиентов: {num(d['customers_contacted'])})",
        f"Рассылок запущено: {num(d['campaigns'])}",
        f"Клиентов отмечено «Решено»: {num(d['resolved'])}, отложено: {num(d['snoozed'])}",
    ]
    if d["customers_contacted"]:
        lines.append(
            f"Помогло: {num(d['helped'])} из {num(d['customers_contacted'])} ({pct(d['helped_share'], 0)}) — "
            "после письма снизился риск или клиент пополнил баланс"
        )
    else:
        lines.append("Писем за неделю не было — оценить, помогло ли, пока не на чем")
    if d["emails_failed"]:
        lines.append(f"Не доставлено писем: {num(d['emails_failed'])}")
    return lines


LEVEL_TITLES = {"high": "высокий", "medium": "средний", "low": "низкий", "unknown": "нет данных"}


def _svg_line(points: list[int], width: int = 640, height: int = 160, color: str = "#dc2626") -> str:
    if len(points) < 2:
        return ""
    top = max(points) or 1
    step = width / (len(points) - 1)
    coords = " ".join(f"{i * step:.1f},{height - 10 - (v / top) * (height - 30):.1f}" for i, v in enumerate(points))
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="график">'
        f'<polyline fill="none" stroke="{color}" stroke-width="2.5" points="{coords}"/></svg>'
    )


def render_html(report: dict[str, Any], *, for_email: bool = False) -> str:
    e = html.escape
    k_cards = "".join(
        f'<div class="kpi"><div class="t">{e(k.title)}</div><div class="v">{e(k.value)}</div>'
        f'<div class="h">{e(k.hint)}</div></div>'
        for k in kpis(report)
    )
    rows = "".join(
        f"<tr><td>#{c['id']}</td><td>{e(c['email'])}</td><td>{e(LEVEL_TITLES.get(c['risk_level'], c['risk_level']))}"
        f"</td><td class='n'>{num(c['risk_score'], 1)}</td><td class='n'>{money(c['expected_loss'])}</td>"
        f"<td>{e(c['reason'] or '—')}</td></tr>"
        for c in report["customers"]
    ) or "<tr><td colspan='6'>Клиентов под риском нет</td></tr>"
    reasons = "".join(
        f"<tr><td>{e(r['title'])}</td><td class='n'>{num(r['customers'])}</td>"
        f"<td class='n'>{money(r['expected_loss'])}</td></tr>"
        for r in report["reasons"]
    ) or "<tr><td colspan='3'>Причин пока нет</td></tr>"
    done = "".join(f"<li>{e(line)}</li>" for line in done_lines(report))
    charts = ""
    if not for_email:
        hist = _svg_line([h["expected_loss"] for h in report["history"]])
        fc = _svg_line([p["expected_loss"] for p in report["forecast"]], color="#2563eb")
        charts = (
            "<h2>Динамика ожидаемой потери за 4 недели</h2>"
            + (hist or "<p class='muted'>Истории пока мало: точки появляются после каждого пересчёта.</p>")
            + f"<h2>Прогноз потерь по дням на {report['horizon_days']} дней</h2>"
            + (fc or "<p class='muted'>Прогноза нет.</p>")
        )
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ChurnRadar — еженедельный отчёт</title>
<style>
body{{font-family:Arial,Helvetica,sans-serif;color:#111827;background:#fff;margin:0;padding:24px;max-width:860px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 8px}}
.muted{{color:#6b7280;font-size:13px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px;margin-top:16px}}
.kpi{{border:1px solid #e5e7eb;border-radius:10px;padding:12px}}
.kpi .t{{font-size:12px;color:#6b7280}} .kpi .v{{font-size:22px;font-weight:700;margin:4px 0}}
.kpi .h{{font-size:12px;color:#374151}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
td,th{{border-bottom:1px solid #e5e7eb;padding:6px 8px;text-align:left}} th{{color:#6b7280;font-weight:600}}
td.n{{text-align:right;white-space:nowrap}}
</style></head><body>
<h1>ChurnRadar — еженедельный отчёт</h1>
<div class="muted">Период {e(report['period_from'])} — {e(report['period_to'])}. Данные пересчёта
{e(str(report['scored_at'] or '—'))}, модель v{e(str(report['model_version'] or 0))}.
Суммы в евро.</div>
<div class="grid">{k_cards}</div>
{charts}
<h2>Топ клиентов под риском</h2>
<table><thead><tr><th>ID</th><th>Почта</th><th>Риск</th><th>Score</th><th>Потеря</th><th>Главная причина</th></tr>
</thead><tbody>{rows}</tbody></table>
<h2>Главные причины</h2>
<table><thead><tr><th>Причина</th><th>Клиентов</th><th>Потеря</th></tr></thead><tbody>{reasons}</tbody></table>
<h2>Что сделали за неделю</h2><ul>{done}</ul>
<p class="muted">Сформировано {e(report['generated_at'])}.</p>
</body></html>"""


def filename(report: dict[str, Any], ext: str) -> str:
    return f"churnradar-weekly-{report['period_to']}.{ext}"
