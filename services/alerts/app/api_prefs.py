"""HTTP: личные настройки уведомлений, личная лента, привязка Telegram, раздел администратора.

Без `from __future__ import annotations`: FastAPI должен видеть Depends(...) из замыкания как объект.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Path, Query
from fastapi.responses import Response
from svckit.auth import User, make_require_admin
from svckit.errors import ApiError

from . import repository as repo
from .bot import deep_link
from .catalog import CHANNELS, PERIODS_H, Prefs, PrefsError, catalog_json, parse_prefs, prefs_from_row, prefs_to_json
from .config import Config
from .fanout import Fanout, describe
from .notify import probe_notice

USERNAME_PATH = Path(min_length=1, max_length=200)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def install_prefs_routes(app: FastAPI, config: Config, state: Any, current_user: Callable[..., User]) -> None:
    CurrentUser = Annotated[User, Depends(current_user)]
    AdminUser = Annotated[User, Depends(make_require_admin(current_user))]
    fanout = Fanout(config)

    def pool() -> Any:
        if state.pool is None:
            raise ApiError(503, "service_unavailable", "База данных недоступна")
        return state.pool

    async def channels_info(conn: Any, prefs: Prefs) -> dict[str, Any]:
        link = await repo.link_of_user(conn, prefs.username)
        bot = getattr(state, "bot", None)
        return {
            "panel": {"available": True, "reason": None},
            "email": {
                "available": config.mail.enabled,
                "reason": None if config.mail.enabled else "Почтовый сервер не настроен (SMTP_HOST)",
            },
            "telegram": {
                "available": config.bot_enabled,
                "reason": None if config.bot_enabled else "Бот не настроен: не задан TELEGRAM_BOT_TOKEN",
                "bot_username": bot.username if bot is not None else None,
                "linked": link is not None,
                "tg_username": None if link is None else link.get("tg_username"),
            },
        }

    async def prefs_response(conn: Any, prefs: Prefs) -> dict[str, Any]:
        return {
            "preferences": prefs_to_json(prefs),
            "catalog": catalog_json(),
            "periods_h": list(PERIODS_H),
            "channels": await channels_info(conn, prefs),
        }

    @app.get("/api/v1/alerts/preferences", tags=["alerts"])
    async def get_preferences(user: CurrentUser) -> dict[str, Any]:
        async with pool().acquire() as conn:
            prefs = await repo.get_prefs(conn, user.name)
            return await prefs_response(conn, prefs)

    @app.put("/api/v1/alerts/preferences", tags=["alerts"])
    async def put_preferences(user: CurrentUser, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        try:
            prefs = parse_prefs(user.name, body)
        except PrefsError as exc:
            raise ApiError(422, "validation_error", str(exc), exc.details) from exc
        async with pool().acquire() as conn:
            await repo.save_prefs(conn, prefs, user.name)
            return await prefs_response(conn, await repo.get_prefs(conn, user.name))

    @app.post("/api/v1/alerts/preferences/test", tags=["alerts"])
    async def send_test(user: CurrentUser) -> dict[str, Any]:
        """Пробное уведомление во все каналы, которые у пользователя есть, без тихих часов и паузы."""
        now = datetime.now(UTC)
        async with pool().acquire() as conn, conn.transaction():
            prefs = await repo.get_prefs(conn, user.name)
            chats = await repo.chats_by_user(conn)
            result = await fanout.deliver(
                conn,
                prefs,
                probe_notice(),
                ("panel", "email", "telegram"),
                now=now,
                chats=chats,
                respect_quiet=False,
            )
        kick = getattr(state, "kick", None)
        if kick is not None:
            kick()
        return {"results": describe(result)}

    @app.post("/api/v1/alerts/telegram/link", tags=["alerts"])
    async def telegram_link(user: CurrentUser) -> dict[str, Any]:
        bot = getattr(state, "bot", None)
        if not config.bot_enabled or bot is None:
            raise ApiError(409, "conflict", "Telegram-бот не настроен: администратору нужно задать TELEGRAM_BOT_TOKEN")
        username = await bot.resolve_username()
        if not username:
            raise ApiError(503, "service_unavailable", "Telegram не ответил, попробуйте через минуту")
        ttl = timedelta(seconds=config.telegram_link_ttl_s)
        async with pool().acquire() as conn:
            code = await repo.create_link_code(conn, user.name, ttl)
        return {"url": deep_link(username, code), "expires_in_s": int(ttl.total_seconds())}

    @app.delete("/api/v1/alerts/telegram/link", tags=["alerts"], status_code=204)
    async def telegram_unlink(user: CurrentUser) -> Response:
        async with pool().acquire() as conn:
            await repo.unlink(conn, username=user.name)
        return Response(status_code=204)

    @app.get("/api/v1/alerts/notifications", tags=["alerts"])
    async def my_notifications(user: CurrentUser, limit: Annotated[int, Query(ge=1, le=100)] = 30) -> dict[str, Any]:
        async with pool().acquire() as conn:
            await repo.get_prefs(conn, user.name)  # первый визит: подписка по умолчанию
        rows, unread = await repo.list_notifications(pool(), user.name, limit)
        return {
            "items": [
                {
                    "id": r["id"],
                    "event_type": r["event_type"],
                    "title": r["title"],
                    "message": r["message"],
                    "link": r["link"],
                    "created_at": _iso(r["created_at"]),
                    "read": r["read_at"] is not None,
                }
                for r in rows
            ],
            "unread": unread,
        }

    @app.post("/api/v1/alerts/notifications/read", tags=["alerts"], status_code=204)
    async def read_notifications(user: CurrentUser) -> Response:
        await repo.mark_read(pool(), user.name)
        return Response(status_code=204)

    # --- администратор ------------------------------------------------------------------------

    @app.get("/api/v1/alerts/preferences/users", tags=["alerts"])
    async def list_user_prefs(_: AdminUser) -> dict[str, Any]:
        async with pool().acquire() as conn:
            rows = await repo.prefs_overview(conn)
        items = []
        for row in rows:
            prefs = prefs_from_row(row)
            enabled = [code for code, p in prefs.events.items() if p.enabled and p.channels]
            used = {c for p in prefs.events.values() if p.enabled for c in p.channels}
            channels = [c for c in CHANNELS if c in used]
            items.append(
                {
                    "username": prefs.username,
                    "email": prefs.email,
                    "enabled_events": enabled,
                    "channels": channels,
                    "digest_enabled": prefs.digest_enabled,
                    "telegram_linked": bool(row.get("telegram_linked")),
                    "tg_username": row.get("tg_username"),
                    "updated_at": _iso(row.get("updated_at")),
                    "updated_by": row.get("updated_by"),
                }
            )
        return {"items": items}

    @app.get("/api/v1/alerts/preferences/users/{username}", tags=["alerts"])
    async def get_user_prefs(_: AdminUser, username: Annotated[str, USERNAME_PATH]) -> dict[str, Any]:
        async with pool().acquire() as conn:
            prefs = await repo.get_prefs(conn, username, create=False)
            return await prefs_response(conn, prefs)

    @app.delete("/api/v1/alerts/preferences/users/{username}", tags=["alerts"], status_code=204)
    async def reset_user_prefs(_: AdminUser, username: Annotated[str, USERNAME_PATH]) -> Response:
        async with pool().acquire() as conn:
            if not await repo.reset_prefs(conn, username):
                raise ApiError(404, "not_found", "У пользователя нет сохранённых настроек")
        return Response(status_code=204)
