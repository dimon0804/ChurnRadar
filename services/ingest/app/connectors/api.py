"""HTTP API коннекторов: /api/v1/source/connectors*, /api/v1/source/metrica*. Только admin."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from svckit.auth import User
from svckit.db import get_pool
from svckit.errors import ApiError

from . import metrica, store
from .models import (
    ClientIdsUpload,
    ConnectorCreate,
    ConnectorPatch,
    allowed_hosts,
    check_base_url,
    validate_options,
)

Pool = Annotated[Any, Depends(get_pool)]
# Что у коннектора из окружения (managed) задаётся только переменными ingest.
MANAGED_FIELDS = ("base_url", "token_env")
MANAGED_OPTIONS = ("counter_id",)


def _not_found(connector_id: str) -> ApiError:
    return ApiError(404, "not_found", f"Коннектор {connector_id} не найден")


def _invalid(message: str, field: str | None = None) -> ApiError:
    return ApiError(422, "validation_error", message, [{"field": field, "message": message, "type": "invalid"}])


def _kick(request: Request) -> None:
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        scheduler.kick()


def build_router(admin: Any, allowed_hosts_raw: Any) -> APIRouter:
    """admin — зависимость FastAPI «нужна роль admin»; allowed_hosts_raw() — текущий список хостов."""
    router = APIRouter(prefix="/api/v1/source")
    AdminUser = Annotated[User, Depends(admin)]

    def check_url(url: str) -> None:
        problem = check_base_url(url, allowed_hosts(allowed_hosts_raw()))
        if problem:
            raise _invalid(problem[0].upper() + problem[1:], "base_url")

    @router.get("/connectors")
    async def list_connectors(request: Request, user: AdminUser, pool: Pool) -> dict[str, Any]:
        scheduler = getattr(request.app.state, "scheduler", None)
        leader = await pool.fetchval(
            "SELECT instance FROM source.connector_runs ORDER BY id DESC LIMIT 1"
        )
        return {
            "items": [store.to_api(r) for r in await store.list_connectors(pool)],
            "scheduler": {
                "enabled": scheduler is not None,
                "leader": scheduler.instance if scheduler is not None and scheduler.is_leader else leader,
            },
        }

    @router.post("/connectors", status_code=201)
    async def create_connector(body: ConnectorCreate, request: Request, user: AdminUser, pool: Pool) -> dict[str, Any]:
        check_url(body.base_url)
        data = body.model_dump()
        try:
            data["options"] = validate_options(body.kind, body.options)
        except ValueError as exc:
            raise _invalid(f"Опции не подходят: {exc}", "options") from exc
        row = await store.create_connector(pool, data, user.name)
        if row is None:
            raise ApiError(409, "conflict", f"Коннектор {body.id} уже есть")
        _kick(request)
        return store.to_api(row)

    @router.get("/connectors/{connector_id}")
    async def get_connector(connector_id: str, user: AdminUser, pool: Pool) -> dict[str, Any]:
        row = await store.get_connector(pool, connector_id)
        if row is None:
            raise _not_found(connector_id)
        return store.to_api(row)

    @router.patch("/connectors/{connector_id}")
    async def patch_connector(
        connector_id: str, body: ConnectorPatch, request: Request, user: AdminUser, pool: Pool
    ) -> dict[str, Any]:
        row = await store.get_connector(pool, connector_id)
        if row is None:
            raise _not_found(connector_id)
        changes = body.model_dump(exclude_unset=True)
        if not changes:
            raise _invalid("Нечего менять: пустой запрос")
        if row["managed"]:
            locked = [f for f in MANAGED_FIELDS if f in changes and changes[f] != row[f]]
            current = store._json(row["options"])
            locked += [
                f"options.{k}" for k in MANAGED_OPTIONS
                if k in (changes.get("options") or {}) and changes["options"][k] != current.get(k)
            ]
            if locked:
                raise _invalid(
                    f"{', '.join(locked)} задаются переменными окружения ingest (коннектор из окружения)", locked[0]
                )
        if "base_url" in changes:
            check_url(changes["base_url"])
        if "options" in changes:
            merged = {**store._json(row["options"]), **(changes["options"] or {})}
            try:
                changes["options"] = validate_options(row["kind"], merged)
            except ValueError as exc:
                raise _invalid(f"Опции не подходят: {exc}", "options") from exc
        updated = await store.update_connector(pool, connector_id, changes, user.name)
        if updated is None:
            raise _not_found(connector_id)
        _kick(request)
        return store.to_api(updated)

    @router.delete("/connectors/{connector_id}", status_code=204)
    async def delete_connector(connector_id: str, user: AdminUser, pool: Pool) -> Response:
        if not await store.delete_connector(pool, connector_id):
            raise _not_found(connector_id)
        return Response(status_code=204)

    @router.post("/connectors/{connector_id}/run", status_code=202)
    async def run_connector(connector_id: str, request: Request, user: AdminUser, pool: Pool) -> dict[str, Any]:
        row = await store.request_run(pool, connector_id)
        if row is None:
            raise _not_found(connector_id)
        _kick(request)
        return store.to_api(row)

    @router.post("/connectors/{connector_id}/reset-cursor")
    async def reset_cursor(connector_id: str, user: AdminUser, pool: Pool) -> dict[str, Any]:
        row = await store.reset_cursor(pool, connector_id, user.name)
        if row is None:
            raise _not_found(connector_id)
        return store.to_api(row)

    @router.get("/connectors/{connector_id}/runs")
    async def list_runs(
        connector_id: str, user: AdminUser, pool: Pool, limit: Annotated[int, Query(ge=1, le=200)] = 20
    ) -> dict[str, Any]:
        if await store.get_connector(pool, connector_id) is None:
            raise _not_found(connector_id)
        return {"items": [store.run_to_api(r) for r in await store.list_runs(pool, connector_id, limit)]}

    @router.get("/metrica")
    async def metrica_summary(user: AdminUser, pool: Pool) -> dict[str, Any]:
        data = await metrica.summary(pool)
        return {
            "client_ids": int(data.get("client_ids") or 0),
            "client_ids_updated_at": store._iso(data.get("client_ids_updated_at")),
            "accounts_with_activity": int(data.get("accounts_with_activity") or 0),
            "last_visit_at": store._iso(data.get("last_visit_at")),
            "computed_at": store._iso(data.get("computed_at")),
        }

    @router.post("/metrica/client-ids")
    async def upload_client_ids(body: ClientIdsUpload, request: Request, user: AdminUser, pool: Pool) -> dict[str, Any]:
        latest: dict[int, int] = {}
        for item in body.items:
            client = int(item.client_id)
            if client >= 2**64:
                raise _invalid(f"clientID {item.client_id} больше UInt64", "items.client_id")
            latest[client] = item.account_id
        async with pool.acquire() as conn, conn.transaction():
            if body.replace:
                await conn.execute("DELETE FROM source.metrica_client_ids")
            if latest:
                await conn.executemany(
                    """
                    INSERT INTO source.metrica_client_ids (client_id, account_id, updated_by)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (client_id) DO UPDATE SET account_id = EXCLUDED.account_id,
                        updated_at = now(), updated_by = EXCLUDED.updated_by
                    """,
                    [(Decimal(c), a, user.name) for c, a in latest.items()],
                )
            # связка поменялась: следующий запуск Метрики перечитает всю глубину с новыми соответствиями
            await store.reset_metrica_cursors(conn)
            total = await conn.fetchval("SELECT count(*) FROM source.metrica_client_ids")
        return {"accepted": len(latest), "total": int(total)}

    return router
