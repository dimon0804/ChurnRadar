"""Unit-тесты «С кем связаться сегодня»: отметки, правила возврата в очередь, CSV."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from svckit.errors import ApiError

from app import actions
from tests.conftest import ANALYST, NOW, FakeConn, FakePool


def _row(**over: Any) -> dict[str, Any]:
    row = {
        "customer_id": 9,
        "email": "user-0009@example.test",
        "balance": 91506,
        "total_replenished": 1_000_000,
        "risk_score": 72.44,
        "risk_level": "high",
        "expected_loss": 138497,
        "main_reason": "На балансе мало денег",
        "next_action": {"code": "insufficient_funds", "title": "Напомнить пополнить"},
        "a_status": None,
        "a_until": None,
        "a_note": None,
        "a_at": None,
        "a_by": None,
        "a_loss": None,
        "a_level": None,
    }
    row.update(over)
    return row


def _marked(status: str, until=None, **over: Any) -> dict[str, Any]:
    return _row(
        a_status=status,
        a_until=until,
        a_at=NOW,
        a_by="analyst",
        a_loss=138497,
        a_level="high",
        **over,
    )


# --- тело запроса ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"status": "resolved"},
        {"status": "resolved", "note": "Ответил, пополнит в пятницу"},
        {"status": "emailed"},
        {"status": "emailed", "snooze_days": 3},
        {"status": "snoozed", "snooze_days": 3},
        {"status": "snoozed", "snoozed_until": "2026-11-01T09:00:00Z"},
    ],
)
def test_action_update_valid(body):
    actions.ActionUpdate.model_validate(body)


@pytest.mark.parametrize(
    "body",
    [
        {"status": "snoozed"},
        {
            "status": "snoozed",
            "snooze_days": 3,
            "snoozed_until": "2026-11-01T09:00:00Z",
        },
        {
            "status": "emailed",
            "snooze_days": 3,
            "snoozed_until": "2026-11-01T09:00:00Z",
        },
        {"status": "resolved", "snooze_days": 3},
        {"status": "snoozed", "snooze_days": 0},
        {"status": "snoozed", "snooze_days": 366},
        {
            "status": "snoozed",
            "snoozed_until": "2026-11-01T09:00:00",
        },  # без часового пояса
        {"status": "done"},  # старый статус больше не принимаем
        {"status": "resolved", "note": "x" * 501},
        {"status": "resolved", "extra": 1},
    ],
)
def test_action_update_invalid(body):
    with pytest.raises(ValidationError):
        actions.ActionUpdate.model_validate(body)


def test_snooze_deadline():
    days = actions.ActionUpdate(status="snoozed", snooze_days=7)
    assert actions.snooze_deadline(days, NOW) == NOW + timedelta(days=7)
    assert actions.snooze_deadline(actions.ActionUpdate(status="resolved"), NOW) is None
    # письмо без срока — ждём ответа по настройке
    assert actions.snooze_deadline(
        actions.ActionUpdate(status="emailed"), NOW, 5
    ) == NOW + timedelta(days=5)
    exact = actions.ActionUpdate(
        status="snoozed", snoozed_until=NOW + timedelta(hours=5)
    )
    assert actions.snooze_deadline(exact, NOW) == NOW + timedelta(hours=5)
    for until in (NOW - timedelta(minutes=1), NOW + timedelta(days=366)):
        with pytest.raises(ApiError) as err:
            actions.snooze_deadline(
                actions.ActionUpdate(status="snoozed", snoozed_until=until), NOW
            )
        assert err.value.status == 422


# --- жизненный цикл ------------------------------------------------------------------------


def test_return_reason():
    later = NOW + timedelta(days=3)
    rr = actions.return_reason
    assert rr("resolved", None, 1000, "medium", 1000, "medium", NOW) is None
    assert (
        rr("resolved", None, 1000, "medium", 900, "medium", NOW) is None
    )  # потеря упала — не возвращаем
    assert rr("resolved", None, 1000, "medium", 1001, "medium", NOW) == "risk_grew"
    assert (
        rr("resolved", None, 1000, "medium", 500, "high", NOW) == "risk_grew"
    )  # уровень вырос
    assert (
        rr("emailed", later, 1000, "medium", 1000, "medium", NOW) is None
    )  # ждём ответа
    assert rr("emailed", later, 1000, "medium", 2000, "medium", NOW) == "risk_grew"
    assert (
        rr("emailed", NOW - timedelta(seconds=1), 1000, "medium", 900, "medium", NOW)
        == "no_reply"
    )
    assert (
        rr("snoozed", later, 1000, "medium", 5000, "high", NOW) is None
    )  # отложка — до даты, что бы ни было
    assert rr("snoozed", NOW, 1000, "medium", 1000, "medium", NOW) == "snooze_over"


def test_action_out_states():
    assert actions.action_out(_row(), NOW) is None

    resolved = actions.action_out(_marked("resolved"), NOW)
    assert (
        resolved["status"] == "resolved"
        and resolved["active"] is True
        and resolved["return_reason"] is None
    )
    assert (
        resolved["updated_at"] == "2026-10-19T15:00:00Z"
        and resolved["updated_by"] == "analyst"
    )

    grown = actions.action_out(_marked("resolved", expected_loss=150000), NOW)
    assert grown["active"] is False and grown["return_reason"] == "risk_grew"

    emailed = _marked("emailed", NOW + timedelta(days=7))
    assert actions.action_out(emailed, NOW)["active"] is True
    assert (
        actions.action_out(emailed, NOW + timedelta(days=8))["return_reason"]
        == "no_reply"
    )

    snoozed = _marked("snoozed", NOW + timedelta(days=2))
    out = actions.action_out(snoozed, NOW)
    assert out["active"] is True and out["until"] == "2026-10-21T15:00:00Z"
    assert (
        actions.action_out(snoozed, NOW + timedelta(days=3))["return_reason"]
        == "snooze_over"
    )


def test_status_text():
    assert actions.status_text(None) == ""
    assert actions.status_text(actions.action_out(_marked("resolved"), NOW)) == "Решено"
    emailed = _marked("emailed", NOW + timedelta(days=7))
    assert (
        actions.status_text(actions.action_out(emailed, NOW))
        == "Письмо отправлено 19.10.2026"
    )
    assert "ответа нет" in actions.status_text(
        actions.action_out(emailed, NOW + timedelta(days=8))
    )
    grown = actions.action_out(_marked("resolved", expected_loss=10**6), NOW)
    assert "снова в списке" in actions.status_text(grown)
    snoozed = _marked("snoozed", NOW + timedelta(days=2))
    assert (
        actions.status_text(actions.action_out(snoozed, NOW))
        == "Отложено до 21.10.2026"
    )
    assert (
        actions.status_text(actions.action_out(snoozed, NOW + timedelta(days=5))) == ""
    )


# --- CSV ---------------------------------------------------------------------------------------


def test_euros_and_safe_text():
    assert actions.euros(138497) == "1384,97"
    assert actions.euros(600) == "6,00"
    assert actions.euros(5) == "0,05"
    assert actions.euros(None) == ""
    assert actions.safe_text("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert actions.safe_text("-1+2") == "'-1+2"
    assert actions.safe_text("a@b.c") == "a@b.c"
    assert actions.safe_text("две\nстроки") == "две строки"
    assert actions.safe_text(None) == ""


def test_csv_row_and_render():
    row = actions.csv_row(_marked("resolved"), NOW)
    assert row == [
        "9",
        "user-0009@example.test",
        "Высокий",
        "72,4",
        "1384,97",
        "915,06",
        "10000,00",
        "На балансе мало денег",
        "Напомнить пополнить",
        "Решено",
    ]
    unknown = actions.csv_row(
        _row(risk_score=None, risk_level="unknown", next_action=None, main_reason=None),
        NOW,
    )
    assert unknown[2:4] == ["Нет оценки", ""] and unknown[7:] == ["", "", ""]

    data = actions.render_csv([row]).decode("utf-8")
    assert data.startswith("﻿ID;Email;Уровень риска;Риск, баллы;Ожидаемая потеря, €;")
    lines = data.lstrip("﻿").split("\r\n")
    assert lines[1].endswith(";Напомнить пополнить;Решено")
    assert lines[2] == ""
    # точка с запятой внутри текста — в кавычках, столбцы не съезжают
    quoted = actions.render_csv([["1", "a;b"]]).decode("utf-8").split("\r\n")[1]
    assert quoted == '1;"a;b"'


def test_order_is_qualified_for_join():
    assert actions._qualified("risk_score DESC NULLS LAST, customer_id ASC") == (
        "cs.risk_score DESC NULLS LAST, cs.customer_id ASC"
    )
    assert actions._qualified("customer_id DESC") == "cs.customer_id DESC"


def test_export_filename():
    assert actions.export_filename(NOW) == "churnradar-clients-2026-10-19.csv"


# --- эндпоинты ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/v1/contact-today", None),
        ("GET", "/api/v1/customers/export.csv", None),
        ("GET", "/api/v1/customers/1/action", None),
        ("PUT", "/api/v1/customers/1/action", {"status": "resolved"}),
        ("DELETE", "/api/v1/customers/1/action", None),
    ],
)
async def test_requires_user(client, method, path, body):
    resp = await client.request(method, path, json=body)
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "unauthorized"


async def test_export_csv_endpoint(client, app):
    app.state.pool = FakePool(
        {
            "SELECT now()": NOW,
            "LEFT JOIN scoring.customer_actions": [
                _marked("resolved"),
                _row(customer_id=1),
            ],
        }
    )
    resp = await client.get(
        "/api/v1/customers/export.csv?level=high&sort=expected_loss&order=desc",
        headers=ANALYST,
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "text/csv; charset=utf-8"
    assert resp.headers["content-disposition"].startswith(
        'attachment; filename="churnradar-clients-'
    )
    assert resp.content.startswith(b"\xef\xbb\xbf")
    lines = resp.content.decode("utf-8-sig").split("\r\n")
    assert lines[0].split(";")[0] == "ID" and len(lines[0].split(";")) == 10
    assert [line.split(";")[0] for line in lines[1:3]] == ["9", "1"]
    sql = app.state.pool.conn.queries[-1]
    assert "ORDER BY cs.expected_loss DESC, cs.customer_id ASC" in sql
    assert "risk_level = ANY($1::text[])" in sql


async def test_export_csv_worklist_and_validation(client, app):
    app.state.pool = FakePool(
        {"SELECT now()": NOW, "LEFT JOIN scoring.customer_actions": []}
    )
    resp = await client.get(
        "/api/v1/customers/export.csv?worklist=true", headers=ANALYST
    )
    assert resp.status_code == 200
    assert "NOT COALESCE((" in app.state.pool.conn.queries[-1]
    assert resp.content.decode("utf-8-sig").count("\r\n") == 1  # только заголовок
    assert (
        await client.get("/api/v1/customers/export.csv?level=extreme", headers=ANALYST)
    ).status_code == 422
    assert (
        await client.get("/api/v1/customers/export.csv?sort=password", headers=ANALYST)
    ).status_code == 422


async def test_put_action_validation_before_db(client):
    bad = await client.put(
        "/api/v1/customers/1/action", json={"status": "snoozed"}, headers=ANALYST
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error"
    bad_id = await client.put(
        "/api/v1/customers/abc/action", json={"status": "resolved"}, headers=ANALYST
    )
    assert bad_id.status_code == 422


class TxConn(FakeConn):
    """FakeConn с транзакцией и execute: хватает для PUT/DELETE без настоящей БД."""

    def __init__(self, answers: dict[str, Any]) -> None:
        super().__init__(answers)
        self.executed: list[tuple[str, tuple]] = []

    def transaction(self):
        class _Tx:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *exc):
                return False

        return _Tx()

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "OK"


async def test_analyst_can_mark_resolved(client, app):
    pool = FakePool()
    pool.conn = TxConn(
        {
            "SELECT now()": NOW,
            "SELECT expected_loss, risk_level FROM scoring.customer_scores": {
                "expected_loss": 600,
                "risk_level": "high",
            },
            "LEFT JOIN scoring.customer_actions a": {
                "expected_loss": 600,
                "risk_level": "high",
                "a_status": "resolved",
                "a_until": None,
                "a_note": None,
                "a_at": NOW,
                "a_by": "analyst",
                "a_loss": 600,
                "a_level": "high",
            },
            "FROM scoring.customer_actions_log": [],
        }
    )
    app.state.pool = pool
    resp = await client.put(
        "/api/v1/customers/10/action", json={"status": "resolved"}, headers=ANALYST
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (
        body["customer_id"] == 10
        and body["action"]["status"] == "resolved"
        and body["action"]["active"] is True
    )
    upsert, log_insert = pool.conn.executed
    assert "ON CONFLICT (customer_id) DO UPDATE" in upsert[0]
    assert upsert[1] == (10, "resolved", None, None, 600, "high", "analyst")
    assert "customer_actions_log" in log_insert[0] and log_insert[1][-1] == "analyst"


async def test_mark_unknown_customer_is_404(client, app):
    pool = FakePool()
    pool.conn = TxConn(
        {
            "SELECT now()": NOW,
            "SELECT expected_loss, risk_level FROM scoring.customer_scores": None,
        }
    )
    app.state.pool = pool
    resp = await client.put(
        "/api/v1/customers/999/action", json={"status": "resolved"}, headers=ANALYST
    )
    assert resp.status_code == 404
    assert pool.conn.executed == []
