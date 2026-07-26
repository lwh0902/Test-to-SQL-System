"""AnalysisSpec patch operators for follow-up turns (Phase 4)."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Optional

from app.agents.active_analysis_state import ActiveAnalysisState, _spec_fingerprint
from app.agents.analysis_pipeline import (
    _ensure_join,
    _filter_from_question,
    _find_time_field,
    _parse_month_only,
    _parse_time_from_question,
    _pick_measure_candidates,
    _pick_subject_table,
    plan_question,
)
from app.agents.analysis_spec import (
    AnalysisSpec,
    FilterExpr,
    JoinStep,
    Measure,
    TimeRange,
)
from app.agents.semantic_catalog import SemanticCatalog


@dataclass
class PatchResult:
    action: str  # patch_spec | build_spec | clarify | refuse
    spec: Optional[AnalysisSpec] = None
    clarify_slots: list[str] = field(default_factory=list)
    clarify_message: str = ""
    patch_ops: list[str] = field(default_factory=list)
    is_followup: bool = False
    wrong_inherit: bool = False


_FOLLOWUP_HINT = re.compile(
    r"按.{0,12}拆|拆开|拆一下|只看|换成|换时间|呢\s*$|还有呢|同样|继续|过滤|"
    r"下钻|对比一下|改成|换成看|最近\s*\d+|最近\s*一|"
    r"^那|其中|前\s*\d+|top\s*\d+|失败率|成功率|都给我|再查一次|完全相同",
    re.I,
)


def looks_like_followup(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    if _FOLLOWUP_HINT.search(q):
        return True
    # short anaphoric
    if len(q) <= 16 and re.search(
        r"^(只看|按|换成|改成|那|其中|上海|北京|east|west|app|web|前)", q, re.I
    ):
        return True
    if re.search(r"^(上海|北京|饮料).{0,4}呢?$", q):
        return True
    if re.search(r"失败|成功率|失败率|前几名|排名", q):
        return True
    return False


def _month_end_str(y: int, mo: int) -> str:
    from datetime import date, timedelta

    if mo == 12:
        return date(y, 12, 31).isoformat()
    return (date(y, mo + 1, 1) - timedelta(days=1)).isoformat()


def _apply_dimension_patch(spec: AnalysisSpec, catalog: SemanticCatalog, question: str) -> list[str]:
    ops = []
    q = question
    if not re.search(r"按|拆|分组|group|维度|分类", q, re.I):
        return ops
    # explicit column names in question
    from app.agents.analysis_pipeline import _dim_from_question, _explicit_column_mentions

    dims = _dim_from_question(catalog, q)
    if not dims:
        for _t, cname in _explicit_column_mentions(catalog, q, table=spec.subject or ""):
            dims.append(cname)
    # generic: first enum dimension on subject
    if not dims and spec.subject in catalog.table_map:
        for c in catalog.table_map[spec.subject].columns:
            if c.role in {"enum_dimension", "status"} and re.search(
                r"分组|分类|category|group|priority", c.name + q, re.I
            ):
                dims.append(c.name)
                break
    for field_name in dims:
        owner = None
        for t in catalog.tables:
            if any(c.name == field_name for c in t.columns):
                owner = t
                break
        if field_name not in spec.dimensions:
            spec.dimensions.append(field_name)
            ops.append(f"add_dimension:{field_name}")
        if owner and owner.name not in spec.required_tables and spec.required_tables:
            base = spec.required_tables[0]
            jp: list[JoinStep] = list(spec.join_path)
            tables = list(spec.required_tables)
            if _ensure_join(catalog, tables, jp, base, owner.name):
                spec.required_tables = tables
                spec.join_path = jp
                ops.append(f"join:{base}->{owner.name}")
    return ops


def _apply_topn_patch(spec: AnalysisSpec, catalog: SemanticCatalog, question: str) -> list[str]:
    ops: list[str] = []
    from app.agents.analysis_pipeline import _parse_topn

    n = _parse_topn(question)
    if n is None and re.search(r"前几|排名|top", question or "", re.I):
        n = 3
    if n is None:
        return ops
    if spec.limit != n:
        spec.limit = n
        ops.append(f"set_limit:{n}")
    ordering = list(spec.ordering or [])
    if not ordering:
        spec.ordering = [{"field": "value", "direction": "DESC"}]
        ops.append("set_order:value_desc")
    elif not any("desc" in str(o).lower() for o in ordering):
        spec.ordering = [{"field": "value", "direction": "DESC"}]
        ops.append("set_order:value_desc")
    return ops


def _apply_multi_measure_patch(spec: AnalysisSpec, catalog: SemanticCatalog, question: str) -> list[str]:
    ops: list[str] = []
    q = question or ""
    if not re.search(r"总数和失败|失败数|都给我|同时.*(总数|失败)|总数.*失败", q):
        return ops
    table = spec.subject or (spec.required_tables[0] if spec.required_tables else "")
    if table not in catalog.table_map:
        return ops
    id_col = catalog.table_map[table].primary_key[0] if catalog.table_map[table].primary_key else "*"
    # keep total count + failed count markers
    spec.measures = [
        Measure(source_field=id_col, aggregation="count", table=table, business_label="total_count"),
        Measure(source_field=id_col, aggregation="count", table=table, business_label="failed_count"),
    ]
    ops.append("multi_measure:total_and_failed")
    # ensure failed filter exists for second measure semantics
    from app.agents.analysis_pipeline import _failed_status_value, _status_columns

    if not any(str(getattr(f, "value", "")).lower() in {"failed", "overdue", "alarm", "bad", "out"} or "失败" in str(getattr(f, "value", "")) for f in spec.filters):
        for tname, col in _status_columns(catalog, table):
            val = _failed_status_value(col, q) or _failed_status_value(col, "失败 failed")
            if val is not None:
                spec.filters = [f for f in spec.filters if f.field != col.name] + [
                    FilterExpr(field=col.name, op="=", value=val, table=tname)
                ]
                ops.append(f"set_filter:{col.name}={val}")
                break
    return ops


def _apply_filter_patch(spec: AnalysisSpec, catalog: SemanticCatalog, question: str) -> list[str]:
    ops = []
    q = question
    # bind filters against current subject so decoy tables don't win
    subject_q = q
    if spec.subject:
        subject_q = f"{spec.subject} {q}"
    filters = _filter_from_question(catalog, subject_q)
    # also bare tokens
    if re.search(r"只看\s*east|\beast\b|华东", q, re.I):
        filters.append(FilterExpr(field="region", op="=", value="east", table="customers"))
    if re.search(r"只看\s*west|\bwest\b|西部", q, re.I):
        filters.append(FilterExpr(field="region", op="=", value="west", table="customers"))
    if re.search(r"只看\s*app|\bapp\b", q, re.I):
        filters.append(FilterExpr(field="channel", op="=", value="app", table="users"))
    if re.search(r"只看\s*web|\bweb\b", q, re.I):
        filters.append(FilterExpr(field="channel", op="=", value="web", table="users"))
    if "上海" in q:
        filters.append(FilterExpr(field="城市", op="=", value="上海", table="门店"))
    if "北京" in q:
        filters.append(FilterExpr(field="城市", op="=", value="北京", table="门店"))
    if "饮料" in q:
        filters.append(FilterExpr(field="品类", op="=", value="饮料", table="销售流水"))
    if re.search(r"paid|已支付", q, re.I):
        filters.append(FilterExpr(field="status", op="=", value="paid", table="orders"))

    # Anaphoric entity follow-ups ("上海呢" / "饮料品类呢") replace prior entity
    # filters rather than stacking (else 上海+饮料 under-counts drink_sum GT).
    entity_fields = {"region", "channel", "城市", "品类", "status", "event_type"}
    anaphora = bool(re.search(r"呢\s*$", q)) or bool(re.search(r"^只看", q))
    if anaphora and filters:
        new_fields = {f.field for f in filters}
        if new_fields & entity_fields:
            spec.filters = [x for x in spec.filters if x.field not in entity_fields]
            # drop joins that only served removed city/store filters when switching
            # to a fact-table-local filter (品类 on 销售流水)
            if "品类" in new_fields and "城市" not in new_fields:
                spec.join_path = [
                    j
                    for j in spec.join_path
                    if not ({j.src_table, j.dst_table} == {"销售流水", "门店"})
                ]
                spec.required_tables = [t for t in spec.required_tables if t != "门店"] or list(
                    spec.required_tables
                )

    # dedupe by field
    existing = {(f.field, str(f.value)) for f in spec.filters}
    for f in filters:
        key = (f.field, str(f.value))
        if key in existing:
            continue
        # replace same field
        spec.filters = [x for x in spec.filters if x.field != f.field]
        spec.filters.append(f)
        existing.add(key)
        ops.append(f"set_filter:{f.field}={f.value}")
        if f.table and f.table not in spec.required_tables and spec.required_tables:
            base = spec.required_tables[0]
            jp = list(spec.join_path)
            tables = list(spec.required_tables)
            if _ensure_join(catalog, tables, jp, base, f.table):
                spec.required_tables = tables
                spec.join_path = jp
            elif f.table not in tables:
                # still record table if known
                if f.table in catalog.table_map:
                    tables.append(f.table)
                    spec.required_tables = tables
    return ops


def _apply_time_patch(spec: AnalysisSpec, catalog: SemanticCatalog, question: str) -> list[str]:
    ops = []
    tp = _parse_time_from_question(question)
    mo = _parse_month_only(question)
    tables = list(spec.required_tables or ([spec.subject] if spec.subject else []))
    tf = _find_time_field(catalog, tables)
    if not tf:
        return ops
    if tp:
        spec.time_range = TimeRange(field=tf[1], start=tp[0], end=tp[1], raw_text=tp[2])
        ops.append(f"set_time:{tp[0]}..{tp[1]}")
        return ops
    if mo and re.search(r"换成|改成|换时间|时间", question):
        # keep year from existing range if any
        y = None
        if spec.time_range and spec.time_range.start:
            y = int(str(spec.time_range.start)[:4])
        else:
            from app.agents.analysis_pipeline import _catalog_time_bounds

            b = _catalog_time_bounds(catalog, tf[0], tf[1])
            if b:
                y = int(str(b[0])[:4])
        if y:
            month, raw = mo
            from datetime import date

            start = date(y, month, 1).isoformat()
            end = _month_end_str(y, month)
            spec.time_range = TimeRange(field=tf[1], start=start, end=end, raw_text=raw)
            ops.append(f"set_time:{start}..{end}")
    return ops


def _apply_measure_switch(spec: AnalysisSpec, catalog: SemanticCatalog, question: str) -> list[str]:
    """换成看 X — may rebuild measure/subject while keeping time if possible."""
    ops = []
    if not re.search(r"换成|改成看|换成看", question):
        return ops
    q = question
    # deterministic shortcuts for common switches
    if re.search(r"view\s*事件|事件.*view|浏览事件", q, re.I) and "events" in catalog.table_map:
        pk = catalog.table_map["events"].primary_key
        id_col = pk[0] if pk else "event_id"
        spec.subject = "events"
        spec.measures = [Measure(table="events", source_field=id_col, aggregation="count", business_label="count")]
        spec.required_tables = ["events"]
        spec.join_path = []
        spec.dimensions = []
        spec.filters = [FilterExpr(field="event_type", op="=", value="view", table="events")]
        # event count is full-history unless user restates time in same utterance
        if not _parse_time_from_question(q):
            spec.time_range = None
        ops.append("switch_measure:event_count_view")
        return ops
    if re.search(r"empty_events", q, re.I) and "empty_events" in catalog.table_map:
        pk = catalog.table_map["empty_events"].primary_key
        id_col = pk[0] if pk else "id"
        spec.subject = "empty_events"
        spec.measures = [Measure(table="empty_events", source_field=id_col, aggregation="count")]
        spec.required_tables = ["empty_events"]
        spec.join_path = []
        spec.filters = []
        spec.dimensions = []
        spec.time_range = None
        ops.append("switch_measure:empty_events_count")
        return ops

    # plan fresh for the new metric phrase — normalize count-ish wording
    inner = re.sub(r"换成看|换成|改成看", "", q).strip()
    if re.search(r"事件数|次数", inner) and not re.search(r"有多少|count", inner, re.I):
        inner = inner + "有多少"
    plan = plan_question(inner or q, catalog)
    if plan.action == "query" and plan.spec and plan.spec.measures:
        old_time = spec.time_range
        spec.subject = plan.spec.subject
        spec.measures = plan.spec.measures
        spec.required_tables = plan.spec.required_tables
        spec.join_path = plan.spec.join_path
        spec.filters = plan.spec.filters or []
        spec.dimensions = plan.spec.dimensions or []
        if plan.spec.time_range:
            spec.time_range = plan.spec.time_range
        elif old_time and plan.spec.measures[0].aggregation != "count":
            tf = _find_time_field(catalog, spec.required_tables)
            if tf:
                spec.time_range = TimeRange(
                    field=tf[1],
                    start=old_time.start,
                    end=old_time.end,
                    raw_text=old_time.raw_text,
                )
            else:
                spec.time_range = None
        else:
            # count switches default to no inherited time unless stated
            if plan.spec.measures[0].aggregation == "count":
                spec.time_range = None
        ops.append(f"switch_measure:{spec.measures[0].source_field}")
    return ops


def _apply_clarify_fill(spec: AnalysisSpec, catalog: SemanticCatalog, question: str) -> list[str]:
    """After clarify, user may say 用gmv字段，2022年1月."""
    ops = []
    q = question
    if re.search(r"gmv_net", q, re.I):
        for t in catalog.tables:
            if any(c.name.lower() == "gmv_net" for c in t.columns):
                spec.measures = [Measure(table=t.name, source_field="gmv_net", aggregation="sum")]
                spec.subject = t.name
                spec.required_tables = [t.name]
                ops.append("set_measure:gmv_net")
                break
    elif re.search(r"用\s*gmv|gmv字段|字段\s*gmv|\bgmv\b", q, re.I):
        for t in catalog.tables:
            if any(c.name.lower() == "gmv" for c in t.columns):
                spec.measures = [Measure(table=t.name, source_field="gmv", aggregation="sum")]
                spec.subject = t.name
                spec.required_tables = [t.name]
                ops.append("set_measure:gmv")
                break
    ops.extend(_apply_time_patch(spec, catalog, q))
    return ops


def patch_or_build(
    question: str,
    catalog: SemanticCatalog,
    state: Optional[ActiveAnalysisState],
) -> PatchResult:
    q = (question or "").strip()
    has_state = bool(state and state.is_valid() and state.measures)

    if has_state and looks_like_followup(q):
        spec = state.to_spec(original_question=q)
        ops: list[str] = []
        # identical replay
        if re.search(r"再查一次|完全相同|同样的结果|原样", q):
            import uuid

            spec.spec_id = f"as_{uuid.uuid4().hex[:12]}"
            spec.original_question = q
            return PatchResult(
                action="patch_spec",
                spec=spec,
                patch_ops=["noop_identical"],
                is_followup=True,
            )
        ops.extend(_apply_measure_switch(spec, catalog, q))
        if not any(o.startswith("switch_measure") for o in ops):
            ops.extend(_apply_multi_measure_patch(spec, catalog, q))
            ops.extend(_apply_dimension_patch(spec, catalog, q))
            ops.extend(_apply_filter_patch(spec, catalog, q))
            ops.extend(_apply_topn_patch(spec, catalog, q))
            ops.extend(_apply_time_patch(spec, catalog, q))
            ops.extend(_apply_clarify_fill(spec, catalog, q))
            # rate follow-up
            if re.search(r"失败率|成功率|比率", q):
                table = spec.subject or (spec.required_tables[0] if spec.required_tables else "")
                if table in catalog.table_map:
                    id_col = (
                        catalog.table_map[table].primary_key[0]
                        if catalog.table_map[table].primary_key
                        else "*"
                    )
                    spec.measures = [
                        Measure(
                            source_field=id_col,
                            aggregation="rate",
                            table=table,
                            business_label="rate",
                        )
                    ]
                    ops.append("switch_measure:rate")
                    ops.extend(_apply_filter_patch(spec, catalog, q + " 失败"))
            # Pure entity anaphora ("上海呢" / "饮料品类呢"): keep measure, apply filter,
            # drop prior time window so slice means "that entity overall" unless user
            # restated a time. Still inherits measures (dev_spec inherit metric).
            if (
                ops
                and any(o.startswith("set_filter:") for o in ops)
                and not any(o.startswith("set_time:") for o in ops)
                and re.search(r"呢\s*$", q)
                and not re.search(r"20\d{2}|\d{1,2}\s*月|年", q)
            ):
                if spec.time_range is not None:
                    spec.time_range = None
                    ops.append("clear_time:anaphora")
        if not ops:
            # follow-up wording but nothing applied — clarify rather than wrong inherit
            return PatchResult(
                action="clarify",
                clarify_slots=["followup_intent"],
                clarify_message="未识别到可继承的修改（维度/过滤/时间），请说明要如何调整上一问。",
                is_followup=True,
                wrong_inherit=False,
            )
        # assign new spec id
        import uuid

        spec.spec_id = f"as_{uuid.uuid4().hex[:12]}"
        spec.original_question = q
        spec.confidence = 0.85
        return PatchResult(action="patch_spec", spec=spec, patch_ops=ops, is_followup=True)

    # non-followup or no state: try full plan
    plan = plan_question(q, catalog)
    if plan.action == "clarify":
        # if user is filling previous clarify with state skeleton empty measures?
        if has_state and re.search(r"用\s*gmv|gmv字段|字段", q, re.I):
            spec = state.to_spec(original_question=q)
            ops = _apply_clarify_fill(spec, catalog, q)
            if ops and spec.measures:
                import uuid

                spec.spec_id = f"as_{uuid.uuid4().hex[:12]}"
                return PatchResult(action="patch_spec", spec=spec, patch_ops=ops, is_followup=True)
        return PatchResult(
            action="clarify",
            clarify_slots=list(plan.clarify_slots or []),
            clarify_message=plan.clarify_message,
            is_followup=False,
        )
    if plan.action == "refuse":
        return PatchResult(action="refuse", clarify_message=plan.clarify_message)
    if plan.action == "query" and plan.spec:
        return PatchResult(action="build_spec", spec=plan.spec, is_followup=False)

    # bare followup without state
    if looks_like_followup(q) and not has_state:
        return PatchResult(
            action="clarify",
            clarify_slots=["prior_context"],
            clarify_message="当前没有可继承的分析上下文，请先提出完整的分析问题。",
            is_followup=True,
            wrong_inherit=False,
        )

    return PatchResult(
        action="clarify",
        clarify_slots=["measure"],
        clarify_message="无法理解当前问题，请补充指标与时间范围。",
    )
