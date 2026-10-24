"""Передача части полного пересчёта в процесс и обратно: кортежи на входе, быстрый pickle результатов."""

from __future__ import annotations

import copyreg
import dataclasses
import pickle
from datetime import UTC, datetime
from typing import Any

import riskcore
from riskcore import CustomerResult

from app import engine
from app import repository as repo

REFERENCE = datetime(2026, 10, 19, 15, tzinfo=UTC)


def _rows(sample_data: dict[str, Any]) -> repo.SourceRows:
    accounts, services, transactions = riskcore.parse_snapshot(sample_data)
    return repo.SourceRows(
        [dataclasses.astuple(a) for a in accounts],
        [dataclasses.astuple(s) for s in services],
        [dataclasses.astuple(t) for t in transactions],
    )


def test_rows_rebuild_same_objects(sample_data) -> None:  # type: ignore[no-untyped-def]
    accounts, services, transactions = riskcore.parse_snapshot(sample_data)
    data = _rows(sample_data).to_data()
    assert (data.accounts, data.services, data.transactions) == (accounts, services, transactions)
    assert _rows(sample_data).service_objects() == services


def test_results_survive_pickle(sample_data) -> None:  # type: ignore[no-untyped-def]
    assert CustomerResult in copyreg.dispatch_table
    settings = riskcore.default_settings()
    rows = _rows(sample_data)
    slim, records = engine._score_chunk(rows, settings, REFERENCE, 1, REFERENCE)
    back_slim, back_records = pickle.loads(pickle.dumps((slim, records), protocol=pickle.HIGHEST_PROTOCOL))

    assert back_records == records
    assert [r.to_row() for r in back_slim] == [r.to_row() for r in slim]
    for was, now in zip(slim, back_slim, strict=True):
        assert [(o.service, o.probability, o.level, o.revenue) for o in now.services] == [
            (o.service, o.probability, o.level, o.revenue) for o in was.services
        ]
        assert [(h.feature.code, h.signal, h.contribution) for o in now.services for h in o.hits] == [
            (h.feature.code, h.signal, h.contribution) for o in was.services for h in o.hits
        ]
        assert [(t.code, t.points, t.loss, t.top_hit.signal) for t in now.feature_totals] == [
            (t.code, t.points, t.loss, t.top_hit.signal) for t in was.feature_totals
        ]
    # сводка по результатам после pickle та же, что по исходным
    services = rows.service_objects()
    assert riskcore.build_summary(back_slim, services, [], settings, REFERENCE) == riskcore.build_summary(
        slim, services, [], settings, REFERENCE
    )
    # поля для маркетинга (платежи, первый контакт) тоже переживают pickle
    assert [(r.paid_total, r.payments, r.first_seen) for r in back_slim] == [
        (r.paid_total, r.payments, r.first_seen) for r in slim
    ]
    with_marketing = engine.build_summary(back_slim, services, [], settings, REFERENCE)
    assert with_marketing["marketing"] == riskcore.build_marketing(slim, [], settings.horizon_days, REFERENCE)
    assert with_marketing["marketing"]["mrr"] == with_marketing["monthly_revenue_total"]
