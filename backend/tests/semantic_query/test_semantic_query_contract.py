from app.agents.model_adapter import ModelRequest, ModelResponse
from app.agents.semantic_catalog import ColumnProfile, ReadinessReport, ReadinessStatus, SemanticCatalog, TableProfile


def _catalog():
    return SemanticCatalog(
        database_id="travel_b2b",
        tables=[
            TableProfile(
                name="tb_orders",
                columns=[
                    ColumnProfile(name=name)
                    for name in (
                        "id", "gmv", "status", "booked_at", "product_type",
                        "product_name", "channel_name", "supplier_name", "region_group",
                    )
                ],
                primary_key=["id"],
            )
        ],
        readiness=ReadinessReport(status=ReadinessStatus.READY),
    )


def test_b2b_semantic_model_builds_cancel_rate_spec_from_model_ids():
    from app.agents.semantic_model import build_seed_semantic_model
    from app.agents.semantic_parser import SemanticQueryRequest
    from app.agents.semantic_spec_builder import build_analysis_spec

    model = build_seed_semantic_model("travel_b2b", _catalog())
    query = SemanticQueryRequest.from_payload({
        "operation": "new_query",
        "entity": "order",
        "metrics": ["cancel_rate"],
        "dimensions": [],
        "filters": [{"field": "product_name", "op": "=", "value": "上海迪士尼乐园一日票"}],
        "time_range": {"start": "2026-01-01", "end": "2026-08-12", "field": "booked_at"},
        "time_grain": None,
        "order_by": [],
        "limit": 20,
        "unresolved_slots": [],
    })

    spec = build_analysis_spec(query, model, _catalog(), original_question="上海迪士尼乐园一日票的取消率是多少")

    assert spec.subject == "tb_orders"
    assert spec.measures[0].aggregation == "rate"
    assert spec.measures[0].filter.value == ["cancelled", "refunded"]
    assert spec.filters[0].field == "product_name"
    assert spec.time_range.field == "booked_at"


def test_semantic_parser_accepts_only_known_model_ids():
    from app.agents.semantic_model import build_seed_semantic_model
    from app.agents.semantic_parser import SemanticParser

    model = build_seed_semantic_model("travel_b2b", _catalog())

    def complete(_request):
        return ModelResponse(ok=True, json_payload={
            "operation": "new_query", "entity": "order", "metrics": ["quote_amount"],
            "dimensions": [], "filters": [], "time_range": None,
            "time_grain": None, "order_by": [], "limit": 20, "unresolved_slots": [],
        })

    result = SemanticParser(llm_complete=complete).parse("报价金额多少", model)

    assert result.ok is False
    assert result.error == "unknown_metric:quote_amount"


def test_month_grain_and_status_set_compile_to_safe_mysql_sql():
    from app.agents.analysis_pipeline import compile_and_guard
    from app.agents.semantic_model import build_seed_semantic_model
    from app.agents.semantic_parser import SemanticQueryRequest
    from app.agents.semantic_spec_builder import build_analysis_spec

    catalog = _catalog()
    model = build_seed_semantic_model("travel_b2b", catalog)
    query = SemanticQueryRequest.from_payload({
        "operation": "new_query", "entity": "order", "metrics": ["gmv"],
        "dimensions": [], "filters": [],
        "time_range": {"start": "2026-07-01", "end": "2026-08-31", "field": "booked_at"},
        "time_grain": "month", "order_by": [], "limit": 20, "unresolved_slots": [],
    })

    result = compile_and_guard(build_analysis_spec(query, model, catalog, original_question="7到8月GMV趋势"), catalog)

    assert result.ok is True
    assert "DATE_FORMAT(`tb_orders`.`booked_at`, '%Y-%m')" in result.sql
    assert "`status` IN (:filter_1_1, :filter_1_2)" in result.sql
    assert result.params["filter_1_1"] == "paid"
    assert result.params["filter_1_2"] == "fulfilled"


def test_time_grain_orders_by_grouped_time_expression_in_mysql_strict_mode():
    from app.agents.analysis_pipeline import compile_and_guard
    from app.agents.semantic_model import build_seed_semantic_model
    from app.agents.semantic_parser import SemanticQueryRequest
    from app.agents.semantic_spec_builder import build_analysis_spec

    catalog = _catalog()
    model = build_seed_semantic_model("travel_b2b", catalog)
    query = SemanticQueryRequest.from_payload({
        "operation": "new_query", "entity": "order", "metrics": ["order_count"],
        "dimensions": [], "filters": [],
        "time_range": {"start": "2026-07-01", "end": "2026-08-31", "field": "booked_at"},
        "time_grain": "day",
        "order_by": [{"field": "booked_at", "direction": "asc"}],
        "limit": 500, "unresolved_slots": [],
    })

    result = compile_and_guard(
        build_analysis_spec(query, model, catalog, original_question="暑假订单数趋势"),
        catalog,
    )

    grouped = "DATE_FORMAT(`tb_orders`.`booked_at`, '%Y-%m-%d')"
    assert result.ok is True
    assert f"GROUP BY {grouped}" in result.sql
    assert f"ORDER BY {grouped} ASC" in result.sql
    assert "ORDER BY `booked_at`" not in result.sql


def test_semantic_query_normalises_model_operator_aliases():
    from app.agents.semantic_parser import SemanticQueryRequest

    query = SemanticQueryRequest.from_payload({
        "operation": "new_query", "entity": "order", "metrics": ["cancel_rate"],
        "dimensions": [], "filters": [{"field": "product_name", "op": "eq", "value": "上海迪士尼乐园一日票"}],
        "time_range": None, "time_grain": None, "order_by": [], "limit": 20, "unresolved_slots": [],
    })

    assert query.filters[0].op == "="


def test_semantic_query_normalises_pro_model_payload_shape():
    from app.agents.semantic_parser import SemanticQueryRequest

    query = SemanticQueryRequest.from_payload({
        "operation": "query", "entity": "order", "metrics": [{"id": "cancel_rate"}],
        "dimensions": [], "filters": [{"field": "product_name", "operator": "=", "value": "上海迪士尼乐园一日票"}],
        "time_range": None, "time_grain": None, "order_by": None, "limit": None, "unresolved_slots": [],
    })

    assert query.operation == "new_query"
    assert query.metrics == ["cancel_rate"]
    assert query.filters[0].op == "="


def test_semantic_parser_uses_pro_model_for_governed_query_translation():
    from app.agents.semantic_model import build_seed_semantic_model
    from app.agents.semantic_parser import SemanticParser

    seen = []
    def complete(request):
        seen.append(request.tier.value)
        return ModelResponse(ok=True, json_payload={
            "operation": "new_query", "entity": "order", "metrics": ["gmv"],
            "dimensions": [], "filters": [], "time_range": None,
            "time_grain": None, "order_by": [], "limit": 20, "unresolved_slots": [],
        })

    assert SemanticParser(llm_complete=complete).parse("GMV是多少", build_seed_semantic_model("travel_b2b", _catalog())).ok
    assert seen == ["pro"]


def test_semantic_parser_stops_waiting_when_model_call_exceeds_deadline():
    import time

    from app.agents.semantic_model import build_seed_semantic_model
    from app.agents.semantic_parser import SemanticParser

    def slow_complete(_request):
        time.sleep(0.05)
        return ModelResponse(ok=True, json_payload={})

    parser = SemanticParser(llm_complete=slow_complete, timeout_s=0.001)
    started = time.monotonic()
    response = parser._complete_with_deadline(ModelRequest(system="", user=""), timeout_s=0.001)

    assert response.ok is False
    assert response.error == "semantic_parser_timeout"
    assert time.monotonic() - started < 0.04


def test_semantic_parser_fills_model_missing_relative_time_slot(monkeypatch):
    from app.agents.semantic_model import build_seed_semantic_model
    from app.agents.semantic_parser import SemanticParser

    result = SemanticParser(llm_complete=lambda _: ModelResponse(ok=True, json_payload={
        "operation": "clarify", "entity": "order", "metrics": ["order_count"],
        "dimensions": [], "filters": [{"field": "channel_name", "op": "=", "value": "杭州阳光假期门店"}],
        "time_range": None, "time_grain": None, "order_by": [], "limit": None,
        "unresolved_slots": ["time_range"],
    })).parse("杭州阳光假期门店近14天订单数", build_seed_semantic_model("travel_b2b", _catalog()))

    assert result.ok is True
    assert result.query.operation == "new_query"
    assert result.query.time_range is not None
    assert result.query.time_range.field == "booked_at"
    assert "time_range" not in result.query.unresolved_slots


def test_semantic_parser_normalises_model_relative_time_values():
    from app.agents.semantic_model import build_seed_semantic_model
    from app.agents.semantic_parser import SemanticParser

    result = SemanticParser(llm_complete=lambda _: ModelResponse(ok=True, json_payload={
        "operation": "new_query", "entity": "order", "metrics": ["order_count"],
        "dimensions": [], "filters": [],
        "time_range": {"start": "today-14d", "end": "today", "field": "booked_at"},
        "time_grain": None, "order_by": [], "limit": None, "unresolved_slots": [],
    })).parse("近14天订单数", build_seed_semantic_model("travel_b2b", _catalog()))

    assert result.ok is True
    assert result.query.time_range.start != "today-14d"
    assert result.query.time_range.end != "today"


def test_semantic_parser_keeps_original_time_when_resolved_question_loses_it():
    from app.agents.semantic_model import build_seed_semantic_model
    from app.agents.semantic_parser import SemanticParser

    result = SemanticParser(llm_complete=lambda _: ModelResponse(ok=True, json_payload={
        "operation": "clarify", "entity": "order", "metrics": ["order_count"],
        "dimensions": [], "filters": [{"field": "channel_name", "op": "=", "value": "杭州阳光假期门店"}],
        "time_range": None, "time_grain": None, "order_by": [], "limit": None,
        "unresolved_slots": ["time_range"],
    })).parse(
        "查询杭州阳光假期门店订单数",
        build_seed_semantic_model("travel_b2b", _catalog()),
        original_question="杭州阳光假期门店近14天订单数",
    )

    assert result.ok is True
    assert result.query.time_range is not None


def test_semantic_parser_retries_pro_once_before_flash_fallback():
    from app.agents.semantic_model import build_seed_semantic_model
    from app.agents.semantic_parser import SemanticParser

    seen = []
    def complete(request):
        seen.append(request.tier.value)
        if len(seen) == 1:
            return ModelResponse(ok=True, json_payload=None)
        return ModelResponse(ok=True, json_payload={
            "operation": "new_query", "entity": "order", "metrics": ["order_count"],
            "dimensions": [], "filters": [], "time_range": None,
            "time_grain": None, "order_by": [], "limit": 20, "unresolved_slots": [],
        })

    assert SemanticParser(llm_complete=complete).parse("订单数", build_seed_semantic_model("travel_b2b", _catalog())).ok
    assert seen == ["pro", "pro"]


def test_semantic_parser_uses_flash_directly_after_pro_timeout():
    from app.agents.semantic_model import build_seed_semantic_model
    from app.agents.semantic_parser import SemanticParser

    seen = []

    def complete(request):
        seen.append(request.tier.value)
        if request.tier.value == "pro":
            return ModelResponse(ok=False, error="semantic_parser_timeout")
        return ModelResponse(ok=True, json_payload={
            "operation": "new_query", "entity": "order", "metrics": ["order_count"],
            "dimensions": [], "filters": [], "time_range": None,
            "time_grain": None, "order_by": [], "limit": 20, "unresolved_slots": [],
        })

    assert SemanticParser(llm_complete=complete).parse("订单数", build_seed_semantic_model("travel_b2b", _catalog())).ok
    assert seen == ["pro", "flash"]


def test_semantic_parser_normalises_relative_colon_time_values():
    from app.agents.semantic_model import build_seed_semantic_model
    from app.agents.semantic_parser import SemanticParser

    result = SemanticParser(llm_complete=lambda _: ModelResponse(ok=True, json_payload={
        "operation": "new_query", "entity": "order", "metrics": ["order_count"],
        "dimensions": [], "filters": [],
        "time_range": {"start": "relative:14_days_ago", "end": "relative:now", "field": "booked_at"},
        "time_grain": None, "order_by": [], "limit": None, "unresolved_slots": [],
    })).parse("近14天订单数", build_seed_semantic_model("travel_b2b", _catalog()))

    assert result.ok is True
    assert result.query.time_range.start != "relative:14_days_ago"
    assert result.query.time_range.end != "relative:now"


def test_semantic_parser_normalises_now_relative_time_values():
    from app.agents.semantic_model import build_seed_semantic_model
    from app.agents.semantic_parser import SemanticParser

    result = SemanticParser(llm_complete=lambda _: ModelResponse(ok=True, json_payload={
        "operation": "new_query", "entity": "order", "metrics": ["order_count"],
        "dimensions": [], "filters": [],
        "time_range": {"start": "now-14d", "end": "now", "field": "booked_at"},
        "time_grain": None, "order_by": [], "limit": None, "unresolved_slots": [],
    })).parse("近14天订单数", build_seed_semantic_model("travel_b2b", _catalog()))

    assert result.ok is True
    assert result.query.time_range.start != "now-14d"
    assert result.query.time_range.end != "now"


def test_semantic_parser_does_not_turn_supervisor_params_into_an_executable_query():
    from app.agents.semantic_model import build_seed_semantic_model
    from app.agents.semantic_parser import SemanticParser

    result = SemanticParser(llm_complete=lambda _: ModelResponse(ok=True, text="", json_payload=None)).parse(
        "2026年7到8月GMV趋势",
        build_seed_semantic_model("travel_b2b", _catalog()),
        supervisor_params={
            "metric": "GMV", "date_range": ["2026-07-01", "2026-08-31"],
            "trend": True, "granularity": "month",
        },
    )

    assert result.ok is False
    assert result.error == "semantic_parser_unavailable"
