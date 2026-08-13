from app.agents.analysis_spec import AnalysisSpec, Measure, TimeRange
from app.agents.evidence import EvidenceBundle
from app.agents.query_outcome import QueryOutcome


def evidence(spec, rows):
    return EvidenceBundle(
        analysis_spec_id=spec.spec_id,
        rows_count=len(rows),
        result_preview=rows,
        time_range=spec.time_range.__dict__ if spec.time_range else None,
        measures=[m.__dict__ for m in spec.measures],
        dimensions=list(spec.dimensions),
    )


def answer(spec, rows):
    from app.agents.controlled_loop import assemble_query_answer

    outcome = QueryOutcome.success_with_data(
        rows=rows,
        columns=list(rows[0]) if rows else [],
    )
    return assemble_query_answer(spec, outcome, evidence(spec, rows))


def test_trend_answer_reports_time_points_not_first_bucket_value():
    spec = AnalysisSpec(
        measures=[Measure("id", "count", "订单数", "tb_orders")],
        dimensions=["time:day:booked_at"],
        time_range=TimeRange("booked_at", "2026-07-01", "2026-08-31"),
    )
    rows = [
        {"date": "2026-07-01", "value": 183},
        {"date": "2026-07-02", "value": 201},
        {"date": "2026-07-03", "value": 176},
    ]

    message = answer(spec, rows)

    assert "3 个时间点" in message
    assert "查询结果为 183" not in message


def test_breakdown_answer_reports_group_count_and_top_group():
    spec = AnalysisSpec(
        measures=[Measure("id", "count", "订单数", "tb_orders")],
        dimensions=["channel_name"],
    )
    rows = [
        {"channel_name": "华东渠道", "value": 320},
        {"channel_name": "华南渠道", "value": 210},
    ]

    message = answer(spec, rows)

    assert "2 个分组" in message
    assert "华东渠道" in message
    assert "320" in message


def test_scalar_answer_keeps_single_metric_value():
    spec = AnalysisSpec(
        measures=[Measure("id", "count", "订单数", "tb_orders")],
        dimensions=[],
    )

    message = answer(spec, [{"value": 35000}])

    assert "订单数为 35000" in message


def test_internal_metric_id_is_humanized_in_answer():
    spec = AnalysisSpec(
        measures=[Measure("id", "count", "order_count", "tb_orders")],
        dimensions=["time:day:booked_at"],
    )

    message = answer(spec, [{"date": "2026-07-01", "value": 183}])

    assert "订单数趋势" in message
    assert "order_count" not in message


def test_detail_answer_reports_returned_row_count():
    spec = AnalysisSpec(
        task_type="detail",
        measures=[Measure("id", "sample", "订单明细", "tb_orders")],
        dimensions=[],
    )
    rows = [{"id": 1}, {"id": 2}, {"id": 3}]

    message = answer(spec, rows)

    assert "返回 3 条明细" in message
