from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_DIR.parents[1]
sys.path.insert(0, str(SERVICE_DIR))
try:
    import svckit  # noqa: F401
except ImportError:  # локальный запуск без установленного пакета
    sys.path.insert(0, str(REPO_ROOT / "libs" / "svckit"))

SCHEMA_FILES = sorted((REPO_ROOT / "db" / "core").glob("*.sql"))

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "postgresql://churn:churn@localhost:55433/churn")
TEST_NATS_URL = os.getenv("TEST_NATS_URL", "nats://localhost:54223")

SCORE_SQL = """
INSERT INTO scoring.customer_scores (
    customer_id, email, balance, bonus_balance, bonus_state, withdraw_balance, total_replenished,
    risk_score, risk_level, loyalty, reasons, services_total, services_at_risk, monthly_revenue,
    expected_loss, exposure, detail, model_version, scored_at)
VALUES ($1, $2, 0, 0, 'locked', 0, 0, $3, $4, 0.5, '[]', 1, 0, 1000, $5, $5, '{}', 1, now())
ON CONFLICT (customer_id) DO UPDATE SET
    risk_level = EXCLUDED.risk_level, risk_score = EXCLUDED.risk_score, expected_loss = EXCLUDED.expected_loss
"""


async def put_score(conn: Any, customer_id: int, level: str, loss_cents: int) -> None:
    score = {"high": 80.0, "medium": 50.0, "low": 10.0}.get(level)
    await conn.execute(SCORE_SQL, customer_id, f"user{customer_id}@example.com", score, level, loss_cents)


async def _reset_schema(dsn: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "DROP SCHEMA IF EXISTS alerts CASCADE; DROP SCHEMA IF EXISTS source CASCADE; "
            "DROP SCHEMA IF EXISTS scoring CASCADE;"
        )
        for path in SCHEMA_FILES:
            await conn.execute(path.read_text(encoding="utf-8"))
    finally:
        await conn.close()


@pytest.fixture
async def db_pool() -> AsyncIterator[Any]:
    asyncpg = pytest.importorskip("asyncpg")
    try:
        await asyncio.wait_for(_reset_schema(TEST_DATABASE_URL), timeout=10)
    except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Postgres недоступен: {exc}")
    from app import repository as repo

    pool = await repo.create_pool(TEST_DATABASE_URL, max_size=6)
    try:
        yield pool
    finally:
        await pool.close()


class WebhookServer:
    """Локальный приёмник webhook: копит тела запросов, код ответа можно менять на лету."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = 200
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                outer.requests.append(
                    {
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": json.loads(body or b"{}"),
                        "status": outer.status,
                        "at": datetime.now(UTC),
                    }
                )
                self.send_response(outer.status)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def webhook() -> Iterator[WebhookServer]:
    server = WebhookServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()
