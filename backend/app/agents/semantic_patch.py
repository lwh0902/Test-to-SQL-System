"""Deterministic semantic patches for travel_b2b follow-up turns."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.agents.semantic_model import SemanticModel
from app.agents.semantic_parser import SemanticFilterRequest, SemanticQueryRequest, SemanticTimeRange
from app.agents.semantic_snapshot import SemanticSnapshot


PATCH_ACTIONS = frozenset({
    "add_dimensions",
    "replace_dimensions",
    "add_filters",
    "replace_filters",
    "remove_filters",
    "replace_time",
    "replace_grain",
    "replace_metrics",
    "set_limit",
    "reset",
    "clarify",
    "noop",
})


@dataclass
class SemanticPatch:
    action: str
    dimensions: list[str] = field(default_factory=list)
    filters: list[SemanticFilterRequest] = field(default_factory=list)
    remove_fields: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    time_range: SemanticTimeRange | None = None
    time_grain: str | None = None
    limit: int | None = None
    unresolved_slots: list[str] = field(default_factory=list)
    source: str = "parser"

    @property
    def replaces_metrics(self) -> bool:
        return self.action in {"replace_metrics", "reset"}

    @property
    def replaces_time(self) -> bool:
        return self.action in {"replace_time", "reset"}

    @property
    def replaces_dimensions(self) -> bool:
        return self.action in {"replace_dimensions", "reset"}

    @property
    def is_reset(self) -> bool:
        return self.action == "reset"


_FOLLOWUP_HINT = re.compile(
    r"按.{0,12}拆|拆开|拆一下|只看|换成|改成|改回|不要|去掉|再加上|再按|再看|"
    r"时间改成|不要.+限制|同样|继续|下钻|呢\s*$",
    re.I,
)


def looks_like_followup_utterance(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    if _FOLLOWUP_HINT.search(q):
        return True
    return len(q) <= 16 and not re.search(r"20\d{2}", q)


def looks_like_complete_new_query(question: str, model: SemanticModel) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    has_time = bool(re.search(r"20\d{2}\s*年|\d{1,2}\s*月|近\s*\d+\s*天", q))
    has_metric = False
    for metric in model.metrics:
        names = (metric.id, metric.description, *metric.aliases)
        if any(name and str(name) in q for name in names):
            has_metric = True
            break
        if metric.id == "gmv" and "GMV" in q.upper():
            has_metric = True
            break
        if metric.id == "order_count" and ("订单数" in q or "订单量" in q):
            has_metric = True
            break
        if metric.id == "cancel_rate" and ("取消率" in q or "退款率" in q):
            has_metric = True
            break
    has_slice = bool(re.search(r"按|只看|趋势", q))
    return has_time and has_metric and has_slice and not re.match(r"^(按|只看|改成|换成|不要)", q)


def followup_required(
    *,
    supervisor_intent: str,
    question: str,
    snapshot: SemanticSnapshot | None,
    model: SemanticModel,
) -> bool:
    if snapshot is None or not snapshot.metrics:
        return False
    if looks_like_complete_new_query(question, model):
        return False
    if supervisor_intent == "follow_up":
        return True
    return looks_like_followup_utterance(question)


def _metric_by_alias(model: SemanticModel, text: str) -> str | None:
    q = text or ""
    if re.search(r"取消率|退款率", q):
        return "cancel_rate" if model.metric("cancel_rate") else None
    if re.search(r"订单数|订单量", q) and "取消" not in q:
        return "order_count" if model.metric("order_count") else None
    if re.search(r"GMV|成交额|交易额", q, re.I):
        return "gmv" if model.metric("gmv") else None
    return None


def _dimension_from_text(model: SemanticModel, text: str) -> str | None:
    q = text or ""
    mapping = [
        (r"渠道", "channel_name"),
        (r"城市", "city"),
        (r"品类|产品类型", "product_type"),
        (r"供应商", "supplier_name"),
        (r"产品(?!类型)", "product_name"),
        (r"国家", "country"),
        (r"地区|区域", "region_group"),
        (r"车型|档次", "car_tier"),
        (r"状态", "status"),
    ]
    for pattern, dim_id in mapping:
        if re.search(pattern, q) and model.dimension(dim_id):
            return dim_id
    return None


def _parse_time_range(text: str, *, today: date | None = None) -> SemanticTimeRange | None:
    q = text or ""
    current = today or date.today()
    iso_dates = re.findall(r"(20\d{2}-\d{2}-\d{2})", q)
    if len(iso_dates) >= 2:
        return SemanticTimeRange(iso_dates[0], iso_dates[-1])
    if len(iso_dates) == 1:
        return SemanticTimeRange(iso_dates[0], iso_dates[0])
    rel = re.search(r"(?:最近|近|过去)\s*(\d{1,3})\s*(?:天|日)", q)
    if rel:
        days = int(rel.group(1))
        if 1 <= days <= 366:
            start = current - timedelta(days=days - 1)
            return SemanticTimeRange(start.isoformat(), current.isoformat())
    return _parse_month_range(q)


def _product_name_from_text(text: str) -> str | None:
    q = text or ""
    matched = re.search(r"(.+?)的(?:取消率|退款率|GMV|成交额|交易额|订单数|订单量)", q, re.I)
    if not matched:
        return None
    name = matched.group(1).strip(" ，,。")
    name = re.sub(r"时间范围为\d{4}-\d{2}-\d{2}至\d{4}-\d{2}-\d{2}", "", name)
    name = re.sub(r"(?:20\d{2}\s*年\s*)?\d{1,2}\s*月", "", name)
    name = re.sub(r"(?:最近|近|过去)\s*\d+\s*(?:天|日)", "", name)
    name = name.strip(" ，,的")
    if name and 4 <= len(name) <= 40 and "按" not in name:
        return name
    return None


def _parse_month_range(text: str) -> SemanticTimeRange | None:
    span = re.search(
        r"(20\d{2})\s*年\s*(\d{1,2})\s*(?:到|至|[-~—])\s*(\d{1,2})\s*月",
        text or "",
    )
    if span:
        year, m1, m2 = map(int, span.groups())
        if 1 <= m1 <= 12 and 1 <= m2 <= 12:
            start, end = (m1, m2) if m1 <= m2 else (m2, m1)
            return SemanticTimeRange(
                f"{year:04d}-{start:02d}-01",
                f"{year:04d}-{end:02d}-{calendar.monthrange(year, end)[1]:02d}",
            )
    months = re.findall(r"(20\d{2})[-/.年](\d{1,2})(?:月)?", text or "")
    if len(months) >= 2:
        y1, m1 = map(int, months[0])
        y2, m2 = map(int, months[-1])
        return SemanticTimeRange(
            f"{y1:04d}-{m1:02d}-01",
            f"{y2:04d}-{m2:02d}-{calendar.monthrange(y2, m2)[1]:02d}",
        )
    if len(months) == 1:
        year, month = map(int, months[0])
        return SemanticTimeRange(
            f"{year:04d}-{month:02d}-01",
            f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}",
        )
    only_month = re.search(r"(?:改成|换成)?\s*(\d{1,2})\s*月", text or "")
    year_hint = re.search(r"(20\d{2})", text or "")
    if only_month and year_hint:
        year = int(year_hint.group(1))
        month = int(only_month.group(1))
        if 1 <= month <= 12:
            return SemanticTimeRange(
                f"{year:04d}-{month:02d}-01",
                f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}",
            )
    return None


def parse_deterministic_new_query(question: str, model: SemanticModel) -> SemanticQueryRequest | None:
    q = (question or "").strip()
    if not q:
        return None
    time_range = _parse_time_range(q)
    metric_id = _metric_by_alias(model, q)
    if time_range is None or not metric_id:
        return None
    if not re.search(r"趋势|按|只看|是多少|有多少|订单数|取消率|GMV", q, re.I):
        return None
    entity = model.entity("order")
    if entity is None and model.entities:
        entity = model.entities[0]
    if entity is None:
        return None
    dimensions = []
    dim = _dimension_from_text(model, q)
    if dim and re.search(r"按", q):
        dimensions.append(dim)
    filters = []
    if re.search(r"只看酒店|酒店订单|酒店GMV", q, re.I) and "不要" not in q and model.dimension("product_type"):
        filters.append(SemanticFilterRequest("product_type", "=", "酒店"))
    elif re.search(r"只看用车|用车订单|用车GMV", q, re.I) and "不要" not in q and model.dimension("product_type"):
        filters.append(SemanticFilterRequest("product_type", "=", "用车"))
    elif re.search(r"只看门票|门票订单|门票GMV", q, re.I) and "不要" not in q and model.dimension("product_type"):
        filters.append(SemanticFilterRequest("product_type", "=", "门票"))
    product_name = _product_name_from_text(q)
    if product_name and model.dimension("product_name") and not any(item.field == "product_name" for item in filters):
        filters.append(SemanticFilterRequest("product_name", "=", product_name))
    grain = None
    if "趋势" in q:
        grain = "month"
        try:
            start = date.fromisoformat(time_range.start[:10])
            end = date.fromisoformat(time_range.end[:10])
            grain = "day" if (end - start).days <= 21 else "month"
        except ValueError:
            grain = "month"
    return SemanticQueryRequest(
        operation="new_query",
        entity=entity.id,
        metrics=[metric_id],
        dimensions=dimensions,
        filters=filters,
        time_range=time_range,
        time_grain=grain,
    )


def parse_deterministic_patch(question: str, model: SemanticModel) -> SemanticPatch | None:
    q = (question or "").strip()
    if not q:
        return None
    if re.search(r"再拆一下|拆一下$|拆开$", q) and _dimension_from_text(model, q) is None:
        return SemanticPatch(action="clarify", unresolved_slots=["dimension"], source="deterministic")
    if re.search(r"不要(?:酒店|用车|门票)?限制|不要产品限制|去掉(?:酒店|用车|门票|产品)(?:限制|筛选)?", q):
        fields = []
        if "产品" in q:
            fields.append("product_name")
        if "酒店" in q or "用车" in q or "门票" in q or "限制" in q:
            fields.append("product_type")
        if not fields:
            fields = ["product_type", "product_name"]
        return SemanticPatch(action="remove_filters", remove_fields=fields, source="deterministic")
    metric_id = _metric_by_alias(model, q)
    if metric_id and re.search(r"改成|换成|改回|再看", q) and not re.search(r"按", q):
        return SemanticPatch(action="replace_metrics", metrics=[metric_id], source="deterministic")
    time_range = _parse_time_range(q)
    if time_range and re.search(r"时间|改成20|换成20", q):
        return SemanticPatch(action="replace_time", time_range=time_range, source="deterministic")
    dim = _dimension_from_text(model, q)
    if dim and re.search(r"换成按|改成按|换城市|换成城市", q):
        return SemanticPatch(action="replace_dimensions", dimensions=[dim], source="deterministic")
    if dim and re.search(r"按|再按|拆", q):
        return SemanticPatch(action="add_dimensions", dimensions=[dim], source="deterministic")
    if re.search(r"只看酒店", q) and model.dimension("product_type"):
        return SemanticPatch(
            action="add_filters",
            filters=[SemanticFilterRequest("product_type", "=", "酒店")],
            source="deterministic",
        )
    if re.search(r"只看用车", q) and model.dimension("product_type"):
        return SemanticPatch(
            action="add_filters",
            filters=[SemanticFilterRequest("product_type", "=", "用车")],
            source="deterministic",
        )
    if re.search(r"只看门票", q) and model.dimension("product_type"):
        return SemanticPatch(
            action="add_filters",
            filters=[SemanticFilterRequest("product_type", "=", "门票")],
            source="deterministic",
        )
    if re.search(r"只看上海$", q) and model.dimension("city"):
        return SemanticPatch(
            action="add_filters",
            filters=[SemanticFilterRequest("city", "=", "上海")],
            source="deterministic",
        )
    only_look = re.match(r"^只看(.+?)(?:了|呢|吧)?$", q)
    if only_look:
        name = only_look.group(1).strip()
        if name and model.dimension("channel_name") and ("门店" in name or "渠道" in name):
            return SemanticPatch(
                action="add_filters",
                filters=[SemanticFilterRequest("channel_name", "=", name)],
                source="deterministic",
            )
        if name and model.dimension("product_name") and ("票" in name or "乐园" in name or "酒店" in name):
            return SemanticPatch(
                action="add_filters",
                filters=[SemanticFilterRequest("product_name", "=", name)],
                source="deterministic",
            )
        if name and model.dimension("city") and len(name) <= 6 and "门店" not in name:
            return SemanticPatch(
                action="add_filters",
                filters=[SemanticFilterRequest("city", "=", name)],
                source="deterministic",
            )
        if name:
            return SemanticPatch(action="add_filters", source="deterministic")
    return None


def patch_from_payload(raw: dict[str, Any], model: SemanticModel) -> SemanticPatch:
    action = str(raw.get("action") or raw.get("operation") or "clarify")
    action = {
        "modify_query": "add_filters",
        "new_query": "reset",
        "add_dimension": "add_dimensions",
        "replace_dimension": "replace_dimensions",
        "add_filter": "add_filters",
        "replace_filter": "replace_filters",
        "remove_filter": "remove_filters",
    }.get(action, action)
    if action not in PATCH_ACTIONS:
        action = "clarify"
    dimensions = [str(x) for x in (raw.get("dimensions") or []) if x]
    metrics = [
        str(x.get("id") or x.get("metric") or x) if isinstance(x, dict) else str(x)
        for x in (raw.get("metrics") or [])
    ]
    metrics = [x for x in metrics if x]
    filters = []
    for item in raw.get("filters") or []:
        if not isinstance(item, dict):
            continue
        field_name = str(item.get("field") or "")
        if not field_name:
            continue
        filters.append(SemanticFilterRequest(field_name, str(item.get("op") or "="), item.get("value")))
    tr = raw.get("time_range")
    time_range = None
    if isinstance(tr, dict) and (tr.get("start") or tr.get("end")):
        time_range = SemanticTimeRange(str(tr.get("start") or ""), str(tr.get("end") or ""), str(tr.get("field") or ""))
    remove_fields = [str(x) for x in (raw.get("remove_fields") or raw.get("fields") or []) if x]
    for dim in dimensions:
        if dim and not model.dimension(dim):
            action = "clarify"
    for metric in metrics:
        if metric and not model.metric(metric):
            action = "clarify"
    return SemanticPatch(
        action=action,
        dimensions=dimensions,
        filters=filters,
        remove_fields=remove_fields,
        metrics=metrics,
        time_range=time_range,
        time_grain=raw.get("time_grain"),
        limit=raw.get("limit"),
        unresolved_slots=[str(x) for x in raw.get("unresolved_slots") or []],
        source="parser",
    )


def apply_patch(snapshot: SemanticSnapshot, patch: SemanticPatch) -> SemanticQueryRequest:
    current = snapshot.to_query()
    if patch.action == "reset":
        query = SemanticQueryRequest(
            operation="new_query",
            entity=current.entity,
            metrics=list(patch.metrics or current.metrics),
            dimensions=list(patch.dimensions),
            filters=list(patch.filters),
            time_range=patch.time_range or current.time_range,
            time_grain=patch.time_grain,
            limit=patch.limit,
        )
        return query
    metrics = list(current.metrics)
    dimensions = list(current.dimensions)
    filters = list(current.filters)
    time_range = current.time_range
    time_grain = current.time_grain
    limit = current.limit
    if patch.action == "replace_metrics" and patch.metrics:
        metrics = list(patch.metrics)
    elif patch.action == "add_dimensions" and patch.dimensions:
        for dim in patch.dimensions:
            if dim not in dimensions:
                dimensions.append(dim)
    elif patch.action == "replace_dimensions":
        dimensions = list(patch.dimensions)
    elif patch.action == "add_filters" and patch.filters:
        incoming = {item.field for item in patch.filters}
        filters = [item for item in filters if item.field not in incoming]
        filters.extend(patch.filters)
    elif patch.action == "replace_filters":
        filters = list(patch.filters)
    elif patch.action == "remove_filters":
        drop = set(patch.remove_fields) | {item.field for item in patch.filters}
        filters = [item for item in filters if item.field not in drop]
    elif patch.action == "replace_time" and patch.time_range is not None:
        field_name = patch.time_range.field or (current.time_range.field if current.time_range else "")
        time_range = SemanticTimeRange(patch.time_range.start, patch.time_range.end, field_name)
    elif patch.action == "replace_grain":
        time_grain = patch.time_grain
    elif patch.action == "set_limit" and patch.limit is not None:
        limit = patch.limit
    elif patch.action == "noop":
        pass
    return SemanticQueryRequest(
        operation="modify_query",
        entity=current.entity,
        metrics=metrics,
        dimensions=dimensions,
        filters=filters,
        time_range=time_range,
        time_grain=time_grain,
        limit=limit,
    )


def inheritance_errors(prior: SemanticSnapshot, merged: SemanticQueryRequest, patch: SemanticPatch) -> list[str]:
    errors: list[str] = []
    if patch.action == "reset" or patch.action == "clarify":
        return errors
    if not patch.replaces_metrics and tuple(merged.metrics) != prior.metrics:
        errors.append("lost_metrics")
    if (merged.entity or "") != prior.entity:
        errors.append("lost_entity")
    if not patch.replaces_time:
        tr = merged.time_range
        if prior.time_start and (tr is None or tr.start != prior.time_start or tr.end != prior.time_end):
            errors.append("lost_time")
    if not patch.replaces_dimensions and patch.action != "add_dimensions":
        if patch.action not in {"replace_filters", "add_filters", "remove_filters", "replace_time", "replace_metrics", "replace_grain", "set_limit", "noop"}:
            pass
        elif patch.action in {"replace_filters", "add_filters", "remove_filters", "replace_time", "replace_metrics", "replace_grain", "set_limit", "noop"}:
            if tuple(merged.dimensions) != prior.dimensions:
                errors.append("lost_dimensions")
    if patch.action == "add_dimensions" and any(dim not in merged.dimensions for dim in prior.dimensions):
        errors.append("lost_dimensions")
    if patch.action in {"add_dimensions", "replace_dimensions", "replace_metrics", "replace_time", "replace_grain", "noop"}:
        prior_fields = {field for field, _op, _value in prior.filters}
        merged_fields = {item.field for item in merged.filters}
        if prior_fields - merged_fields:
            errors.append("lost_filters")
    return errors
