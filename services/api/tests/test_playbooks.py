"""Сценарии удержания без базы: условия, контрольная группа, лимиты, оценка эффекта, проверки API."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app import playbook_rules as rules
from app.playbooks import outcome
from tests.conftest import ADMIN, ANALYST

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)


def cand(cid: int, **kw) -> rules.Candidate:
    base = dict(
        customer_id=cid,
        email=f"c{cid}@example.test",
        risk_level="high",
        risk_score=80.0,
        expected_loss=1000,
        monthly_revenue=2000,
        balance=100,
        total_replenished=5000,
    )
    base.update(kw)
    return rules.Candidate(**base)


def cond(**kw) -> rules.Conditions:
    return rules.Conditions(**kw)


# --- условия --------------------------------------------------------------------------------


def test_level_and_reason():
    c = cand(1, main_reason="insufficient_funds")
    assert rules.matches(cond(risk_levels=["high"]), c, NOW)
    assert not rules.matches(cond(risk_levels=["medium", "low"]), c, NOW)
    assert rules.matches(cond(main_reason="insufficient_funds"), c, NOW)
    assert not rules.matches(cond(main_reason="suspended"), c, NOW)


def test_became_high_needs_previous_levels():
    rule = cond(became_high=True)
    assert rules.matches(rule, cand(1, prev_level="medium"), NOW)
    assert rules.matches(rule, cand(2, prev_level=None), NOW)  # новый клиент сразу high
    assert not rules.matches(rule, cand(3, prev_level="high"), NOW)
    assert not rules.matches(rule, cand(4, risk_level="medium", prev_level="low"), NOW)
    # первый проход: сравнивать не с чем — никто не «стал» high
    assert not rules.matches(
        rule, cand(1, prev_level="medium"), NOW, levels_ready=False
    )


def test_money_and_segment():
    c = cand(1, expected_loss=5000, monthly_revenue=3000)
    assert rules.matches(cond(min_expected_loss=5000), c, NOW)
    assert not rules.matches(cond(min_expected_loss=5001), c, NOW)
    assert rules.matches(cond(min_mrr=3000, max_mrr=3000), c, NOW)
    assert not rules.matches(cond(min_mrr=3001), c, NOW)
    assert not rules.matches(cond(max_mrr=2999), c, NOW)


def test_no_email_days():
    rule = cond(no_email_days=14)
    assert rules.matches(rule, cand(1, last_email_at=None), NOW)
    assert rules.matches(rule, cand(2, last_email_at=NOW - timedelta(days=15)), NOW)
    assert not rules.matches(rule, cand(3, last_email_at=NOW - timedelta(days=3)), NOW)


def test_autoprolong_and_expiry():
    assert rules.matches(cond(autoprolong_off=True), cand(1, autoprolong_off=True), NOW)
    assert not rules.matches(cond(autoprolong_off=True), cand(1), NOW)
    rule = cond(expires_within_days=3)
    assert rules.matches(rule, cand(1, min_days_to_expiry=2.5), NOW)
    assert rules.matches(rule, cand(2, min_days_to_expiry=0.0), NOW)
    assert not rules.matches(rule, cand(3, min_days_to_expiry=3.5), NOW)
    assert not rules.matches(rule, cand(4, min_days_to_expiry=None), NOW)


def test_all_conditions_together():
    rule = cond(
        risk_levels=["high"], main_reason="insufficient_funds", min_expected_loss=500
    )
    assert rules.matches(rule, cand(1, main_reason="insufficient_funds"), NOW)
    assert not rules.matches(
        rule, cand(2, main_reason="insufficient_funds", expected_loss=100), NOW
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},  # без условий задело бы всех
        {"main_reason": "nope"},
        {"min_mrr": 500, "max_mrr": 100},
        {"risk_levels": []},
        {"no_email_days": 0},
    ],
)
def test_bad_conditions(payload):
    with pytest.raises(ValidationError):
        rules.Conditions(**payload)


def test_actions_validation():
    base = {"name": "x", "conditions": {"risk_levels": ["high"]}}
    with pytest.raises(ValidationError):
        rules.PlaybookIn(**base, actions=[])
    with pytest.raises(ValidationError):
        rules.PlaybookIn(**base, actions=[{"type": "email"}, {"type": "email"}])
    with pytest.raises(ValidationError):
        rules.PlaybookIn(**base, actions=[{"type": "status", "status": "snoozed"}])
    with pytest.raises(ValidationError):
        rules.PlaybookIn(**base, actions=[{"type": "email"}], control_share=0.9)
    pb = rules.PlaybookIn(
        **base,
        actions=[
            {"type": "notify", "username": "admin"},
            {"type": "status", "status": "resolved"},
        ],
    )
    assert (
        pb.mode == "simulation"
        and not pb.enabled
        and pb.control_share == 0.2
        and not pb.needs_email
    )


def test_presets_are_valid():
    for preset in rules.PRESETS:
        pb = rules.PlaybookIn(
            name=preset["name"],
            conditions=preset["conditions"],
            actions=preset["actions"],
        )
        assert pb.needs_email


# --- контрольная группа ---------------------------------------------------------------------


def test_holdout_is_deterministic_and_nested():
    first = [rules.in_control(i, 0.2) for i in range(1, 2001)]
    again = [rules.in_control(i, 0.2) for i in range(1, 2001)]
    assert first == again
    share = sum(first) / len(first)
    assert 0.17 < share < 0.23
    # контроль 10% — часть контроля 20%: клиент не прыгает между группами при смене доли
    assert all(
        rules.in_control(i, 0.2) for i in range(1, 2001) if rules.in_control(i, 0.1)
    )
    assert not any(rules.in_control(i, 0.0) for i in range(1, 200))


def test_holdout_point_known_value():
    # значение зафиксировано: если поменять хеш или соль, все контрольные группы перемешаются
    import hashlib

    expected = (
        int.from_bytes(hashlib.sha256(b"churnradar-holdout:42").digest()[:8], "big")
        / 2**64
    )
    assert rules.holdout_point(42) == expected


# --- лимиты ---------------------------------------------------------------------------------


def test_select_splits_and_orders():
    cands = [cand(i, expected_loss=10_000 - i) for i in range(1, 501)]
    sel = rules.select(
        cands,
        share=0.2,
        cooldown=set(),
        treated_today=0,
        max_per_day=10_000,
        needs_email=True,
    )
    assert sel.matched == 500
    assert len(sel.treated) + len(sel.control) == 500
    assert {c.customer_id for c in sel.control} == {
        i for i in range(1, 501) if rules.in_control(i, 0.2)
    }
    assert [c.customer_id for c in sel.treated] == sorted(
        c.customer_id for c in sel.treated
    )


def test_daily_limit_stops_both_groups():
    cands = [cand(i, expected_loss=10_000 - i) for i in range(1, 201)]
    sel = rules.select(
        cands,
        share=0.2,
        cooldown=set(),
        treated_today=3,
        max_per_day=13,
        needs_email=False,
    )
    assert len(sel.treated) == 10
    last_treated = max(c.customer_id for c in sel.treated)
    # контроль набран только из того же начала списка
    assert all(c.customer_id < last_treated + 10 for c in sel.control)
    assert sel.skipped_limit > 0
    assert sel.skipped_limit + len(sel.treated) + len(sel.control) == 200


def test_limit_already_spent():
    cands = [cand(i) for i in range(1, 50)]
    sel = rules.select(
        cands,
        share=0.0,
        cooldown=set(),
        treated_today=5,
        max_per_day=5,
        needs_email=False,
    )
    assert sel.treated == [] and sel.skipped_limit == 49


def test_cooldown_and_no_email():
    cands = [cand(1), cand(2, email=""), cand(3)]
    sel = rules.select(
        cands,
        share=0.0,
        cooldown={3},
        treated_today=0,
        max_per_day=10,
        needs_email=True,
    )
    assert [c.customer_id for c in sel.treated] == [1]
    assert sel.skipped_cooldown == 1 and sel.skipped_no_email == 1
    sel = rules.select(
        cands,
        share=0.0,
        cooldown=set(),
        treated_today=0,
        max_per_day=10,
        needs_email=False,
    )
    assert len(sel.treated) == 3


# --- эффект ---------------------------------------------------------------------------------


def test_compare_few_data():
    res = rules.compare(
        [rules.Outcome(True, True, True)] * 5, [rules.Outcome(False, False, True)] * 2
    )
    assert not res["enough"]
    assert all(m["verdict"] == "few_data" for m in res["metrics"])
    assert "Мало данных" in rules.explain(res)


def test_compare_empty():
    res = rules.compare([], [])
    assert res["treated_n"] == 0 and res["metrics"][0]["diff"] is None
    assert "ещё нет" in rules.explain(res)


def test_uplift_on_synthetic_effect():
    """Синтетика: письмо поднимает удержание с 60% до 80%. На 800 клиентах эффект виден."""
    rnd = random.Random(7)

    def group(n, p):
        return [
            rules.Outcome(rnd.random() < 0.3, rnd.random() < 0.2, rnd.random() < p)
            for _ in range(n)
        ]

    res = rules.compare(group(640, 0.8), group(160, 0.6))
    retained = res["metrics"][0]
    assert res["enough"] and retained["code"] == "retained"
    assert 0.1 < retained["diff"] < 0.3
    assert retained["ci_low"] > 0 and retained["verdict"] == "better"
    assert "помогает" in rules.explain(res)

    # без эффекта (ровно по 70% в обеих группах) интервал включает ноль
    def exact(n, kept):
        return [rules.Outcome(False, False, i < kept) for i in range(n)]

    flat = rules.compare(exact(640, 448), exact(160, 112))
    assert (
        flat["metrics"][0]["diff"] == 0 and flat["metrics"][0]["verdict"] == "no_effect"
    )
    assert flat["metrics"][0]["ci_low"] < 0 < flat["metrics"][0]["ci_high"]


def test_interval_does_not_collapse_at_extremes():
    diff, low, high = rules.diff_interval(40, 40, 30, 30)
    assert diff == 0 and low < 0 < high


def test_outcome_from_row():
    row = {
        "grp": "treated",
        "risk_score_at": 80.0,
        "risk_score": 60.0,
        "replenished_at": 100,
        "total_replenished": 150,
        "present": True,
        "risk_level": "medium",
    }
    grp, out = outcome(row)
    assert grp == "treated" and out == rules.Outcome(True, True, True)
    gone = {
        **row,
        "present": False,
        "risk_score": None,
        "total_replenished": None,
        "risk_level": None,
    }
    assert outcome(gone)[1] == rules.Outcome(False, False, False)


# --- API без базы ---------------------------------------------------------------------------

BODY = {
    "name": "Тест",
    "conditions": {"risk_levels": ["high"]},
    "actions": [{"type": "email"}],
}


async def test_writes_need_admin(client):
    assert (
        await client.post("/api/v1/playbooks", json=BODY, headers=ANALYST)
    ).status_code == 403
    assert (
        await client.delete("/api/v1/playbooks/1", headers=ANALYST)
    ).status_code == 403
    assert (
        await client.post("/api/v1/playbooks/1/run", headers=ANALYST)
    ).status_code == 403


async def test_validation_errors(client):
    bad = {**BODY, "conditions": {}}
    resp = await client.post("/api/v1/playbooks", json=bad, headers=ADMIN)
    assert resp.status_code == 422
    resp = await client.post(
        "/api/v1/playbooks",
        json={**BODY, "actions": [{"type": "email", "template_code": "nope"}]},
        headers=ADMIN,
    )
    assert resp.status_code == 422


async def test_requires_login(client):
    assert (await client.get("/api/v1/playbooks")).status_code == 401
