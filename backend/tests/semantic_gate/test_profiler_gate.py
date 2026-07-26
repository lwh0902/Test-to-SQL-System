from __future__ import annotations

import json

from app.agents.live_profiler import profile_live_mysql

from .mysql_runtime import provision_suite
from .schema_factory import build_schema_suites


def _column(catalog, name: str):
    for table in catalog.tables:
        for column in table.columns:
            if column.name == name:
                return column
    raise AssertionError(f"column not found: {name}")


def test_profiler_emits_bounded_evidence_status_vocabulary_and_sample_strategy():
    suite = next(s for s in build_schema_suites() if s.family == "support")
    with provision_suite(suite) as (cfg, database):
        catalog = profile_live_mysql(
            host=cfg.host,
            port=cfg.port,
            user=cfg.user,
            password=cfg.password,
            database=database,
            database_id=suite.family,
        )
    status = _column(catalog, suite.physical("status"))
    assert hasattr(status, "profile"), "ColumnProfile must carry bounded profile evidence"
    profile = status.profile
    assert suite.ground_truth["failed_count"] > 0
    assert "failed" in getattr(profile, "top_values", [])
    assert getattr(profile, "sample_strategy", "") != "physical_head_only"
    assert getattr(profile, "sample_size", 10**9) <= 100


def test_profiler_does_not_persist_sensitive_raw_samples():
    suite = next(s for s in build_schema_suites() if s.family == "commerce")
    with provision_suite(suite) as (cfg, database):
        catalog = profile_live_mysql(
            host=cfg.host,
            port=cfg.port,
            user=cfg.user,
            password=cfg.password,
            database=database,
            database_id=suite.family,
        )
    persisted = json.dumps(catalog.to_public_dict(), ensure_ascii=False)
    assert all(value not in persisted for value in suite.sensitive_values)
    phone = _column(catalog, suite.physical("phone"))
    assert phone.sensitive is True


def test_profiler_records_budget_and_evidence_source():
    suite = next(s for s in build_schema_suites() if s.family == "billing")
    with provision_suite(suite) as (cfg, database):
        catalog = profile_live_mysql(
            host=cfg.host,
            port=cfg.port,
            user=cfg.user,
            password=cfg.password,
            database=database,
            database_id=suite.family,
        )
    public = catalog.to_public_dict()
    assert public.get("profiling_policy", {}).get("read_only") is True
    assert public.get("profiling_policy", {}).get("per_query_timeout_ms", 0) > 0
    assert public.get("profiling_policy", {}).get("raw_samples_persisted") is False

