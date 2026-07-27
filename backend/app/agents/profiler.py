"""Profiler: DDL (+ optional seed) → SemanticCatalog (Phase 2).

Works offline on eval DDL fixtures; can later bind to live information_schema.
Never stores credentials or raw business row dumps into the catalog.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Optional

from app.agents.semantic_catalog import (
    BoundedColumnStats,
    CandidateMeasure,
    ColumnProfile,
    FieldRole,
    ReadinessReport,
    ReadinessStatus,
    RelationEdge,
    SemanticCatalog,
    TableProfile,
    compute_schema_fingerprint,
)

_SENSITIVE = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "credit_card",
    "id_card",
    "phone",
    "mobile",
    "email",
}

_TIME_NAME = re.compile(
    r"(^|_)(date|time|dt|day|month|year|ts|timestamp|created|updated|pay_time|order_date|event_time|signup)(_|$)|"
    r"(成交日|成交时间|日期|时间|年月|下单日|支付日|创建日|更新日)",
    re.I,
)
_AMOUNT_NAME = re.compile(
    r"(amount|amt|price|gmv|sales|revenue|fee|cost|金额|销售额|单价|流水)",
    re.I,
)
_STATUS_NAME = re.compile(r"(status|state|状态)", re.I)
_ID_NAME = re.compile(r"(^id$|_id$|编号|号$)", re.I)
_RATIO_NAME = re.compile(r"(rate|ratio|pct|percent|率)", re.I)

_CREATE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(`?[\w\u4e00-\u9fff]+`?)\s*\((.*?)\)\s*;",
    re.I | re.S,
)
_FK_INLINE = re.compile(
    r"FOREIGN\s+KEY\s*\((`?[\w\u4e00-\u9fff]+`?)\)\s*REFERENCES\s+(`?[\w\u4e00-\u9fff]+`?)\s*\((`?[\w\u4e00-\u9fff]+`?)\)",
    re.I,
)
_FK_TABLE = re.compile(
    r"CONSTRAINT\s+\S+\s+FOREIGN\s+KEY\s*\((`?[\w\u4e00-\u9fff]+`?)\)\s*REFERENCES\s+(`?[\w\u4e00-\u9fff]+`?)\s*\((`?[\w\u4e00-\u9fff]+`?)\)",
    re.I,
)
_PK = re.compile(r"PRIMARY\s+KEY\s*\(([^)]+)\)", re.I)
_PK_INLINE = re.compile(r"\bPRIMARY\s+KEY\b", re.I)
_UNIQUE = re.compile(r"UNIQUE\s*(?:KEY|INDEX)?\s*(?:`?\w+`?)?\s*\(([^)]+)\)", re.I)
_KEY = re.compile(r"(?:KEY|INDEX)\s+(`?[\w\u4e00-\u9fff]+`?)\s*\(([^)]+)\)", re.I)


def _strip_q(name: str) -> str:
    return (name or "").strip().strip("`").strip('"').strip("'")


def _split_defs(body: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return [p for p in parts if p]


def _parse_type(rest: str) -> str:
    m = re.match(r"([a-zA-Z]+(?:\s*\([^)]*\))?)", rest.strip())
    return (m.group(1) if m else rest.split()[0] if rest.split() else "").strip()


def infer_field_role(name: str, data_type: str, *, is_pk: bool = False) -> str:
    n = name.lower()
    dt = (data_type or "").lower()
    if is_pk or (_ID_NAME.search(name) and "int" in dt):
        if is_pk or n == "id" or n.endswith("_id") or name.endswith("编号"):
            return FieldRole.IDENTIFIER.value
    if _TIME_NAME.search(name) or any(x in dt for x in ("date", "time", "timestamp")):
        return FieldRole.TIME.value
    if _AMOUNT_NAME.search(name) or (
        any(x in dt for x in ("decimal", "numeric", "money", "float", "double"))
        and any(k in n for k in ("amt", "amount", "price", "gmv", "pay", "金额", "sales"))
    ):
        return FieldRole.AMOUNT.value
    if _RATIO_NAME.search(name):
        return FieldRole.RATIO.value
    if _STATUS_NAME.search(name) or "enum" in dt:
        return FieldRole.STATUS.value
    if any(x in dt for x in ("int", "decimal", "numeric", "float", "double")):
        return FieldRole.CONTINUOUS_NUMERIC.value
    if any(x in dt for x in ("char", "text", "varchar")):
        # short name-like dims
        if any(k in n for k in ("region", "city", "channel", "category", "type", "城市", "品类", "渠道", "名称")):
            return FieldRole.ENUM_DIMENSION.value
        return FieldRole.TEXT.value
    return FieldRole.UNKNOWN.value


def _is_sensitive(name: str) -> bool:
    n = name.lower()
    if n in _SENSITIVE:
        return True
    return any(s in n for s in _SENSITIVE)


def parse_ddl_tables(ddl: str) -> tuple[list[TableProfile], list[RelationEdge]]:
    tables: list[TableProfile] = []
    fks: list[RelationEdge] = []
    if not (ddl or "").strip():
        return tables, fks

    for m in _CREATE_RE.finditer(ddl):
        tname = _strip_q(m.group(1))
        body = m.group(2)
        # table comment may sit on first PK column COMMENT in gate DDLs
        table_comment = ""
        cols: list[ColumnProfile] = []
        pk_cols: list[str] = []
        indexes: list[dict] = []

        # table-level PK
        for pkm in _PK.finditer(body):
            pk_cols = [_strip_q(x) for x in pkm.group(1).split(",")]

        for fk_re in (_FK_TABLE, _FK_INLINE):
            for fkm in fk_re.finditer(body):
                src_c, dst_t, dst_c = _strip_q(fkm.group(1)), _strip_q(fkm.group(2)), _strip_q(fkm.group(3))
                fks.append(
                    RelationEdge(
                        src_table=tname,
                        src_column=src_c,
                        dst_table=dst_t,
                        dst_column=dst_c,
                        kind="explicit_fk",
                        confidence=1.0,
                        evidence=["ddl_foreign_key"],
                        allow_auto_join=True,
                    )
                )

        for um in _UNIQUE.finditer(body):
            ucols = [_strip_q(x) for x in um.group(1).split(",")]
            indexes.append({"name": "unique_" + "_".join(ucols), "columns": ucols, "unique": True, "primary": False})

        for km in _KEY.finditer(body):
            iname = _strip_q(km.group(1))
            icols = [_strip_q(x) for x in km.group(2).split(",")]
            if iname.upper() in {"PRIMARY", "UNIQUE", "KEY", "INDEX", "FOREIGN"}:
                continue
            indexes.append({"name": iname, "columns": icols, "unique": False, "primary": False})

        for defn in _split_defs(body):
            up = defn.upper().lstrip()
            if up.startswith(
                ("PRIMARY KEY", "FOREIGN KEY", "CONSTRAINT", "UNIQUE", "KEY ", "INDEX ", "CHECK ")
            ):
                continue
            # column def: name type ...
            cm = re.match(r"(`?[\w\u4e00-\u9fff]+`?)\s+(.+)", defn.strip())
            if not cm:
                continue
            cname = _strip_q(cm.group(1))
            rest = cm.group(2)
            ctype = _parse_type(rest)
            nullable = "NOT NULL" not in rest.upper()
            is_pk_inline = bool(_PK_INLINE.search(rest))
            if is_pk_inline and cname not in pk_cols:
                pk_cols.append(cname)
            comment = ""
            cmt = re.search(r"COMMENT\s+'([^']*)'", rest, re.I)
            if cmt:
                comment = cmt.group(1)
            # Gate DDLs put table business comment on the PK column COMMENT.
            if (cname in pk_cols or is_pk_inline) and comment and not table_comment:
                table_comment = comment
            col = ColumnProfile(
                name=cname,
                data_type=ctype,
                nullable=nullable,
                comment=comment,
                is_primary_key=cname in pk_cols or is_pk_inline,
                sensitive=_is_sensitive(cname),
            )
            col.role = infer_field_role(cname, ctype, is_pk=col.is_primary_key)
            cols.append(col)

        # mark FK columns
        fk_cols = {e.src_column for e in fks if e.src_table == tname}
        for c in cols:
            if c.name in fk_cols:
                c.is_foreign_key = True
                # FK keys are identifiers, not additive measures
                c.role = FieldRole.IDENTIFIER.value

        if pk_cols:
            indexes.insert(
                0,
                {"name": "PRIMARY", "columns": list(pk_cols), "unique": True, "primary": True},
            )

        tables.append(
            TableProfile(
                name=tname,
                comment=table_comment,
                columns=cols,
                primary_key=list(pk_cols),
                indexes=indexes,
            )
        )
    return tables, fks


def infer_relations(tables: list[TableProfile], explicit: list[RelationEdge]) -> list[RelationEdge]:
    """Name/type based inference. Explicit FK already allow_auto_join."""
    out = list(explicit)
    explicit_keys = {e.key() for e in explicit}
    # pk map: table -> pk cols
    pk_map = {t.name: list(t.primary_key) for t in tables}
    # also single-column unique as join target
    table_by = {t.name: t for t in tables}
    col_index: dict[str, list[tuple[str, ColumnProfile]]] = defaultdict(list)
    for t in tables:
        for c in t.columns:
            col_index[c.name.lower()].append((t.name, c))

    for t in tables:
        for c in t.columns:
            if c.is_primary_key and c.name.lower() in {"id"}:
                continue
            # pattern: foo_id -> foo.id or foos.id
            n = c.name
            nl = n.lower()
            if not (nl.endswith("_id") or n.endswith("编号")):
                continue
            if nl.endswith("_id"):
                base = nl[: -3]
            else:
                base = n[:-2] if n.endswith("编号") else nl
            candidates = []
            for tname, pkcols in pk_map.items():
                if tname == t.name:
                    continue
                tnl = tname.lower()
                if tnl == base or tnl.rstrip("s") == base or tnl == base + "s" or tnl == base + "es":
                    if pkcols:
                        candidates.append((tname, pkcols[0], 0.9, "name_match_table_pk"))
                # users.user_id style same column name on pk table
            # same column name equals pk name on other table
            for ot_name, oc in col_index.get(nl, []):
                if ot_name == t.name:
                    continue
                ot = table_by[ot_name]
                if oc.is_primary_key or oc.name in ot.primary_key:
                    candidates.append((ot_name, oc.name, 0.92, "same_colname_to_pk"))
                elif oc.name.lower() == nl and "int" in (oc.data_type or "").lower():
                    candidates.append((ot_name, oc.name, 0.75, "same_colname_int"))

            # user_id -> users.user_id
            if nl.endswith("_id"):
                stem = nl[:-3]
                for ot in tables:
                    if ot.name == t.name:
                        continue
                    if stem in ot.name.lower() or ot.name.lower().rstrip("s") == stem:
                        for oc in ot.columns:
                            if oc.name.lower() == nl or oc.is_primary_key:
                                conf = 0.88 if oc.is_primary_key or oc.name.lower() == nl else 0.7
                                candidates.append((ot.name, oc.name, conf, "stem_table_match"))

            best: dict[tuple, tuple[float, str]] = {}
            for dst_t, dst_c, conf, ev in candidates:
                key = (t.name, c.name, dst_t, dst_c)
                if key in explicit_keys:
                    continue
                if key not in best or conf > best[key][0]:
                    best[key] = (conf, ev)
            for key, (conf, ev) in best.items():
                allow = conf >= 0.85
                out.append(
                    RelationEdge(
                        src_table=key[0],
                        src_column=key[1],
                        dst_table=key[2],
                        dst_column=key[3],
                        kind="inferred",
                        confidence=round(conf, 3),
                        evidence=[ev, "type_or_name"],
                        risk="inferred_join" if conf < 0.9 else "",
                        allow_auto_join=allow,
                    )
                )
                c.is_foreign_key = True
    # dedupe by key keep higher confidence
    merged: dict[tuple, RelationEdge] = {}
    for r in out:
        k = r.key()
        if k not in merged or r.confidence > merged[k].confidence:
            merged[k] = r
    return list(merged.values())


def _apply_seed_column_profiles(seed_sql: str, tables: list[TableProfile]) -> None:
    """Derive bounded status vocab / top-k from offline seed inserts (no raw dump)."""
    if not (seed_sql or "").strip():
        return
    insert_re = re.compile(
        r"INSERT\s+INTO\s+(`?[\w\u4e00-\u9fff]+`?)\s*\(([^)]+)\)\s*VALUES\s*(.+?);",
        re.I | re.S,
    )
    tmap = {t.name: t for t in tables}
    for m in insert_re.finditer(seed_sql):
        tname = _strip_q(m.group(1))
        if tname not in tmap:
            match = next((k for k in tmap if k.lower() == tname.lower()), None)
            if not match:
                continue
            tname = match
        cols = [_strip_q(c) for c in m.group(2).split(",")]
        values_blob = m.group(3)
        rows_raw = re.split(r"\),\s*\(", values_blob.strip().lstrip("(").rstrip(")"))
        col_values: dict[str, list[str]] = defaultdict(list)
        for row in rows_raw:
            cells: list[str] = []
            buf = ""
            inq = False
            for ch in row:
                if ch == "'":
                    inq = not inq
                    buf += ch
                elif ch == "," and not inq:
                    cells.append(buf.strip())
                    buf = ""
                else:
                    buf += ch
            if buf:
                cells.append(buf.strip())
            for i, col in enumerate(cols):
                if i >= len(cells):
                    continue
                raw = cells[i].strip()
                if raw.upper() == "NULL":
                    continue
                if raw.startswith("'") and raw.endswith("'"):
                    raw = raw[1:-1]
                col_values[col].append(raw)
        tp = tmap[tname]
        for c in tp.columns:
            vals = col_values.get(c.name) or []
            if not vals:
                continue
            # only low-cardinality / status-like columns get top-k from seed
            if c.role not in {
                FieldRole.STATUS.value,
                FieldRole.ENUM_DIMENSION.value,
                FieldRole.TEXT.value,
            } and not _STATUS_NAME.search(c.name):
                # still allow short string enums
                uniq_probe = {v for v in vals[:50]}
                if len(uniq_probe) > 12:
                    continue
            counts: dict[str, int] = defaultdict(int)
            for v in vals:
                counts[str(v)] += 1
            ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            top_vals = [k for k, _ in ranked[:12]]
            # never persist obvious phone-like raw values in top_values for sensitive cols
            if c.sensitive:
                top_vals = []
                ranked = []
            c.profile = BoundedColumnStats(
                top_values=top_vals,
                value_counts={k: int(v) for k, v in ranked[:12]},
                sample_strategy="seed_insert_scan",
                sample_size=min(len(vals), 100),
                evidence_sources=["seed_distribution", "metadata"],
                approx_distinct_ratio=(len(counts) / max(len(vals), 1)),
            )
            # Promote opaque low-cardinality strings using distribution evidence
            if c.role in {FieldRole.TEXT.value, FieldRole.UNKNOWN.value, FieldRole.ENUM_DIMENSION.value}:
                failish = {"failed", "fail", "overdue", "alarm", "bad", "error", "out", "ok", "success"}
                tops = {str(v).lower() for v in top_vals}
                if tops & failish or any(re.search(r"fail|overdue|alarm|bad|out|ok", v, re.I) for v in top_vals):
                    if len(top_vals) <= 12:
                        c.role = FieldRole.STATUS.value
                elif 1 < len(top_vals) <= 12 and not c.sensitive:
                    c.role = FieldRole.ENUM_DIMENSION.value


def _parse_seed_dates(seed_sql: str, tables: list[TableProfile]) -> None:
    """Best-effort extract min/max date literals per table.column from INSERTs."""
    if not seed_sql:
        return
    # INSERT INTO t (cols) VALUES (...),(...);
    insert_re = re.compile(
        r"INSERT\s+INTO\s+(`?[\w\u4e00-\u9fff]+`?)\s*\(([^)]+)\)\s*VALUES\s*(.+?);",
        re.I | re.S,
    )
    date_lit = re.compile(r"'(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2})?)'")
    tmap = {t.name: t for t in tables}
    for m in insert_re.finditer(seed_sql):
        tname = _strip_q(m.group(1))
        if tname not in tmap:
            # try without case
            match = next((k for k in tmap if k.lower() == tname.lower()), None)
            if not match:
                continue
            tname = match
        cols = [_strip_q(c) for c in m.group(2).split(",")]
        values_blob = m.group(3)
        # split rows by ),(
        rows_raw = re.split(r"\),\s*\(", values_blob.strip().lstrip("(").rstrip(")"))
        time_cols = [
            c
            for c in cols
            if any(
                cc.name == c and cc.role == FieldRole.TIME.value for cc in tmap[tname].columns
            )
        ]
        if not time_cols:
            # still try columns that look like dates in values
            time_cols = [
                c
                for c in cols
                if _TIME_NAME.search(c)
                or any(cc.name == c and "date" in (cc.data_type or "").lower() for cc in tmap[tname].columns)
            ]
        bounds: dict[str, list[str]] = defaultdict(list)
        for row in rows_raw:
            # naive split by comma respecting quotes
            cells: list[str] = []
            buf = ""
            inq = False
            for ch in row:
                if ch == "'":
                    inq = not inq
                    buf += ch
                elif ch == "," and not inq:
                    cells.append(buf.strip())
                    buf = ""
                else:
                    buf += ch
            if buf:
                cells.append(buf.strip())
            for i, col in enumerate(cols):
                if col not in time_cols or i >= len(cells):
                    continue
                dm = date_lit.search(cells[i])
                if dm:
                    bounds[col].append(dm.group(1)[:10])
        tb = tmap[tname].time_bounds
        for col, vals in bounds.items():
            if not vals:
                continue
            tb[col] = {"min": min(vals), "max": max(vals), "field": col}

    # row tier from insert counts
    for t in tables:
        # count value tuples for table
        cnt = 0
        for m in insert_re.finditer(seed_sql):
            if _strip_q(m.group(1)) == t.name or _strip_q(m.group(1)).lower() == t.name.lower():
                blob = m.group(3)
                cnt += len(re.split(r"\),\s*\(", blob.strip().lstrip("(").rstrip(")")))
        if cnt == 0:
            # empty table possible
            t.row_count_tier = "empty"
        elif cnt < 10:
            t.row_count_tier = "tiny"
        elif cnt < 1000:
            t.row_count_tier = "small"
        else:
            t.row_count_tier = "medium"


def _candidate_measures(tables: list[TableProfile]) -> list[CandidateMeasure]:
    out: list[CandidateMeasure] = []
    for t in tables:
        for c in t.columns:
            if c.is_primary_key or c.is_foreign_key or c.role == FieldRole.IDENTIFIER.value:
                continue
            if c.role in (FieldRole.AMOUNT.value, FieldRole.CONTINUOUS_NUMERIC.value, FieldRole.RATIO.value):
                nl = c.name.lower()
                # skip id-like numerics even if mis-roled
                if nl == "id" or nl.endswith("_id") or nl.startswith("dimension_id") or c.name.endswith("编号"):
                    continue
                dt = (c.data_type or "").lower()
                aggs = ["sum", "avg", "min", "max"] if c.role != FieldRole.RATIO.value else ["avg", "min", "max"]
                if c.role == FieldRole.CONTINUOUS_NUMERIC.value and not _AMOUNT_NAME.search(c.name):
                    aggs = ["sum", "avg", "min", "max", "count"]
                # prefer true decimals over integer surrogate keys
                role = c.role
                if "decimal" in dt or "numeric" in dt or "float" in dt or "double" in dt:
                    if role == FieldRole.CONTINUOUS_NUMERIC.value:
                        role = FieldRole.AMOUNT.value
                out.append(
                    CandidateMeasure(
                        table=t.name,
                        field=c.name,
                        aggregations=aggs,
                        business_label=c.comment or c.name,
                        role=role,
                    )
                )
    # stable: amount/decimal first
    out.sort(key=lambda m: (0 if m.role == FieldRole.AMOUNT.value else 1, m.table, m.field))
    return out


def _candidate_dimensions(tables: list[TableProfile]) -> list[dict]:
    out = []
    for t in tables:
        for c in t.columns:
            if c.role in (
                FieldRole.ENUM_DIMENSION.value,
                FieldRole.STATUS.value,
                FieldRole.TIME.value,
            ):
                out.append({"table": t.name, "field": c.name, "role": c.role})
    return out


def compute_readiness(cat: SemanticCatalog) -> ReadinessReport:
    if not cat.tables:
        return ReadinessReport(
            status=ReadinessStatus.BLOCKED,
            reasons=["未发现任何表，无法建档"],
            limited_capabilities=["analysis", "query", "diagnosis"],
        )
    n_cols = sum(len(t.columns) for t in cat.tables)
    if n_cols == 0:
        return ReadinessReport(
            status=ReadinessStatus.BLOCKED,
            reasons=["表无字段"],
            limited_capabilities=["analysis", "query"],
        )

    reasons: list[str] = []
    limits: list[str] = []
    has_time = any(c.role == FieldRole.TIME.value for t in cat.tables for c in t.columns)
    has_measure = bool(cat.candidate_measures)
    has_rel = bool(cat.relations)
    empty_all = all(t.row_count_tier == "empty" for t in cat.tables)

    if empty_all:
        reasons.append("所有表在样本中为空或未知行数")
        limits.append("trend_and_aggregate_may_be_empty")

    if not has_time:
        reasons.append("未识别到时间字段，趋势/同比能力受限")
        limits.append("time_trend")
        limits.append("period_compare")

    if not has_measure:
        reasons.append("未识别到候选度量字段，聚合能力受限")
        limits.append("metric_aggregate")

    if not has_rel:
        limits.append("multi_table_join")
        reasons.append("无可用关系路径，多表关联需澄清或分查")

    # sensitive-only DB still READY but limited
    if limits and (not has_time or not has_measure or empty_all):
        status = ReadinessStatus.DEGRADED
    else:
        status = ReadinessStatus.READY
        reasons = reasons or ["结构可分析"]

    # if blocked conditions none and has tables+cols → at least DEGRADED
    return ReadinessReport(status=status, reasons=reasons, limited_capabilities=limits)


def profile_from_ddl(
    ddl: str,
    *,
    database_id: str,
    seed_sql: str = "",
    connection_meta: Optional[dict] = None,
    version: str = "1",
) -> SemanticCatalog:
    """Build SemanticCatalog from CREATE TABLE DDL (+ optional seed for bounds)."""
    tables, explicit_fks = parse_ddl_tables(ddl or "")
    relations = infer_relations(tables, explicit_fks)
    _parse_seed_dates(seed_sql or "", tables)
    _apply_seed_column_profiles(seed_sql or "", tables)
    measures = _candidate_measures(tables)
    dims = _candidate_dimensions(tables)

    safe_identity: dict[str, Any] = {}
    if connection_meta:
        for k in ("host", "port", "database", "db_name", "user", "charset"):
            if k in connection_meta and connection_meta[k] is not None:
                safe_identity[k] = connection_meta[k]
        # explicitly drop secrets
        for bad in list(safe_identity.keys()):
            if "pass" in bad.lower() or "secret" in bad.lower() or "token" in bad.lower():
                safe_identity.pop(bad, None)

    fp = compute_schema_fingerprint(ddl or "")
    cat = SemanticCatalog(
        database_id=database_id,
        version=version,
        schema_fingerprint=fp,
        tables=tables,
        relations=relations,
        candidate_measures=measures,
        candidate_dimensions=dims,
        identity=safe_identity,
        expires_at=None,
        profiling_policy={
            "read_only": True,
            "per_query_timeout_ms": 5000,
            "max_sample_rows_per_table": 100,
            "raw_samples_persisted": False,
            "sample_strategy_default": "seed_insert_scan",
            "evidence_sources": ["metadata", "seed_distribution"],
        },
    )
    cat.readiness = compute_readiness(cat)
    return cat


def profile_connection_precheck(connection_meta: Optional[dict] = None) -> dict[str, Any]:
    """Lightweight connection precheck result (no password echo)."""
    meta = connection_meta or {}
    ok = bool(meta.get("host") or meta.get("database") or meta.get("dsn_safe"))
    return {
        "ok": ok,
        "engine": meta.get("engine") or "mysql",
        "host": meta.get("host"),
        "database": meta.get("database") or meta.get("db_name"),
        "message": "precheck_ok" if ok else "missing_connection_identity",
    }
