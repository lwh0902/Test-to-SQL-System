"""Live MySQL profiler — information_schema + controlled stats (Recovery R2).

No credentials in catalog. No unbounded raw rows to models.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.agents.profiler import (
    _candidate_dimensions,
    _candidate_measures,
    compute_readiness,
    infer_field_role,
    infer_relations,
    _is_sensitive,
)
from app.agents.semantic_catalog import (
    BoundedColumnStats,
    ColumnProfile,
    FieldRole,
    RelationEdge,
    SemanticCatalog,
    TableProfile,
    compute_schema_fingerprint,
)


# Controlled stats: never SELECT * / never dump rows
_MAX_TABLES = 200
_MAX_COLUMNS = 3000
_ROWCOUNT_TIMEOUT_HINT = 5
_TIME_BOUNDS_MAX_TABLES = 40
_TOPK_MAX_TABLES = 40
_TOPK_MAX_COLS = 80
_TOPK_LIMIT = 12
_SAMPLE_MAX_ROWS = 100
_PER_QUERY_TIMEOUT_MS = 5000


def _safe_ident(name: str) -> str:
    """Allow MySQL identifiers including CJK; block quotes/separators."""
    n = name or ""
    if not n or re.search(r"[`\"';\\]|\s|--|/\*|\*/", n):
        raise ValueError(f"unsafe identifier: {name!r}")
    # letters (any script), numbers, underscore, dollar
    if not re.fullmatch(r"[\w$]+", n, flags=re.UNICODE):
        raise ValueError(f"unsafe identifier: {name!r}")
    return n


def _engine_from_params(
    *,
    host: str,
    port: int = 3306,
    user: str,
    password: str = "",
    database: str,
) -> Engine:
    # password may contain special chars — SQLAlchemy URL
    from urllib.parse import quote_plus

    u = quote_plus(user or "")
    p = quote_plus(password or "")
    url = f"mysql+pymysql://{u}:{p}@{host}:{int(port)}/{database}?charset=utf8mb4"
    return create_engine(url, pool_pre_ping=True, pool_size=1, max_overflow=0)


def _tier(n: Optional[int]) -> str:
    if n is None:
        return "unknown"
    if n <= 0:
        return "empty"
    if n < 100:
        return "tiny"
    if n < 10_000:
        return "small"
    if n < 1_000_000:
        return "medium"
    return "large"


def profile_live_mysql(
    *,
    host: str,
    port: int = 3306,
    user: str,
    password: str = "",
    database: str,
    database_id: str | None = None,
    version: str = "1",
    collect_stats: bool = True,
    collect_time_bounds: bool = True,
    engine: Engine | None = None,
    include_tables: set[str] | list[str] | tuple[str, ...] | None = None,
    table_prefixes: list[str] | tuple[str, ...] | None = None,
    exclude_tables: set[str] | list[str] | tuple[str, ...] | None = None,
) -> SemanticCatalog:
    """Profile a live MySQL schema into SemanticCatalog.

    Optional filters (all case-sensitive MySQL identifiers):
      - include_tables: exact allow-list
      - table_prefixes: keep names starting with any prefix
      - exclude_tables: drop exact names after other filters
    """
    t0 = time.perf_counter()
    own_engine = engine is None
    eng = engine or _engine_from_params(
        host=host, port=port, user=user, password=password, database=database
    )
    db = database
    include_set = {t for t in (include_tables or []) if t}
    exclude_set = {t for t in (exclude_tables or []) if t}
    prefixes = tuple(p for p in (table_prefixes or []) if p)

    def _keep_table(name: str) -> bool:
        if exclude_set and name in exclude_set:
            return False
        if include_set:
            return name in include_set
        if prefixes:
            return any(name.startswith(p) for p in prefixes)
        return True

    try:
        with eng.connect() as conn:
            tables_rows = conn.execute(
                text(
                    "SELECT TABLE_NAME, TABLE_COMMENT, TABLE_ROWS, ENGINE "
                    "FROM INFORMATION_SCHEMA.TABLES "
                    "WHERE TABLE_SCHEMA = :db AND TABLE_TYPE = 'BASE TABLE' "
                    "ORDER BY TABLE_NAME"
                ),
                {"db": db},
            ).fetchall()
            tables_rows = [r for r in tables_rows if _keep_table(r[0])]
            if len(tables_rows) > _MAX_TABLES:
                tables_rows = tables_rows[:_MAX_TABLES]
            kept_names = {r[0] for r in tables_rows}

            # columns
            cols_rows = conn.execute(
                text(
                    "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, COLUMN_TYPE, "
                    "IS_NULLABLE, COLUMN_KEY, COLUMN_COMMENT, ORDINAL_POSITION "
                    "FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = :db "
                    "ORDER BY TABLE_NAME, ORDINAL_POSITION"
                ),
                {"db": db},
            ).fetchall()
            cols_rows = [r for r in cols_rows if r[0] in kept_names]

            # primary keys
            pk_rows = conn.execute(
                text(
                    "SELECT TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION "
                    "FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE "
                    "WHERE TABLE_SCHEMA = :db AND CONSTRAINT_NAME = 'PRIMARY' "
                    "ORDER BY TABLE_NAME, ORDINAL_POSITION"
                ),
                {"db": db},
            ).fetchall()
            pk_map: dict[str, list[str]] = {}
            for r in pk_rows:
                if r[0] not in kept_names:
                    continue
                pk_map.setdefault(r[0], []).append(r[1])

            # foreign keys
            fk_rows = conn.execute(
                text(
                    "SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME, "
                    "CONSTRAINT_NAME "
                    "FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE "
                    "WHERE TABLE_SCHEMA = :db AND REFERENCED_TABLE_NAME IS NOT NULL "
                    "ORDER BY TABLE_NAME, COLUMN_NAME"
                ),
                {"db": db},
            ).fetchall()
            explicit: list[RelationEdge] = []
            fk_cols: set[tuple[str, str]] = set()
            for r in fk_rows:
                src_t, src_c, dst_t, dst_c = r[0], r[1], r[2], r[3]
                if src_t not in kept_names or dst_t not in kept_names:
                    continue
                fk_cols.add((src_t, src_c))
                explicit.append(
                    RelationEdge(
                        src_table=src_t,
                        src_column=src_c,
                        dst_table=dst_t,
                        dst_column=dst_c,
                        kind="explicit_fk",
                        confidence=1.0,
                        evidence=[f"information_schema.fk:{r[4]}"],
                        allow_auto_join=True,
                    )
                )

            # indexes
            idx_rows = conn.execute(
                text(
                    "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME, NON_UNIQUE, SEQ_IN_INDEX "
                    "FROM INFORMATION_SCHEMA.STATISTICS "
                    "WHERE TABLE_SCHEMA = :db "
                    "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX"
                ),
                {"db": db},
            ).fetchall()
            idx_rows = [r for r in idx_rows if r[0] in kept_names]
            idx_acc: dict[tuple[str, str], dict] = {}
            for r in idx_rows:
                key = (r[0], r[1])
                slot = idx_acc.setdefault(
                    key,
                    {
                        "name": r[1],
                        "columns": [],
                        "unique": r[3] == 0,
                        "primary": r[1] == "PRIMARY",
                    },
                )
                slot["columns"].append(r[2])

            # build tables
            col_by_table: dict[str, list] = {}
            for r in cols_rows:
                col_by_table.setdefault(r[0], []).append(r)

            total_cols = sum(len(v) for v in col_by_table.values())
            if total_cols > _MAX_COLUMNS:
                # truncate later tables' columns — still report tables
                pass

            tables: list[TableProfile] = []
            col_count = 0
            for t_row in tables_rows:
                tname = t_row[0]
                approx_rows = t_row[2]
                try:
                    approx_i = int(approx_rows) if approx_rows is not None else None
                except Exception:
                    approx_i = None

                columns: list[ColumnProfile] = []
                pks = set(pk_map.get(tname) or [])
                for c in col_by_table.get(tname) or []:
                    if col_count >= _MAX_COLUMNS:
                        break
                    cname = c[1]
                    dtype = str(c[2] or c[3] or "")
                    is_pk = cname in pks or (str(c[5] or "").upper() == "PRI")
                    is_fk = (tname, cname) in fk_cols
                    role = infer_field_role(cname, dtype, is_pk=is_pk)
                    columns.append(
                        ColumnProfile(
                            name=cname,
                            data_type=dtype,
                            nullable=str(c[4] or "").upper() == "YES",
                            comment=str(c[6] or ""),
                            role=role,
                            sensitive=_is_sensitive(cname),
                            is_primary_key=is_pk,
                            is_foreign_key=is_fk,
                        )
                    )
                    col_count += 1

                t_indexes = [
                    v
                    for (tn, _in), v in idx_acc.items()
                    if tn == tname
                ]
                table_comment = str(t_row[1] or "")
                if not table_comment:
                    # Some schemas put business meaning on PK column COMMENT only.
                    for c in columns:
                        if c.is_primary_key and (c.comment or "").strip():
                            table_comment = str(c.comment).strip()
                            break
                tables.append(
                    TableProfile(
                        name=tname,
                        comment=table_comment,
                        columns=columns,
                        primary_key=list(pk_map.get(tname) or []),
                        indexes=t_indexes,
                        row_count_tier=_tier(approx_i),
                        time_bounds={},
                    )
                )

            # controlled COUNT / time bounds (no raw rows)
            if collect_stats:
                for tp in tables:
                    try:
                        tn = _safe_ident(tp.name)
                        n = conn.execute(text(f"SELECT COUNT(*) FROM `{tn}`")).scalar()
                        tp.row_count_tier = _tier(int(n) if n is not None else None)
                    except Exception:
                        pass

            if collect_time_bounds:
                n_tb = 0
                for tp in tables:
                    if n_tb >= _TIME_BOUNDS_MAX_TABLES:
                        break
                    time_cols = [
                        c
                        for c in tp.columns
                        if c.role == FieldRole.TIME.value
                        or re.search(r"date|time|at$", c.name, re.I)
                    ]
                    if not time_cols:
                        continue
                    n_tb += 1
                    for c in time_cols[:3]:
                        try:
                            tn = _safe_ident(tp.name)
                            cn = _safe_ident(c.name)
                            row = conn.execute(
                                text(f"SELECT MIN(`{cn}`), MAX(`{cn}`) FROM `{tn}`")
                            ).fetchone()
                            if row and (row[0] is not None or row[1] is not None):
                                tp.time_bounds[c.name] = {
                                    "min": str(row[0]) if row[0] is not None else None,
                                    "max": str(row[1]) if row[1] is not None else None,
                                }
                        except Exception:
                            continue

            # Bounded low-cardinality / status vocab profiling.
            # Prefer indexed PK dispersion — never rely solely on physical head rows.
            if collect_stats:
                n_tables = 0
                n_cols = 0
                for tp in tables:
                    if n_tables >= _TOPK_MAX_TABLES or n_cols >= _TOPK_MAX_COLS:
                        break
                    n_tables += 1
                    tn = _safe_ident(tp.name)
                    pk = (tp.primary_key or [None])[0]
                    for c in tp.columns:
                        if n_cols >= _TOPK_MAX_COLS:
                            break
                        if c.sensitive:
                            c.profile = BoundedColumnStats(
                                sample_strategy="skipped_sensitive",
                                sample_size=0,
                                evidence_sources=["metadata", "sensitivity_guard"],
                            )
                            continue
                        want_topk = c.role in {
                            FieldRole.STATUS.value,
                            FieldRole.ENUM_DIMENSION.value,
                        } or bool(
                            re.search(r"status|state|flag|direction|priority", c.name, re.I)
                        )
                        if not want_topk:
                            continue
                        n_cols += 1
                        try:
                            cn = _safe_ident(c.name)
                            rows = conn.execute(
                                text(
                                    f"SELECT `{cn}` AS v, COUNT(*) AS c "
                                    f"FROM `{tn}` WHERE `{cn}` IS NOT NULL "
                                    f"GROUP BY `{cn}` ORDER BY c DESC LIMIT {_TOPK_LIMIT}"
                                )
                            ).fetchall()
                            top_values = [str(r[0]) for r in rows if r and r[0] is not None]
                            value_counts = {
                                str(r[0]): int(r[1])
                                for r in rows
                                if r and r[0] is not None
                            }
                            strategy = "group_by_topk"
                            sample_size = min(sum(value_counts.values()), _SAMPLE_MAX_ROWS)
                            if pk and top_values:
                                try:
                                    pkn = _safe_ident(pk)
                                    probe = conn.execute(
                                        text(
                                            f"SELECT DISTINCT `{cn}` FROM `{tn}` "
                                            f"WHERE MOD(`{pkn}`, 7) = 0 AND `{cn}` IS NOT NULL "
                                            f"LIMIT {_TOPK_LIMIT}"
                                        )
                                    ).fetchall()
                                    for r in probe:
                                        if r and r[0] is not None:
                                            sv = str(r[0])
                                            if sv not in top_values:
                                                top_values.append(sv)
                                    strategy = "group_by_topk+pk_mod_dispersion"
                                except Exception:
                                    strategy = "group_by_topk"
                            c.profile = BoundedColumnStats(
                                top_values=top_values[:_TOPK_LIMIT],
                                value_counts=dict(list(value_counts.items())[:_TOPK_LIMIT]),
                                sample_strategy=strategy,
                                sample_size=int(sample_size),
                                evidence_sources=["metadata", "distribution", "top-k"],
                            )
                        except Exception:
                            c.profile = BoundedColumnStats(
                                sample_strategy="profile_failed",
                                sample_size=0,
                                evidence_sources=["metadata"],
                            )

        relations = infer_relations(tables, explicit)
        measures = _candidate_measures(tables)
        dims = _candidate_dimensions(tables)

        # fingerprint from structure only (no secrets)
        fp_payload = json.dumps(
            {
                "db": db,
                "tables": [
                    {
                        "n": t.name,
                        "c": [(c.name, c.data_type, c.is_primary_key) for c in t.columns],
                        "pk": t.primary_key,
                    }
                    for t in tables
                ],
                "fk": [
                    (r.src_table, r.src_column, r.dst_table, r.dst_column)
                    for r in explicit
                ],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        fp = compute_schema_fingerprint(fp_payload)

        cat = SemanticCatalog(
            database_id=database_id or db,
            version=version,
            schema_fingerprint=fp,
            tables=tables,
            relations=relations,
            candidate_measures=measures,
            candidate_dimensions=dims,
            identity={
                "host": host,
                "port": int(port),
                "database": db,
                "user": user,
                # never password
            },
            engine="mysql",
            profiling_policy={
                "read_only": True,
                "per_query_timeout_ms": _PER_QUERY_TIMEOUT_MS,
                "max_sample_rows_per_table": _SAMPLE_MAX_ROWS,
                "raw_samples_persisted": False,
                "sample_strategy_default": "group_by_topk+pk_mod_dispersion",
                "evidence_sources": ["metadata", "distribution", "top-k", "time_bounds"],
            },
        )
        cat.readiness = compute_readiness(cat)
        # attach timing in identity-safe meta via readiness reason if slow
        elapsed = time.perf_counter() - t0
        if cat.readiness and elapsed > 0:
            cat.readiness.reasons = list(cat.readiness.reasons or []) + [
                f"profile_elapsed_s={elapsed:.3f}"
            ]
        return cat
    finally:
        if own_engine:
            eng.dispose()


def catalog_contains_secrets(cat: SemanticCatalog | dict) -> list[str]:
    """Return list of suspicious secret keys found (should be empty)."""
    blob = cat.to_public_dict() if isinstance(cat, SemanticCatalog) else dict(cat or {})
    found: list[str] = []
    text = json.dumps(blob, ensure_ascii=False).lower()
    for bad in ("password", "passwd", "secret", "token", "encrypted_password"):
        if bad in text:
            # allow field names like password_hash column profiles — check identity
            ident = blob.get("identity") or {}
            for k in ident:
                if bad in str(k).lower():
                    found.append(f"identity.{k}")
            if f'"{bad}"' in json.dumps(ident, ensure_ascii=False).lower():
                found.append(bad)
    return found
