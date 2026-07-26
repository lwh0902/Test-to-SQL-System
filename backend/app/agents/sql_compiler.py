"""Deterministic SQL compiler from AnalysisSpec + SemanticCatalog (Phase 3)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.agents.analysis_spec import AnalysisSpec, validate_spec
from app.agents.semantic_catalog import JoinPolicy, SemanticCatalog


@dataclass
class CompileResult:
    ok: bool
    sql: str = ""
    errors: list[str] = field(default_factory=list)
    used_auto_low_join: bool = False
    join_sources: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)


def _qident(name: str) -> str:
    # quote identifier; support unicode
    n = name.replace("`", "")
    return f"`{n}`"


def compile_spec(spec: AnalysisSpec, catalog: SemanticCatalog) -> CompileResult:
    v = validate_spec(spec, catalog)
    if not v.ok:
        return CompileResult(ok=False, errors=list(v.errors) + [f"unresolved:{u}" for u in v.unresolved])

    if not spec.measures:
        return CompileResult(ok=False, errors=["no_measures"])

    tables = list(spec.required_tables or [])
    if not tables:
        # derive from measures
        for m in spec.measures:
            if m.table and m.table not in tables:
                tables.append(m.table)
    if not tables:
        return CompileResult(ok=False, errors=["no_tables"])

    # join validation
    policy = JoinPolicy(catalog)
    used_low = False
    join_sql = ""
    join_sources: list[str] = []
    base = tables[0]
    joined = {base}
    for step in spec.join_path:
        if step.confidence < 0.85 and step.source != "user":
            used_low = True
            return CompileResult(ok=False, errors=["low_confidence_join_blocked"], used_auto_low_join=True)
        if not policy.can_auto_join(step.src_table, step.dst_table) and step.confidence < 0.85:
            return CompileResult(ok=False, errors=["join_not_allowed"], used_auto_low_join=False)
        # add join
        other = step.dst_table if step.src_table in joined else step.src_table
        if other in joined:
            continue
        join_sql += (
            f" JOIN {_qident(step.dst_table)} ON {_qident(step.src_table)}.{_qident(step.src_column)}"
            f" = {_qident(step.dst_table)}.{_qident(step.dst_column)}"
        )
        joined.add(step.dst_table)
        joined.add(step.src_table)
        join_sources.append(f"{step.source}:{step.confidence}")

    # if multi table without join_path — require high conf path or fail
    if len(tables) > 1 and not spec.join_path:
        # try auto path between first two
        a, b = tables[0], tables[1]
        edge = policy.join_path(a, b)
        if not edge:
            return CompileResult(ok=False, errors=["missing_join_path"])
        join_sql += (
            f" JOIN {_qident(edge.dst_table)} ON {_qident(edge.src_table)}.{_qident(edge.src_column)}"
            f" = {_qident(edge.dst_table)}.{_qident(edge.dst_column)}"
        )
        join_sources.append(f"{edge.kind}:{edge.confidence}")

    select_parts = []
    rate_filters = list(spec.filters or [])
    for m in spec.measures:
        agg = (m.aggregation or "sum").upper()
        table = m.table or base
        if agg == "COUNT":
            select_parts.append("COUNT(*) AS value")
        elif agg == "RATE":
            # conditional rate: filtered_count / total_count on same subject
            if rate_filters:
                f = rate_filters[0]
                ft = f.table or table
                lit = f.value
                if isinstance(lit, str):
                    lit_s = lit.replace("'", "''")
                    cond = f"{_qident(ft)}.{_qident(f.field)} = '{lit_s}'"
                else:
                    cond = f"{_qident(ft)}.{_qident(f.field)} = {lit}"
                select_parts.append(
                    f"(SUM(CASE WHEN {cond} THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*),0)) AS value"
                )
            else:
                select_parts.append("COUNT(*) AS value")
        else:
            select_parts.append(
                f"{agg}({_qident(table)}.{_qident(m.source_field)}) AS value"
            )
    # rate already encodes filter as CASE — avoid double-filtering in WHERE
    if any((m.aggregation or "").lower() == "rate" for m in spec.measures):
        spec_filters_for_where = []
    else:
        spec_filters_for_where = list(spec.filters or [])
    # dimensions
    group_cols = []
    for d in spec.dimensions or []:
        # dimension may be table.field or field
        if "." in d:
            t, c = d.split(".", 1)
            group_cols.append(f"{_qident(t)}.{_qident(c)}")
        else:
            # find table
            col_sql = None
            for tname in tables:
                cols = {c.name for c in catalog.table_map[tname].columns}
                if d in cols:
                    col_sql = f"{_qident(tname)}.{_qident(d)}"
                    break
            if col_sql:
                group_cols.append(col_sql)
    select_parts = group_cols + select_parts

    where = []
    params: dict[str, Any] = {}
    if spec.time_range and spec.time_range.field:
        tr = spec.time_range
        tfield = tr.field
        # qualify
        t_table = base
        for tname in tables:
            if tfield in {c.name for c in catalog.table_map[tname].columns}:
                t_table = tname
                break
        where.append(
            f"{_qident(t_table)}.{_qident(tfield)} >= '{tr.start}' AND {_qident(t_table)}.{_qident(tfield)} <= '{tr.end} 23:59:59'"
        )
    for i, f in enumerate(spec_filters_for_where):
        ft = f.table or base
        for tname in tables:
            if f.field in {c.name for c in catalog.table_map[tname].columns}:
                ft = tname
                break
        op = f.op or "="
        if op not in {"=", "!=", ">", "<", ">=", "<=", "LIKE", "in"}:
            return CompileResult(ok=False, errors=[f"bad_op:{op}"])
        lit = f.value
        if isinstance(lit, str):
            lit_s = lit.replace("'", "''")
            where.append(f"{_qident(ft)}.{_qident(f.field)} {op} '{lit_s}'")
        else:
            where.append(f"{_qident(ft)}.{_qident(f.field)} {op} {lit}")

    sql = f"SELECT {', '.join(select_parts)} FROM {_qident(base)}{join_sql}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if group_cols:
        sql += " GROUP BY " + ", ".join(group_cols)
    if spec.ordering:
        # simple
        ord_parts = []
        for o in spec.ordering:
            col = o.get("field", "value")
            direction = o.get("direction", "DESC")
            ord_parts.append(f"{_qident(col) if col != 'value' else 'value'} {direction}")
        if ord_parts:
            sql += " ORDER BY " + ", ".join(ord_parts)
    # FreeformSQLGuard / product preview hard cap ≤ 500 (dev_spec rows preview)
    lim = int(spec.limit) if spec.limit else 500
    lim = max(1, min(lim, 500))
    sql += f" LIMIT {lim}"

    # final safety strip
    if re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT)\b", sql, re.I):
        return CompileResult(ok=False, errors=["write_keyword_in_sql"])

    return CompileResult(
        ok=True,
        sql=sql,
        used_auto_low_join=used_low,
        join_sources=join_sources,
        params=params,
    )
