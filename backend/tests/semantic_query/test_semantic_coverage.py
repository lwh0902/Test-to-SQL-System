from app.agents.analysis_spec import AnalysisSpec, FilterExpr, Measure, TimeRange
from app.agents.semantic_catalog import ColumnProfile, ReadinessReport, ReadinessStatus, SemanticCatalog, TableProfile
from app.services.entity_value_service import EntityValue, resolve_filter_values


def _catalog():
    return SemanticCatalog(
        database_id="travel_b2b",
        tables=[TableProfile(name="tb_orders", columns=[ColumnProfile(name=x) for x in (
            "id", "gmv", "status", "booked_at", "product_type", "product_name", "channel_name",
        )], primary_key=["id"])],
        readiness=ReadinessReport(status=ReadinessStatus.READY),
    )


def _spec(*, filters=None, time_range=None, dimensions=None):
    return AnalysisSpec(
        subject="tb_orders", required_tables=["tb_orders"],
        measures=[Measure("id", "count", "order_count", "tb_orders")],
        filters=filters or [], time_range=time_range, dimensions=dimensions or [],
    )


def test_explicit_dictionary_value_missing_from_query_plan_is_rejected():
    from app.agents.semantic_coverage import validate_semantic_coverage
    from app.agents.semantic_model import build_seed_semantic_model

    result = validate_semantic_coverage(
        "杭州阳光假期门店近14天订单数",
        _spec(time_range=TimeRange("booked_at", "2026-07-30", "2026-08-12")),
        build_seed_semantic_model("travel_b2b", _catalog()),
        [EntityValue("channel_name", "杭州阳光假期门店")],
    )

    assert result.ok is False
    assert "missing_explicit_filter:channel_name" in result.errors


def test_explicit_dictionary_values_are_added_to_a_semantic_plan_without_model_guessing():
    resolved = resolve_filter_values(
        [],
        "杭州阳光假期门店近14天订单数",
        [EntityValue("channel_name", "杭州阳光假期门店")],
    )

    assert [(x.field, x.value) for x in resolved.filters] == [
        ("channel_name", "杭州阳光假期门店"),
    ]


def test_model_defined_dimension_mentioned_by_user_must_survive_to_spec():
    from app.agents.semantic_coverage import validate_semantic_coverage
    from app.agents.semantic_model import build_seed_semantic_model

    result = validate_semantic_coverage(
        "今年7到8月GMV按品类拆开",
        _spec(time_range=TimeRange("booked_at", "2026-07-01", "2026-08-31")),
        build_seed_semantic_model("travel_b2b", _catalog()),
        [],
    )

    assert result.ok is False
    assert "missing_explicit_dimension:product_type" in result.errors


def test_explicit_relative_time_missing_from_query_plan_is_rejected():
    from app.agents.semantic_coverage import validate_semantic_coverage
    from app.agents.semantic_model import build_seed_semantic_model

    result = validate_semantic_coverage(
        "杭州阳光假期门店近14天订单数",
        _spec(filters=[FilterExpr("channel_name", "=", "杭州阳光假期门店", "tb_orders")]),
        build_seed_semantic_model("travel_b2b", _catalog()),
        [EntityValue("channel_name", "杭州阳光假期门店")],
    )

    assert result.ok is False
    assert "missing_explicit_time_range" in result.errors
