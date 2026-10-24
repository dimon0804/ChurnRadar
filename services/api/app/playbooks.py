"""Сценарии удержания: хранение, исполнитель после пересчёта, предпросмотр, журнал, эффект.

Почему исполнитель живёт в api, а не в alerts. Всё, что делает сценарий, уже есть в api:
шаблоны писем, очередь рассылки (MailWorker), статусы клиента. В alerts пришлось бы
повторять подстановки и запись писем, а два сервиса писали бы одни и те же таблицы.

Как узнаём о пересчёте. scoring после каждого пересчёта, полного или частичного, в той же
транзакции переписывает строку scoring.summary. Исполнитель раз в PLAYBOOK_POLL_INTERVAL_S
сверяет её xmin и scored_at с запомненной отметкой — это то же событие scores.updated, но
без своего durable-потребителя NATS и без потери событий, пока api перезапускается.

Лидер. Проход идёт в одной транзакции под pg_try_advisory_xact_lock: реплик api может быть
несколько, сработает одна, а новая отметка пишется в той же транзакции, что и срабатывания.
Если проход упал — откатится всё, и следующий повторит его целиком.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime, timedelta
from typing import Annotated, Any

import asyncpg
import structlog
from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import Response
from pydantic import ValidationError
from svckit.auth import User, make_current_user, make_require_admin
from svckit.db import get_pool
from svckit.errors import ApiError
from svckit.mail import MailConfig

from app import actions, mail_templates, mailer, metrics
from app import playbook_rules as rules
from app.config import settings
from app.queries import BIGINT_MAX, iso_z

log = structlog.get_logger()

LOCK_KEY = 0x43525042  # «CRPB»
SAMPLE_SIZE = 20
NOTIFY_LIST = 5

_STAMP_SQL = (
    "SELECT xmin::text || '/' || scored_at::text FROM scoring.summary WHERE id = 1"
)

# Только нужное для условий: services целиком не тянем, считаем признаки в SQL.
_SERVICES = "(CASE WHEN jsonb_typeof(cs.detail -> 'services') = 'array' THEN cs.detail -> 'services' ELSE '[]'::jsonb END)"
_CANDIDATES_SQL = f"""
SELECT cs.customer_id, cs.email, cs.risk_level, cs.risk_score, cs.expected_loss, cs.monthly_revenue,
       cs.balance, cs.total_replenished, cs.reasons -> 0 ->> 'code' AS main_reason,
       EXISTS (SELECT 1 FROM jsonb_array_elements({_SERVICES}) s
               WHERE s ->> 'status' IN ('active', 'prolong_wait', 'suspended') AND s ->> 'auto_prolong' = 'false')
           AS autoprolong_off,
       (SELECT min((s ->> 'days_to_expiry')::float8) FROM jsonb_array_elements({_SERVICES}) s
         WHERE s ->> 'status' IN ('active', 'prolong_wait') AND jsonb_typeof(s -> 'days_to_expiry') = 'number'
           AND (s ->> 'days_to_expiry')::float8 >= 0) AS min_days_to_expiry,
       pl.risk_level AS prev_level, le.last_email_at
FROM scoring.customer_scores cs
LEFT JOIN scoring.playbook_levels pl ON pl.customer_id = cs.customer_id
LEFT JOIN (
    SELECT customer_id, max(COALESCE(sent_at, created_at)) AS last_email_at FROM scoring.customer_emails
    WHERE status IN ('queued', 'sending', 'sent') GROUP BY customer_id
) le ON le.customer_id = cs.customer_id
"""


def _candidate(row: asyncpg.Record) -> rules.Candidate:
    return rules.Candidate(
        customer_id=row["customer_id"],
        email=row["email"] or "",
        risk_level=row["risk_level"],
        risk_score=row["risk_score"],
        expected_loss=row["expected_loss"],
        monthly_revenue=row["monthly_revenue"],
        balance=row["balance"],
        total_replenished=row["total_replenished"],
        main_reason=row["main_reason"],
        prev_level=row["prev_level"],
        autoprolong_off=bool(row["autoprolong_off"]),
        min_days_to_expiry=row["min_days_to_expiry"],
        last_email_at=row["last_email_at"],
    )


async def _load_candidates(conn: asyncpg.Connection) -> list[rules.Candidate]:
    return [_candidate(r) for r in await conn.fetch(_CANDIDATES_SQL)]


async def _levels_ready(conn: asyncpg.Connection) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT levels_ready FROM scoring.playbook_state WHERE id = 1"
        )
    )


# --- хранение ------------------------------------------------------------------------------

_COLUMNS = (
    "p.id, p.name, p.description, p.enabled, p.mode, p.conditions, p.actions, p.control_share, p.max_per_day, "
    "p.cooldown_days, p.preset, p.created_at, p.created_by, p.updated_at, p.updated_by"
)
_LIST_SQL = f"""
SELECT {_COLUMNS},
       (SELECT max(created_at) FROM scoring.playbook_runs r WHERE r.playbook_id = p.id) AS last_run_at,
       (SELECT count(*) FROM scoring.playbook_hits h WHERE h.playbook_id = p.id AND h.grp = 'treated') AS treated,
       (SELECT count(*) FROM scoring.playbook_hits h WHERE h.playbook_id = p.id AND h.grp = 'control') AS control,
       (SELECT count(*) FROM scoring.playbook_hits h WHERE h.playbook_id = p.id AND h.email_id IS NOT NULL) AS emails
FROM scoring.playbooks p
"""


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def playbook_out(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "enabled": row["enabled"],
        "mode": row["mode"],
        "conditions": _json(row["conditions"]),
        "actions": _json(row["actions"]),
        "control_share": row["control_share"],
        "max_per_day": row["max_per_day"],
        "cooldown_days": row["cooldown_days"],
        "preset": row["preset"],
        "created_at": iso_z(row["created_at"]),
        "created_by": row["created_by"],
        "updated_at": iso_z(row["updated_at"]),
        "updated_by": row["updated_by"],
        "last_run_at": iso_z(row["last_run_at"]),
        "stats": {
            "treated": row["treated"],
            "control": row["control"],
            "emails": row["emails"],
        },
    }


def _model(row: asyncpg.Record) -> rules.PlaybookIn:
    return rules.PlaybookIn(
        name=row["name"],
        description=row["description"],
        enabled=row["enabled"],
        mode=row["mode"],
        conditions=_json(row["conditions"]),
        actions=_json(row["actions"]),
        control_share=row["control_share"],
        max_per_day=row["max_per_day"],
        cooldown_days=row["cooldown_days"],
        preset=row["preset"],
    )


def check_templates(pb: rules.PlaybookIn) -> None:
    for action in pb.actions:
        if (
            action.type == "email"
            and action.template_code is not None
            and action.template_code not in mail_templates.BY_CODE
        ):
            raise ApiError(
                422,
                "validation_error",
                f"Шаблона «{action.template_code}» нет",
                [{"field": "actions", "message": "Неизвестный шаблон", "type": "enum"}],
            )


def _check_id(playbook_id: int) -> None:
    if playbook_id < 1 or playbook_id > BIGINT_MAX:
        raise ApiError(404, "not_found", "Сценарий не найден")


async def _get_row(conn: asyncpg.Connection, playbook_id: int) -> asyncpg.Record:
    _check_id(playbook_id)
    row = await conn.fetchrow(_LIST_SQL + " WHERE p.id = $1", playbook_id)
    if row is None:
        raise ApiError(404, "not_found", "Сценарий не найден")
    return row


def _dump(pb: rules.PlaybookIn) -> tuple[str, str]:
    return (
        json.dumps(
            pb.conditions.model_dump(exclude_none=True, exclude_defaults=True),
            ensure_ascii=False,
        ),
        json.dumps([a.model_dump() for a in pb.actions], ensure_ascii=False),
    )


async def list_playbooks(pool: asyncpg.Pool, config: MailConfig) -> dict[str, Any]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(_LIST_SQL + " ORDER BY p.id")
    return {
        "items": [playbook_out(r) for r in rows],
        "presets": list(rules.PRESETS),
        "reasons": [{"code": k, "title": v} for k, v in rules.REASONS.items()],
        "mail_enabled": config.enabled,
    }


async def get_playbook(pool: asyncpg.Pool, playbook_id: int) -> dict[str, Any]:
    async with pool.acquire() as conn:
        return playbook_out(await _get_row(conn, playbook_id))


async def create_playbook(
    pool: asyncpg.Pool, pb: rules.PlaybookIn, user: str
) -> dict[str, Any]:
    check_templates(pb)
    cond, acts = _dump(pb)
    async with pool.acquire() as conn:
        new_id = await conn.fetchval(
            """
            INSERT INTO scoring.playbooks (name, description, enabled, mode, conditions, actions, control_share,
                max_per_day, cooldown_days, preset, created_by, updated_by)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, $9, $10, $11, $11) RETURNING id
            """,
            pb.name.strip(),
            (pb.description or "").strip() or None,
            pb.enabled,
            pb.mode,
            cond,
            acts,
            pb.control_share,
            pb.max_per_day,
            pb.cooldown_days,
            pb.preset,
            user,
        )
        return playbook_out(await _get_row(conn, new_id))


async def update_playbook(
    pool: asyncpg.Pool, playbook_id: int, pb: rules.PlaybookIn, user: str
) -> dict[str, Any]:
    check_templates(pb)
    _check_id(playbook_id)
    cond, acts = _dump(pb)
    async with pool.acquire() as conn:
        done = await conn.fetchval(
            """
            UPDATE scoring.playbooks SET name = $2, description = $3, enabled = $4, mode = $5, conditions = $6::jsonb,
                actions = $7::jsonb, control_share = $8, max_per_day = $9, cooldown_days = $10, preset = $11,
                updated_at = now(), updated_by = $12
            WHERE id = $1 RETURNING id
            """,
            playbook_id,
            pb.name.strip(),
            (pb.description or "").strip() or None,
            pb.enabled,
            pb.mode,
            cond,
            acts,
            pb.control_share,
            pb.max_per_day,
            pb.cooldown_days,
            pb.preset,
            user,
        )
        if done is None:
            raise ApiError(404, "not_found", "Сценарий не найден")
        return playbook_out(await _get_row(conn, playbook_id))


async def delete_playbook(pool: asyncpg.Pool, playbook_id: int) -> None:
    _check_id(playbook_id)
    async with pool.acquire() as conn:
        done = await conn.fetchval(
            "DELETE FROM scoring.playbooks WHERE id = $1 RETURNING id", playbook_id
        )
    if done is None:
        raise ApiError(404, "not_found", "Сценарий не найден")


# --- отбор и выполнение --------------------------------------------------------------------


async def _cooldown(
    conn: asyncpg.Connection, playbook_id: int | None, days: int
) -> set[int]:
    if playbook_id is None:
        return set()
    rows = await conn.fetch(
        "SELECT DISTINCT customer_id FROM scoring.playbook_hits "
        "WHERE playbook_id = $1 AND hit_at > now() - make_interval(days => $2)",
        playbook_id,
        days,
    )
    return {r["customer_id"] for r in rows}


async def _treated_today(conn: asyncpg.Connection, playbook_id: int | None) -> int:
    if playbook_id is None:
        return 0
    return await conn.fetchval(
        "SELECT count(*) FROM scoring.playbook_hits WHERE playbook_id = $1 AND grp = 'treated' "
        "AND hit_at >= date_trunc('day', now())",
        playbook_id,
    )


async def plan(
    conn: asyncpg.Connection,
    pb: rules.PlaybookIn,
    playbook_id: int | None,
    candidates: list[rules.Candidate],
    levels_ready: bool,
) -> rules.Selection:
    now = await conn.fetchval("SELECT now()")
    hit = [
        c
        for c in candidates
        if rules.matches(pb.conditions, c, now, levels_ready=levels_ready)
    ]
    return rules.select(
        hit,
        share=pb.control_share,
        cooldown=await _cooldown(conn, playbook_id, pb.cooldown_days),
        treated_today=await _treated_today(conn, playbook_id),
        max_per_day=pb.max_per_day,
        needs_email=pb.needs_email,
    )


def selection_out(sel: rules.Selection) -> dict[str, Any]:
    sample = sorted(
        [(c, "treated") for c in sel.treated] + [(c, "control") for c in sel.control],
        key=lambda pair: (-pair[0].expected_loss, pair[0].customer_id),
    )[:SAMPLE_SIZE]
    return {
        "matched": sel.matched,
        "treated": len(sel.treated),
        "control": len(sel.control),
        "skipped_cooldown": sel.skipped_cooldown,
        "skipped_limit": sel.skipped_limit,
        "skipped_no_email": sel.skipped_no_email,
        "sample": [
            {
                "customer_id": c.customer_id,
                "email": c.email,
                "risk_level": c.risk_level,
                "expected_loss": c.expected_loss,
                "main_reason": c.main_reason,
                "group": group,
            }
            for c, group in sample
        ],
    }


async def preview(
    pool: asyncpg.Pool, pb: rules.PlaybookIn, playbook_id: int | None
) -> dict[str, Any]:
    check_templates(pb)
    async with pool.acquire() as conn:
        levels_ready = await _levels_ready(conn)
        sel = await plan(
            conn, pb, playbook_id, await _load_candidates(conn), levels_ready
        )
    return {**selection_out(sel), "levels_ready": levels_ready}


class Executor:
    """Выполняет действия сценария в текущей транзакции. Письма — в очередь MailWorker."""

    def __init__(
        self, conn: asyncpg.Connection, config: MailConfig, reply_to: str
    ) -> None:
        self.conn = conn
        self.config = config
        self.reply_to = reply_to
        self.emails = 0
        self._templates: dict[str, dict[str, Any]] | None = None
        self._ref: datetime | None = None

    async def _prepare(self) -> None:
        if self._templates is None:
            self._templates = await mail_templates.load_all(self.conn)
            self._ref = (await self.conn.fetchrow(mailer._NOW_SQL))["ref"]

    async def run(
        self,
        playbook_id: int,
        run_id: int,
        pb: rules.PlaybookIn,
        sel: rules.Selection,
        label: str,
    ) -> None:
        notify: dict[str, list[rules.Candidate]] = {}
        full = (
            await mailer._customers(self.conn, [c.customer_id for c in sel.treated])
            if pb.needs_email
            else {}
        )
        now = await self.conn.fetchval("SELECT now()")
        for c in sel.control:
            await self._hit(playbook_id, run_id, c, "control", [], None)
        for c in sel.treated:
            results: list[dict[str, Any]] = []
            email_id = None
            for action in pb.actions:
                if action.type == "email":
                    email_id, result = await self._email(
                        c, full.get(c.customer_id), action.template_code, label
                    )
                elif action.type == "status":
                    until = (
                        now + timedelta(days=action.days)
                        if action.status == "snoozed" and action.days
                        else None
                    )
                    ok = await actions.record_action(
                        self.conn, c.customer_id, action.status, until, label, label
                    )
                    result = "done" if ok else "not_found"
                else:
                    notify.setdefault(action.username, []).append(c)
                    result = "queued"
                results.append({"type": action.type, "result": result})
                if result in ("queued", "done"):
                    metrics.playbook_actions.labels(action=action.type).inc()
            await self._hit(playbook_id, run_id, c, "treated", results, email_id)
        for username, customers in notify.items():
            await self._notify(username, pb.name, customers)

    async def _email(
        self,
        c: rules.Candidate,
        customer: dict[str, Any] | None,
        code: str | None,
        label: str,
    ) -> tuple[int | None, str]:
        if not self.config.enabled:
            return None, "mail_disabled"
        if customer is None or not customer["email"]:
            return None, "no_email"
        await self._prepare()
        assert self._templates is not None and self._ref is not None
        code, subject, text = mailer._compose(
            customer, self._templates, code, self._ref, self.config.from_name
        )
        email_id = await mailer._snapshot_insert(
            self.conn,
            customer,
            campaign_id=None,
            reply_to=self.reply_to,
            code=code,
            subject=subject,
            text=text,
            status="queued",
            user=label,
        )
        self.emails += 1
        return email_id, "queued"

    async def _hit(
        self,
        playbook_id: int,
        run_id: int,
        c: rules.Candidate,
        grp: str,
        results: list[dict[str, Any]],
        email_id: int | None,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO scoring.playbook_hits (playbook_id, run_id, customer_id, grp, risk_level_at, risk_score_at,
                expected_loss_at, balance_at, replenished_at, actions, email_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11)
            """,
            playbook_id,
            run_id,
            c.customer_id,
            grp,
            c.risk_level,
            c.risk_score,
            c.expected_loss,
            c.balance,
            c.total_replenished,
            json.dumps(results),
            email_id,
        )

    async def _notify(
        self, username: str, name: str, customers: list[rules.Candidate]
    ) -> None:
        """Колокольчик в панели — лента alerts.notifications. Нет сервиса alerts — только журнал."""
        if (
            await self.conn.fetchval("SELECT to_regclass('alerts.notifications')")
            is None
        ):
            return
        shown = ", ".join(
            c.email or f"#{c.customer_id}" for c in customers[:NOTIFY_LIST]
        )
        more = (
            f" и ещё {len(customers) - NOTIFY_LIST}"
            if len(customers) > NOTIFY_LIST
            else ""
        )
        try:
            async with self.conn.transaction():
                await self.conn.execute(
                    "INSERT INTO alerts.notifications (username, event_type, title, message, link) "
                    "VALUES ($1, 'playbook', $2, $3, '/playbooks')",
                    username,
                    f"Сценарий «{name}»: {len(customers)} клиент(ов)",
                    f"Сработал сценарий, посмотрите клиентов: {shown}{more}.",
                )
        except asyncpg.PostgresError as exc:
            log.warning("playbook_notify_failed", username=username, error=str(exc))


async def execute(
    conn: asyncpg.Connection,
    playbook_id: int,
    pb: rules.PlaybookIn,
    candidates: list[rules.Candidate],
    levels_ready: bool,
    *,
    cause: str,
    user: str | None,
    config: MailConfig,
    reply_to: str,
) -> dict[str, Any]:
    """Один запуск сценария внутри открытой транзакции. В симуляции только запись в журнал."""
    sel = await plan(conn, pb, playbook_id, candidates, levels_ready)
    metrics.playbook_runs.labels(cause=cause, mode=pb.mode).inc()
    if cause == "scores" and not sel.treated and not sel.control:
        # пустые проходы после каждого пересчёта журнал не засоряют
        return {"run_id": None, "mode": pb.mode, "emails": 0, **selection_out(sel)}
    run_id = await conn.fetchval(
        """
        INSERT INTO scoring.playbook_runs (playbook_id, cause, mode, matched, treated, control,
            skipped_cooldown, skipped_limit, skipped_no_email, created_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING id
        """,
        playbook_id,
        cause,
        pb.mode,
        sel.matched,
        len(sel.treated),
        len(sel.control),
        sel.skipped_cooldown,
        sel.skipped_limit,
        sel.skipped_no_email,
        user,
    )
    emails = 0
    if pb.mode == "live":
        executor = Executor(conn, config, reply_to)
        await executor.run(playbook_id, run_id, pb, sel, f"сценарий «{pb.name}»")
        emails = executor.emails
        await conn.execute(
            "UPDATE scoring.playbook_runs SET emails = $2 WHERE id = $1", run_id, emails
        )
        metrics.playbook_holdout.inc(len(sel.control))
    return {"run_id": run_id, "mode": pb.mode, "emails": emails, **selection_out(sel)}


async def run_now(
    pool: asyncpg.Pool, playbook_id: int, user: str, config: MailConfig, reply_to: str
) -> tuple[dict[str, Any], int]:
    """Запуск из панели. Ждёт, пока закончит исполнитель, чтобы не обработать клиента дважды."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", LOCK_KEY)
        row = await _get_row(conn, playbook_id)
        pb = _model(row)
        result = await execute(
            conn,
            playbook_id,
            pb,
            await _load_candidates(conn),
            await _levels_ready(conn),
            cause="manual",
            user=user,
            config=config,
            reply_to=reply_to,
        )
    log.info(
        "playbook_run",
        playbook_id=playbook_id,
        cause="manual",
        treated=result["treated"],
        user=user,
    )
    return result, result["emails"]


# --- исполнитель по пересчёту ---------------------------------------------------------------


class PlaybookRunner:
    def __init__(
        self,
        pool: asyncpg.Pool,
        config: MailConfig,
        *,
        reply_to: str,
        interval_s: float,
        on_emails: Any = None,
    ) -> None:
        self.pool = pool
        self.config = config
        self.reply_to = reply_to
        self.interval_s = interval_s
        self.on_emails = on_emails
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="playbook-runner")

    def kick(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except (
                Exception
            ) as exc:  # база недоступна и т.п. — попробуем на следующем круге
                log.warning("playbook_runner_error", error=str(exc))
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self.interval_s)

    async def run_once(self) -> list[dict[str, Any]] | None:
        """Один проход. None — пересчёта не было или проход делает другая реплика."""
        async with self.pool.acquire() as conn, conn.transaction():
            if not await conn.fetchval(
                "SELECT pg_try_advisory_xact_lock($1)", LOCK_KEY
            ):
                return None
            stamp = await conn.fetchval(_STAMP_SQL)
            state = await conn.fetchrow(
                "SELECT last_stamp, levels_ready FROM scoring.playbook_state WHERE id = 1"
            )
            if stamp is None or state is None or state["last_stamp"] == stamp:
                return None
            rows = await conn.fetch(
                f"SELECT {_COLUMNS} FROM scoring.playbooks p WHERE p.enabled ORDER BY p.id"
            )
            results = []
            if rows:
                candidates = await _load_candidates(conn)
                for row in rows:
                    try:
                        pb = _model(row)
                    except ValidationError as exc:  # правило из старой версии, которое уже не проходит проверку
                        log.warning(
                            "playbook_invalid", playbook_id=row["id"], error=str(exc)
                        )
                        continue
                    result = await execute(
                        conn,
                        row["id"],
                        pb,
                        candidates,
                        state["levels_ready"],
                        cause="scores",
                        user=None,
                        config=self.config,
                        reply_to=self.reply_to,
                    )
                    results.append({"playbook_id": row["id"], **result})
            # уровни «с прошлого раза» для условия «стал high»
            await conn.execute("DELETE FROM scoring.playbook_levels")
            await conn.execute(
                "INSERT INTO scoring.playbook_levels (customer_id, risk_level) "
                "SELECT customer_id, risk_level FROM scoring.customer_scores"
            )
            await conn.execute(
                "UPDATE scoring.playbook_state SET last_stamp = $1, levels_ready = true, updated_at = now() WHERE id = 1",
                stamp,
            )
        emails = sum(r["emails"] for r in results)
        for r in results:
            log.info(
                "playbook_run",
                playbook_id=r["playbook_id"],
                cause="scores",
                mode=r["mode"],
                matched=r["matched"],
                treated=r["treated"],
                control=r["control"],
                emails=r["emails"],
            )
        if emails and self.on_emails is not None:
            self.on_emails()
        return results


# --- журнал и эффект ------------------------------------------------------------------------


def run_out(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": row["id"],
        "cause": row["cause"],
        "mode": row["mode"],
        "matched": row["matched"],
        "treated": row["treated"],
        "control": row["control"],
        "skipped_cooldown": row["skipped_cooldown"],
        "skipped_limit": row["skipped_limit"],
        "skipped_no_email": row["skipped_no_email"],
        "emails": row["emails"],
        "created_at": iso_z(row["created_at"]),
        "created_by": row["created_by"],
    }


async def runs(pool: asyncpg.Pool, playbook_id: int, limit: int) -> dict[str, Any]:
    async with pool.acquire() as conn:
        await _get_row(conn, playbook_id)
        rows = await conn.fetch(
            "SELECT * FROM scoring.playbook_runs WHERE playbook_id = $1 ORDER BY id DESC LIMIT $2",
            playbook_id,
            limit,
        )
    return {"items": [run_out(r) for r in rows]}


async def hits(pool: asyncpg.Pool, playbook_id: int, limit: int) -> dict[str, Any]:
    async with pool.acquire() as conn:
        await _get_row(conn, playbook_id)
        rows = await conn.fetch(
            """
            SELECT h.id, h.run_id, h.customer_id, h.grp, h.risk_level_at, h.expected_loss_at, h.actions, h.email_id,
                   h.hit_at, cs.email, e.status AS email_status
            FROM scoring.playbook_hits h
            LEFT JOIN scoring.customer_scores cs ON cs.customer_id = h.customer_id
            LEFT JOIN scoring.customer_emails e ON e.id = h.email_id
            WHERE h.playbook_id = $1 ORDER BY h.id DESC LIMIT $2
            """,
            playbook_id,
            limit,
        )
    return {
        "items": [
            {
                "id": r["id"],
                "run_id": r["run_id"],
                "customer_id": r["customer_id"],
                "email": r["email"],
                "group": r["grp"],
                "risk_level": r["risk_level_at"],
                "expected_loss": r["expected_loss_at"],
                "actions": _json(r["actions"]),
                "email_id": r["email_id"],
                "email_status": r["email_status"],
                "hit_at": iso_z(r["hit_at"]),
            }
            for r in rows
        ]
    }


_UPLIFT_SQL = """
SELECT h.grp, h.risk_score_at, h.replenished_at, cs.risk_score, cs.total_replenished, cs.risk_level,
       cs.customer_id IS NOT NULL AS present
FROM (
    SELECT DISTINCT ON (customer_id) customer_id, grp, risk_score_at, replenished_at
    FROM scoring.playbook_hits
    WHERE playbook_id = $1 AND hit_at <= now() - make_interval(days => $2)
    ORDER BY customer_id, hit_at
) h
LEFT JOIN scoring.customer_scores cs ON cs.customer_id = h.customer_id
"""


def outcome(row: asyncpg.Record | dict[str, Any]) -> tuple[str, rules.Outcome]:
    before, now = row["risk_score_at"], row["risk_score"]
    return row["grp"], rules.Outcome(
        risk_down=before is not None and now is not None and now < before,
        topped_up=row["total_replenished"] is not None
        and row["total_replenished"] > row["replenished_at"],
        retained=bool(row["present"]) and row["risk_level"] not in (None, "unknown"),
    )


async def uplift(pool: asyncpg.Pool, playbook_id: int) -> dict[str, Any]:
    """Эффект: у срабатываний старше N дней сравниваем состояние сейчас с моментом срабатывания."""
    windows = []
    async with pool.acquire() as conn:
        await _get_row(conn, playbook_id)
        for days in rules.WINDOWS:
            groups: dict[str, list[rules.Outcome]] = {"treated": [], "control": []}
            for row in await conn.fetch(_UPLIFT_SQL, playbook_id, days):
                grp, out = outcome(row)
                groups[grp].append(out)
            result = rules.compare(groups["treated"], groups["control"])
            windows.append({"days": days, **result, "summary": rules.explain(result)})
    return {
        "playbook_id": playbook_id,
        "min_group": rules.MIN_GROUP,
        "windows": windows,
    }


# --- HTTP -----------------------------------------------------------------------------------

_current_user = make_current_user(settings)
CurrentUser = Annotated[User, Depends(_current_user)]
# Писать клиентам от имени сценария может тот же, кто правит шаблоны. Роли маркетолога ещё
# нет — когда появится, достаточно поменять эту зависимость.
Writer = Annotated[User, Depends(make_require_admin(_current_user))]
Pool = Annotated[Any, Depends(get_pool)]

router = APIRouter(prefix="/api/v1/playbooks", tags=["playbooks"])


def _config(request: Request) -> MailConfig:
    return request.app.state.mail_config


def _reply_to(request: Request) -> str:
    return mailer.reply_to_for(None, settings.smtp_reply_to, _config(request))


@router.get("")
async def http_list(request: Request, _: CurrentUser, pool: Pool) -> dict[str, Any]:
    return await list_playbooks(pool, _config(request))


@router.post("", status_code=201)
async def http_create(
    user: Writer, pool: Pool, body: Annotated[rules.PlaybookIn, Body()]
) -> dict[str, Any]:
    item = await create_playbook(pool, body, user.name)
    log.info(
        "playbook_created",
        playbook_id=item["id"],
        mode=item["mode"],
        enabled=item["enabled"],
        user=user.name,
    )
    return item


@router.post("/preview")
async def http_preview(
    _: CurrentUser,
    pool: Pool,
    body: Annotated[rules.PlaybookIn, Body()],
    playbook_id: Annotated[int | None, Query(ge=1, le=BIGINT_MAX)] = None,
) -> dict[str, Any]:
    return await preview(pool, body, playbook_id)


@router.get("/{playbook_id}")
async def http_get(playbook_id: int, _: CurrentUser, pool: Pool) -> dict[str, Any]:
    return await get_playbook(pool, playbook_id)


@router.put("/{playbook_id}")
async def http_update(
    playbook_id: int,
    user: Writer,
    pool: Pool,
    body: Annotated[rules.PlaybookIn, Body()],
) -> dict[str, Any]:
    item = await update_playbook(pool, playbook_id, body, user.name)
    log.info(
        "playbook_updated",
        playbook_id=playbook_id,
        mode=item["mode"],
        enabled=item["enabled"],
        user=user.name,
    )
    return item


@router.delete("/{playbook_id}", status_code=204, response_class=Response)
async def http_delete(playbook_id: int, user: Writer, pool: Pool) -> Response:
    await delete_playbook(pool, playbook_id)
    log.info("playbook_deleted", playbook_id=playbook_id, user=user.name)
    return Response(status_code=204)


@router.post("/{playbook_id}/run")
async def http_run(
    request: Request, playbook_id: int, user: Writer, pool: Pool
) -> dict[str, Any]:
    result, emails = await run_now(
        pool, playbook_id, user.name, _config(request), _reply_to(request)
    )
    worker = getattr(request.app.state, "mail_worker", None)
    if emails and worker is not None:
        worker.kick()
    return result


@router.get("/{playbook_id}/runs")
async def http_runs(
    playbook_id: int,
    _: CurrentUser,
    pool: Pool,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> dict[str, Any]:
    return await runs(pool, playbook_id, limit)


@router.get("/{playbook_id}/hits")
async def http_hits(
    playbook_id: int,
    _: CurrentUser,
    pool: Pool,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> dict[str, Any]:
    return await hits(pool, playbook_id, limit)


@router.get("/{playbook_id}/uplift")
async def http_uplift(playbook_id: int, _: CurrentUser, pool: Pool) -> dict[str, Any]:
    return await uplift(pool, playbook_id)
