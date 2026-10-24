"""Выход библиотеки сверяется со схемами contracts/openapi.yaml."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from riskcore import (
    StatusChange,
    build_summary,
    default_settings,
    parse_snapshot,
    score_customers,
    settings_to_api,
)

from .factories import AS_OF

jsonschema = pytest.importorskip("jsonschema")


def validate(openapi: dict[str, Any], schema_name: str, instance: Any) -> None:
    schema = {"$ref": f"#/components/schemas/{schema_name}", "components": openapi["components"]}
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors[:10])


def strict(openapi: dict[str, Any], schema_name: str) -> set[str]:
    """Все поля, объявленные в схеме (с учётом allOf)."""
    schema = openapi["components"]["schemas"][schema_name]
    parts = schema.get("allOf", [schema])
    fields: set[str] = set()
    for part in parts:
        if "$ref" in part:
            fields |= strict(openapi, part["$ref"].rsplit("/", 1)[-1])
        else:
            fields |= set(part.get("properties", {}))
    return fields


@pytest.fixture(scope="module")
def scored(sample_data):
    accounts, services, transactions = parse_snapshot(sample_data)
    settings = default_settings()
    results = score_customers(accounts, services, transactions, settings, AS_OF)
    history = [
        StatusChange(s.id, s.owner_id, "prolong_wait", "deleted", AS_OF - timedelta(days=2), estimated=True)
        for s in services
        if s.status == "deleted"
    ]
    return results, services, history, settings


def test_customer_detail_matches_schema(openapi, scored):
    results, *_ = scored
    for result in results:
        detail = result.to_api(scored_at=datetime.now(UTC))
        validate(openapi, "CustomerDetail", detail)
        assert set(detail) <= strict(openapi, "CustomerDetail")


def test_nested_objects_match_schemas(openapi, scored):
    results, *_ = scored
    for result in results:
        detail = result.to_api()
        validate(openapi, "Money", detail["money"])
        assert set(detail["money"]) <= strict(openapi, "Money")
        for service in detail["services"]:
            validate(openapi, "ServiceRisk", service)
            assert set(service) <= strict(openapi, "ServiceRisk")
        for feature in detail["features"]:
            validate(openapi, "FeatureContribution", feature)
            assert set(feature) <= strict(openapi, "FeatureContribution")
        for transaction in detail["transactions"]:
            validate(openapi, "Transaction", transaction)
        for reason in detail["reasons"]:
            validate(openapi, "Reason", reason)
            assert set(reason) <= strict(openapi, "Reason")
        for recommendation in detail["recommendations"]:
            validate(openapi, "Recommendation", recommendation)
            assert set(recommendation) <= strict(openapi, "Recommendation")
        if detail["next_action"] is not None:
            validate(openapi, "Recommendation", detail["next_action"])


def test_customer_summary_matches_schema(openapi, scored):
    results, *_ = scored
    for result in results:
        summary = result.to_summary_api()
        validate(openapi, "CustomerSummary", summary)
        assert set(summary) <= strict(openapi, "CustomerSummary")


def test_summary_matches_schema(openapi, scored):
    results, services, history, settings = scored
    summary = build_summary(results, services, history, settings, AS_OF)
    validate(openapi, "Summary", summary)
    assert set(summary) <= strict(openapi, "Summary")
    for point in summary["timeline"]:
        validate(openapi, "TimelinePoint", point)


def test_model_settings_match_schema(openapi):
    api = settings_to_api(default_settings(), AS_OF, "admin")
    validate(openapi, "ModelSettings", api)
    for weight in api["weights"]:
        assert set(weight) <= strict(openapi, "FeatureWeight")
