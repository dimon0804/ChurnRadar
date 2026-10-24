from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import asyncpg
import pytest

import migrate

REPO_ROOT = Path(__file__).resolve().parents[3]
DB_URL = os.getenv("TEST_DATABASE_URL")


def test_discover_sorted_and_skips_missing(tmp_path):
    core = tmp_path / "core"
    auth = tmp_path / "auth"
    core.mkdir()
    auth.mkdir()
    (core / "002_b.sql").write_text("SELECT 2;")
    (core / "001_a.sql").write_text("SELECT 1;")
    (core / "notes.txt").write_text("не миграция")
    (auth / "001_auth.sql").write_text("SELECT 3;")
    found = migrate.discover([str(core), str(tmp_path / "missing"), str(auth), ""])
    assert [m.name for m in found] == ["core/001_a.sql", "core/002_b.sql", "auth/001_auth.sql"]


def test_core_schema_does_not_create_schema_migrations():
    sql = (REPO_ROOT / "db" / "core" / "001_core.sql").read_text(encoding="utf-8")
    assert "schema_migrations" not in sql


@pytest.fixture
async def fresh_db():
    if not DB_URL:
        pytest.skip("нужен TEST_DATABASE_URL")
    name = f"migrate_test_{uuid.uuid4().hex[:8]}"
    try:
        admin = await asyncpg.connect(DB_URL)
    except OSError as exc:
        pytest.skip(f"Postgres недоступен: {exc}")
    await admin.execute(f'CREATE DATABASE "{name}"')
    base, _, _ = DB_URL.rpartition("/")
    try:
        yield f"{base}/{name}"
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


@pytest.mark.integration
async def test_apply_idempotent_and_concurrent(fresh_db, tmp_path):
    dirs = [str(REPO_ROOT / "db" / "core"), str(tmp_path / "auth-missing")]
    results = await asyncio.gather(*(migrate.run(fresh_db, dirs, 10) for _ in range(3)))
    applied = sorted(name for r in results for name in r)
    expected = sorted(f"core/{p.name}" for p in (REPO_ROOT / "db" / "core").glob("*.sql"))
    assert len(expected) >= 2
    assert applied == expected  # каждая применена ровно один раз
    assert await migrate.run(fresh_db, dirs, 10) == []

    conn = await asyncpg.connect(fresh_db)
    try:
        rows = await conn.fetch("SELECT name, checksum FROM public.schema_migrations")
        assert sorted(r["name"] for r in rows) == expected
        assert len(rows[0]["checksum"]) == 64
        assert await conn.fetchval("SELECT count(*) FROM source.clock") == 1
    finally:
        await conn.close()


@pytest.mark.integration
async def test_failed_migration_rolls_back(fresh_db, tmp_path):
    d = tmp_path / "core"
    d.mkdir()
    (d / "001_ok.sql").write_text("CREATE TABLE t_ok (id int);")
    (d / "002_bad.sql").write_text("CREATE TABLE t_bad (id int); SELECT * FROM no_such_table;")
    with pytest.raises(asyncpg.PostgresError):
        await migrate.run(fresh_db, [str(d)], 10)
    conn = await asyncpg.connect(fresh_db)
    try:
        assert [r["name"] for r in await conn.fetch("SELECT name FROM public.schema_migrations")] == ["core/001_ok.sql"]
        assert await conn.fetchval("SELECT to_regclass('t_bad')") is None
    finally:
        await conn.close()
