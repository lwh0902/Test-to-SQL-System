from app.agents.analysis_spec import AnalysisSpec, FilterExpr, Measure, TimeRange
from app.agents.semantic_catalog import ColumnProfile, ReadinessReport, ReadinessStatus, SemanticCatalog, TableProfile
from app.agents.sql_compiler import compile_spec


def _catalog():
    return SemanticCatalog(
        database_id="x",
        readiness=ReadinessReport(status=ReadinessStatus.READY),
        tables=[TableProfile(name="orders", columns=[
            ColumnProfile(name="created_at"),
            ColumnProfile(name="channel_name"),
        ])],
    )


def test_compiler_binds_filter_and_time_values_instead_of_interpolating_them():
    value = "x' OR 1=1 --"
    spec = AnalysisSpec(
        measures=[Measure(source_field="", aggregation="count", table="orders")],
        required_tables=["orders"],
        filters=[FilterExpr(field="channel_name", value=value, table="orders")],
        time_range=TimeRange(field="created_at", start="2026-01-01", end="2026-01-31"),
    )

    compiled = compile_spec(spec, _catalog())

    assert compiled.ok is True
    assert value not in compiled.sql
    assert ":filter_" in compiled.sql
    assert ":time_start" in compiled.sql
    assert compiled.params["filter_1"] == value
    assert compiled.params["time_start"] == "2026-01-01"
    assert compiled.params["time_end"] == "2026-01-31 23:59:59"
