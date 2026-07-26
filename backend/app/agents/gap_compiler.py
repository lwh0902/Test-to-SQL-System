"""GapCompiler: evidence_gaps → executable Query task_spec.

Architecture (dev_spec):
- Supervisor/orchestration decides *that* a gap-fill query is needed.
- GapCompiler compiles *what* to query (tables / dimensions / metric / question).
- Query Agent only executes the compiled task_spec — it must not guess business intent.

L2 rules first (deterministic, testable). Optional L1 flash can be layered later.
No CoT, no credentials, no raw rows in output.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional


# Dimension keyword → canonical dimension id
_DIMENSION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("channel", ("渠道", "channel", "source", "来源", "投放", "utm")),
    ("category", ("分类", "类目", "category", "品类", "商品类")),
    ("device", ("设备", "device", "终端", "ios", "android", "手机", "pc")),
    ("time", ("趋势", "时间", "对比期", "同比", "环比", "按日", "按周", "按月", "timeline")),
    ("region", ("地区", "区域", "城市", "省份", "region", "city")),
    ("user_segment", ("用户群", "新老客", "会员", "segment", "cohort")),
    ("refund", ("退款", "refund", "退货", "售后")),
    ("product", ("商品", "sku", "product", "spu", "货品")),
)

# Business concept token → preferred physical table name hints (matched against schema)
_TABLE_CONCEPTS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    # concept_key, gap keywords, preferred table name fragments
    ("orders", ("订单", "order", "gmv", "成交", "支付"), ("order", "orders", "ecom_order", "trade")),
    ("refunds", ("退款", "refund", "退货"), ("refund", "refunds", "ecom_refund", "return")),
    ("users", ("用户", "user", "客户", "会员"), ("user", "users", "ecom_user", "customer", "member")),
    ("products", ("商品", "product", "sku", "货品", "类目"), ("product", "products", "ecom_product", "sku", "item")),
    ("traffic", ("流量", "traffic", "访问", "pv", "uv", "点击"), ("traffic", "event", "pageview", "visit", "ecom_traffic")),
    ("scans", ("扫描", "scan", "质检", "成功率"), ("scan", "scans", "scan_record", "quality")),
)

_METRIC_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gmv", ("gmv", "成交额", "销售额", "营收")),
    ("order_count", ("订单量", "订单数", "单量")),
    ("refund_rate", ("退款率", "退货率")),
    ("scan_success_rate", ("扫描成功率", "scan_success", "质检成功率")),
    ("uv", ("uv", "独立访客", "访客数")),
    ("conversion_rate", ("转化率", "下单转化")),
)


@dataclass
class CompiledGapTask:
    """Executable query task compiled from evidence gaps."""

    task_type: str = "gap_fill_query"
    question: str = ""
    metric: Optional[str] = None
    dimensions: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    table_concepts: list[str] = field(default_factory=list)
    evidence_gaps: list[str] = field(default_factory=list)
    time_hint: Optional[str] = None
    priority: str = "normal"  # normal | high
    compiler: str = "gap_compiler.l2.v1"
    notes: list[str] = field(default_factory=list)

    def to_task_spec(self) -> dict[str, Any]:
        """Shape consumed by Query / orchestration fingerprinting."""
        return {
            "task_type": self.task_type,
            "metric": self.metric or "",
            "table": self.tables[0] if self.tables else "",
            "tables": list(self.tables),
            "dimensions": list(self.dimensions),
            "table_concepts": list(self.table_concepts),
            "question": self.question,
            "evidence_gaps": list(self.evidence_gaps)[:8],
            "time_hint": self.time_hint or "",
            "suggested_dimensions": list(self.dimensions),  # backward compatible
            "suggested_tables": list(self.tables) or list(self.table_concepts),
            "compiler": self.compiler,
            "notes": list(self.notes)[:6],
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def _uniq(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for x in items:
        s = str(x).strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _schema_table_names(schema_payload: dict | None) -> list[str]:
    names: list[str] = []
    if not isinstance(schema_payload, dict):
        return names
    for t in schema_payload.get("tables") or []:
        if isinstance(t, dict) and t.get("name"):
            names.append(str(t["name"]))
        elif isinstance(t, str):
            names.append(t)
    # also accept flat list under table_names
    for t in schema_payload.get("table_names") or []:
        if t:
            names.append(str(t))
    return _uniq(names)


def _match_tables(concepts: list[str], schema_names: list[str]) -> list[str]:
    """Map concept keys to real schema table names when possible."""
    if not schema_names:
        return []
    matched: list[str] = []
    lower_map = {n.lower(): n for n in schema_names}
    for concept in concepts:
        frags: tuple[str, ...] = ()
        for key, _kw, fragments in _TABLE_CONCEPTS:
            if key == concept:
                frags = fragments
                break
        if not frags:
            frags = (concept,)
        for name_l, name in lower_map.items():
            if any(f in name_l for f in frags):
                if name not in matched:
                    matched.append(name)
                break
    return matched


def detect_dimensions(gaps: Iterable[str]) -> list[str]:
    dims: list[str] = []
    for g in gaps:
        gl = str(g)
        gl_l = gl.lower()
        for dim, keys in _DIMENSION_RULES:
            if any(k.lower() in gl_l or k in gl for k in keys):
                if dim not in dims:
                    dims.append(dim)
    return dims


def detect_table_concepts(gaps: Iterable[str]) -> list[str]:
    concepts: list[str] = []
    for g in gaps:
        gl = str(g)
        gl_l = gl.lower()
        for key, keys, _frags in _TABLE_CONCEPTS:
            if any(k.lower() in gl_l or k in gl for k in keys):
                if key not in concepts:
                    concepts.append(key)
    return concepts


def detect_metric(gaps: Iterable[str], base_question: str = "") -> Optional[str]:
    blob = " ".join([str(g) for g in gaps] + [base_question or ""])
    blob_l = blob.lower()
    for metric, keys in _METRIC_HINTS:
        if any(k.lower() in blob_l or k in blob for k in keys):
            return metric
    return None


def detect_time_hint(gaps: Iterable[str], base_question: str = "") -> Optional[str]:
    blob = " ".join([str(g) for g in gaps] + [base_question or ""])
    patterns = [
        (r"近\s*(\d+)\s*天", lambda m: f"last_{m.group(1)}_days"),
        (r"最近\s*(\d+)\s*天", lambda m: f"last_{m.group(1)}_days"),
        (r"last\s*(\d+)\s*days?", lambda m: f"last_{m.group(1)}_days"),
        (r"同比", lambda _m: "yoy"),
        (r"环比", lambda _m: "mom"),
        (r"对比期", lambda _m: "compare_period"),
    ]
    for pat, fn in patterns:
        m = re.search(pat, blob, flags=re.I)
        if m:
            return fn(m)
    if any(k in blob for k in ("趋势", "按日", "按周", "timeline")):
        return "trend"
    return None


def build_gap_question(
    *,
    base_question: str,
    gaps: list[str],
    dimensions: list[str],
    tables: list[str],
    table_concepts: list[str],
    metric: Optional[str],
    time_hint: Optional[str],
) -> str:
    """Fully rewritten natural-language question for Query (no bare pronouns)."""
    parts: list[str] = []
    base = (base_question or "").strip()
    if base:
        # strip prior gap suffixes to avoid infinite stacking
        base = re.split(r"\n【补充查询", base, maxsplit=1)[0].strip()
        parts.append(base)

    focus: list[str] = []
    if metric:
        focus.append(f"指标侧重 {metric}")
    if dimensions:
        focus.append("按 " + "、".join(dimensions) + " 维度拆解")
    if tables:
        focus.append("优先使用表 " + "、".join(tables[:4]))
    elif table_concepts:
        focus.append("关联数据域 " + "、".join(table_concepts[:4]))
    if time_hint:
        focus.append(f"时间口径 {time_hint}")

    gap_txt = "；".join(gaps[:5]) if gaps else "证据不足"
    suffix = (
        f"【补充查询·证据缺口】{gap_txt}。"
        + ("请" + "，".join(focus) + "。" if focus else "请补充相关表/维度数据。")
        + "返回可对比的聚合结果，勿重复完全相同的总览查询。"
    )
    parts.append(suffix)
    return "\n".join(parts)


def compile_gap_task(
    gaps: list[str] | None,
    *,
    base_question: str = "",
    schema_payload: dict | None = None,
    prior_query: dict | None = None,
    task_type: str = "gap_fill_query",
) -> CompiledGapTask:
    """Compile evidence gaps into an executable Query task_spec."""
    clean_gaps = _uniq(str(g)[:200] for g in (gaps or []) if str(g).strip())[:8]
    dimensions = detect_dimensions(clean_gaps)
    concepts = detect_table_concepts(clean_gaps)
    metric = detect_metric(clean_gaps, base_question)
    time_hint = detect_time_hint(clean_gaps, base_question)

    # Infer from prior query columns if gaps are vague
    notes: list[str] = []
    pq = prior_query if isinstance(prior_query, dict) else {}
    cols = [str(c).lower() for c in (pq.get("columns") or [])]
    if not dimensions and cols:
        if any("channel" in c or "source" in c for c in cols):
            notes.append("prior_query already has channel-like column; seek finer grain")
        if not any(x in "".join(cols) for x in ("channel", "category", "device", "region")):
            # total-only result → push breakdown
            if "channel" not in dimensions:
                dimensions.append("channel")
            notes.append("inferred channel breakdown from total-only prior query")

    if not concepts and any(k in (base_question or "") for k in ("订单", "GMV", "gmv", "销售")):
        concepts.append("orders")
    if "refund" in dimensions and "refunds" not in concepts:
        concepts.append("refunds")
    if "product" in dimensions and "products" not in concepts:
        concepts.append("products")

    schema_names = _schema_table_names(schema_payload)
    tables = _match_tables(concepts, schema_names)

    # If schema empty, keep concept tokens as soft table hints
    soft_tables = tables if tables else list(concepts)

    question = build_gap_question(
        base_question=base_question,
        gaps=clean_gaps,
        dimensions=dimensions,
        tables=tables,
        table_concepts=concepts,
        metric=metric,
        time_hint=time_hint,
    )

    priority = "high" if len(clean_gaps) >= 2 or ("退款" in "".join(clean_gaps)) else "normal"

    return CompiledGapTask(
        task_type=task_type,
        question=question,
        metric=metric,
        dimensions=dimensions,
        tables=soft_tables,
        table_concepts=concepts,
        evidence_gaps=clean_gaps,
        time_hint=time_hint,
        priority=priority,
        notes=notes,
    )


def compile_gap_task_spec(
    gaps: list[str] | None,
    *,
    base_question: str = "",
    schema_payload: dict | None = None,
    prior_query: dict | None = None,
    task_type: str = "gap_fill_query",
) -> dict[str, Any]:
    """Convenience: return plain dict task_spec."""
    return compile_gap_task(
        gaps,
        base_question=base_question,
        schema_payload=schema_payload,
        prior_query=prior_query,
        task_type=task_type,
    ).to_task_spec()
