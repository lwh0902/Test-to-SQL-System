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


def _filter_condition_sql(filter_expr: Any, default_table: str) -> str:
    table = filter_expr.table or default_table
    op = filter_expr.op or "="
    value = filter_expr.value
    if isinstance(value, str):
        literal = "'" + value.replace("'", "''") + "'"
    else:
        literal = str(value)
    return f"{_qident(table)}.{_qident(filter_expr.field)} {op} {literal}"


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

    def _append_join(src_table: str, src_column: str, dst_table: str, dst_column: str, source: str, confidence: float) -> bool:
        """JOIN the side not yet present; never re-JOIN the FROM base as alias-less duplicate."""
        nonlocal join_sql
        src_in = src_table in joined
        dst_in = dst_table in joined
        if src_in and dst_in:
            return True
        if not src_in and not dst_in:
            # attach dst onto base graph only if one endpoint is base-adjacent later
            # Prefer joining dst if src is base, else join src.
            if src_table == base:
                other, left_t, left_c, right_t, right_c = dst_table, src_table, src_column, dst_table, dst_column
            elif dst_table == base:
                other, left_t, left_c, right_t, right_c = src_table, src_table, src_column, dst_table, dst_column
            else:
                other, left_t, left_c, right_t, right_c = dst_table, src_table, src_column, dst_table, dst_column
        elif src_in and not dst_in:
            other, left_t, left_c, right_t, right_c = dst_table, src_table, src_column, dst_table, dst_column
        else:  # dst_in and not src_in — edge stored child→parent while FROM is parent
            other, left_t, left_c, right_t, right_c = src_table, src_table, src_column, dst_table, dst_column
        if other in joined:
            return True
        join_sql += (
            f" JOIN {_qident(other)} ON {_qident(left_t)}.{_qident(left_c)}"
            f" = {_qident(right_t)}.{_qident(right_c)}"
        )
        joined.add(other)
        join_sources.append(f"{source}:{confidence}")
        return True

    for step in spec.join_path:
        if step.confidence < 0.85 and step.source != "user":
            used_low = True
            return CompileResult(ok=False, errors=["low_confidence_join_blocked"], used_auto_low_join=True)
        if not policy.can_auto_join(step.src_table, step.dst_table) and step.confidence < 0.85:
            return CompileResult(ok=False, errors=["join_not_allowed"], used_auto_low_join=False)
        _append_join(
            step.src_table,
            step.src_column,
            step.dst_table,
            step.dst_column,
            step.source,
            step.confidence,
        )

    # if multi table without join_path — require high conf path or fail
    if len(tables) > 1 and not spec.join_path:
        # try auto path between first two
        a, b = tables[0], tables[1]
        edge = policy.join_path(a, b)
        if not edge:
            return CompileResult(ok=False, errors=["missing_join_path"])
        _append_join(
            edge.src_table,
            edge.src_column,
            edge.dst_table,
            edge.dst_column,
            getattr(edge, "kind", "inferred"),
            edge.confidence,
        )

    select_parts = []
    rate_filters = list(spec.filters or [])
    consumed_global_filter_indexes: set[int] = set()
    multiple_measures = len(spec.measures) > 1
    is_sample = any((m.aggregation or "").lower() == "sample" for m in spec.measures)
    if is_sample:
        # row preview: pick useful columns from base (+ joined) without aggregation
        cols_out: list[str] = []
        for tname in tables:
            tprof = catalog.table_map.get(tname)
            if not tprof:
                continue
            picked = 0
            for c in tprof.columns:
                nm = c.name or ""
                # skip huge blobs if any
                if re.search(r"password|secret|token|blob", nm, re.I):
                    continue
                cols_out.append(f"{_qident(tname)}.{_qident(nm)}")
                picked += 1
                if picked >= 12:
                    break
        select_parts = cols_out or [f"{_qident(base)}.*"]
    else:
      for measure_index, m in enumerate(spec.measures):
        agg = (m.aggregation or "sum").upper()
        table = m.table or base
        alias_name = (
            re.sub(r"[^A-Za-z0-9_\u4e00-\u9fff]", "_", m.business_label or "")
            if multiple_measures
            else "value"
        ) or f"value_{measure_index + 1}"
        alias_sql = _qident(alias_name) if multiple_measures else "value"
        if agg == "COUNT":
            if m.filter is not None:
                cond = _filter_condition_sql(m.filter, table)
                select_parts.append(f"SUM(CASE WHEN {cond} THEN 1 ELSE 0 END) AS {alias_sql}")
            else:
                select_parts.append(f"COUNT(*) AS {alias_sql}")
        elif agg == "RATE":
            # conditional rate: filtered_count / total_count on same subject
            rate_filter = m.filter
            if rate_filter is None and rate_filters:
                rate_filter = rate_filters[0]
                consumed_global_filter_indexes.add(0)
            if rate_filter is not None:
                cond = _filter_condition_sql(rate_filter, table)
                select_parts.append(
                    f"(SUM(CASE WHEN {cond} THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*),0)) AS {alias_sql}"
                )
            else:
                select_parts.append(f"COUNT(*) AS {alias_sql}")
        else:
            select_parts.append(
                f"{agg}({_qident(table)}.{_qident(m.source_field)}) AS {alias_sql}"
            )
    # Only predicates consumed by a rate CASE are removed from WHERE. Other
    # global slices (time/region/etc.) must still constrain the denominator.
    spec_filters_for_where = [
        f for i, f in enumerate(spec.filters or []) if i not in consumed_global_filter_indexes
    ]
    # dimensions (not for sample/row preview)
    group_cols = []
    if not is_sample:
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
