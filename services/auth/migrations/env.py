from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings

VERSION_SCHEMA = "auth"


def do_run_migrations(connection) -> None:
    connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {VERSION_SCHEMA}"))
    context.configure(
        connection=connection,
        target_metadata=None,
        version_table="alembic_version",
        version_table_schema=VERSION_SCHEMA,
    )
    with context.begin_transaction():
        # advisory lock: several replicas may start migrations at the same time
        connection.execute(text("SELECT pg_advisory_xact_lock(74107401)"))
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(get_settings().sqlalchemy_url)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
        await connection.commit()
    await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=get_settings().sqlalchemy_url,
        literal_binds=True,
        version_table_schema=VERSION_SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_async_migrations())
