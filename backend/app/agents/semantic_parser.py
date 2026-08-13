"""Model-backed translation from natural language to semantic object IDs."""

from __future__ import annotations

import json
import calendar
import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import date, timedelta
from dataclasses import dataclass, field
from typing import Any, Callable

from app.agents.model_adapter import ModelRequest, ModelResponse, ModelTier, ThinkingLevel, get_model_adapter
from app.agents.semantic_model import SemanticModel


@dataclass(frozen=True)
class SemanticFilterRequest:
    field: str
    op: str = "="
    value: Any = None


@dataclass(frozen=True)
class SemanticTimeRange:
    start: str
    end: str
    field: str = ""


@dataclass(frozen=True)
class SemanticQueryRequest:
    operation: str
    entity: str | None
    metrics: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    filters: list[SemanticFilterRequest] = field(default_factory=list)
    time_range: SemanticTimeRange | None = None
    time_grain: str | None = None
    order_by: list[dict[str, str]] = field(default_factory=list)
    limit: int | None = None
    unresolved_slots: list[str] = field(default_factory=list)

    @classmethod
    def from_payload(cls, raw: dict[str, Any]) -> "SemanticQueryRequest":
        tr = raw.get("time_range")
        op_aliases = {"eq": "=", "neq": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "contains": "LIKE"}
        operation = str(raw.get("operation") or "clarify")
        operation = {"query": "new_query", "new": "new_query", "modify": "modify_query"}.get(operation, operation)
        metrics = [
            str(x.get("id") or x.get("metric") or "") if isinstance(x, dict) else str(x)
            for x in raw.get("metrics") or []
        ]
        return cls(
            operation=operation, entity=raw.get("entity"),
            metrics=[x for x in metrics if x], dimensions=[str(x) for x in raw.get("dimensions") or []],
            filters=[SemanticFilterRequest(str(x.get("field") or ""), op_aliases.get(str(x.get("op") or x.get("operator") or "=").lower(), str(x.get("op") or x.get("operator") or "=")), x.get("value")) for x in raw.get("filters") or [] if isinstance(x, dict)],
            time_range=SemanticTimeRange(str(tr.get("start") or ""), str(tr.get("end") or ""), str(tr.get("field") or "")) if isinstance(tr, dict) else None,
            time_grain=raw.get("time_grain"), order_by=[x for x in raw.get("order_by") or [] if isinstance(x, dict)],
            limit=raw.get("limit"), unresolved_slots=[str(x) for x in raw.get("unresolved_slots") or []],
        )


@dataclass
class SemanticParseResult:
    ok: bool
    query: SemanticQueryRequest | None = None
    error: str = ""
    raw_payload: dict[str, Any] | None = None


class SemanticParser:
    def __init__(
        self,
        *,
        llm_complete: Callable[[ModelRequest], ModelResponse] | None = None,
        timeout_s: float | None = None,
    ):
        self._complete = llm_complete or get_model_adapter().complete
        self._timeout_s = timeout_s if timeout_s is not None else float(
            os.getenv("SEMANTIC_PARSER_TIMEOUT_S", "60")
        )

    def _complete_with_deadline(self, request: ModelRequest, *, timeout_s: float | None = None) -> ModelResponse:
        """Bound a synchronous gateway call so one slow model cannot hang a chat turn."""
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self._complete, request)
        try:
            return future.result(timeout=timeout_s if timeout_s is not None else self._timeout_s)
        except FuturesTimeout:
            future.cancel()
            return ModelResponse(ok=False, error="semantic_parser_timeout")
        finally:
            # A network client may not support cancellation.  Do not wait for its worker
            # here: the request should be allowed to fall back or return a clarification.
            executor.shutdown(wait=False, cancel_futures=True)

    def parse(
        self,
        question: str,
        model: SemanticModel,
        *,
        previous_spec: dict | None = None,
        supervisor_params: dict[str, Any] | None = None,
        original_question: str | None = None,
    ) -> SemanticParseResult:
        system = """你是数据语义解析器。只能从提供的语义模型中选择 entity、metrics、dimensions 和 filters.field；不能猜物理表或字段。只输出 JSON：operation(new_query|modify_query|clarify), entity, metrics, dimensions, filters([{field,op,value}]), time_range({start,end,field}|null), time_grain(day|week|month|quarter|year|null), order_by, limit, unresolved_slots。若缺信息，用 clarify 和 unresolved_slots。"""
        raw_question = original_question or question
        payload = {"question": question, "original_question": raw_question, "semantic_model": model.prompt_summary(), "previous_spec": previous_spec or {}}
        response = self._complete_with_deadline(ModelRequest(system=system, user=json.dumps(payload, ensure_ascii=False), tier=ModelTier.PRO, thinking=ThinkingLevel.LOW, max_tokens=900, expect_json=True, temperature=0.0))
        # A malformed answer can benefit from one strong-model retry.  A network
        # timeout cannot, and retrying it would turn one slow turn into two.
        if response.error != "semantic_parser_timeout" and (not response.ok or not isinstance(response.json_payload, dict)):
            response = self._complete_with_deadline(ModelRequest(system=system, user=json.dumps(payload, ensure_ascii=False), tier=ModelTier.PRO, thinking=ThinkingLevel.LOW, max_tokens=900, expect_json=True, temperature=0.0))
        if not response.ok or not isinstance(response.json_payload, dict):
            response = self._complete_with_deadline(ModelRequest(system=system, user=json.dumps(payload, ensure_ascii=False), tier=ModelTier.FLASH, thinking=ThinkingLevel.NONE, max_tokens=700, expect_json=True, temperature=0.0))
        if not response.ok or not isinstance(response.json_payload, dict):
            # Supervisor parameters are routing hints, not a query plan.  Using
            # incomplete route slots here can silently drop user filters/time.
            return SemanticParseResult(False, error="semantic_parser_unavailable")
        try:
            query = SemanticQueryRequest.from_payload(response.json_payload)
        except Exception:
            return SemanticParseResult(False, error="invalid_semantic_payload", raw_payload=response.json_payload)
        if query.operation not in {"new_query", "modify_query", "clarify"}:
            return SemanticParseResult(False, error="bad_operation", raw_payload=response.json_payload)
        query = self._normalise_query_time(query, raw_question, model)
        if query.operation == "clarify":
            query = self._fill_relative_time(query, raw_question, model)
            if query.operation == "clarify":
                return SemanticParseResult(True, query=query, raw_payload=response.json_payload)
        if not query.entity or not model.entity(query.entity):
            return SemanticParseResult(False, error=f"unknown_entity:{query.entity or ''}", raw_payload=response.json_payload)
        for metric in query.metrics:
            if not model.metric(metric):
                return SemanticParseResult(False, error=f"unknown_metric:{metric}", raw_payload=response.json_payload)
        for dim in query.dimensions:
            if not model.dimension(dim):
                return SemanticParseResult(False, error=f"unknown_dimension:{dim}", raw_payload=response.json_payload)
        allowed_fields = {d.id for d in model.dimensions} | {d.field for d in model.dimensions}
        for item in query.filters:
            if item.field not in allowed_fields:
                return SemanticParseResult(False, error=f"unknown_filter:{item.field}", raw_payload=response.json_payload)
        return SemanticParseResult(True, query=query, raw_payload=response.json_payload)

    @staticmethod
    def _normalise_query_time(
        query: SemanticQueryRequest, question: str, model: SemanticModel
    ) -> SemanticQueryRequest:
        tr = query.time_range
        if tr is None or not query.entity or not model.entity(query.entity):
            return query
        start = (tr.start or "").strip().lower()
        end = (tr.end or "").strip().lower()
        raw_days = None
        if start.startswith(("today-", "now-")) and start.endswith("d") and end in {"today", "now"}:
            raw_days = start.split("-", 1)[1].removesuffix("d")
        elif start.startswith("relative:") and start.endswith("_days_ago") and end in {"relative:now", "relative:today"}:
            raw_days = start.removeprefix("relative:").removesuffix("_days_ago")
        if raw_days is None:
            return query
        try:
            days = int(raw_days)
        except ValueError:
            return query
        if not 1 <= days <= 366:
            return query
        today = date.today()
        return SemanticQueryRequest(
            operation=query.operation,
            entity=query.entity,
            metrics=list(query.metrics),
            dimensions=list(query.dimensions),
            filters=list(query.filters),
            time_range=SemanticTimeRange((today - timedelta(days=days)).isoformat(), today.isoformat(), tr.field or model.entity(query.entity).time_field),
            time_grain=query.time_grain,
            order_by=list(query.order_by),
            limit=query.limit,
            unresolved_slots=list(query.unresolved_slots),
        )

    @staticmethod
    def _fill_relative_time(
        query: SemanticQueryRequest, question: str, model: SemanticModel
    ) -> SemanticQueryRequest:
        """Fill only unambiguous relative windows; never invent metric or entity."""
        if "time_range" not in query.unresolved_slots or query.time_range is not None:
            return query
        match = re.search(r"(?:最近|近|过去)\s*(\d{1,3})\s*(?:天|日)", question or "")
        if not match or not query.entity or not model.entity(query.entity):
            return query
        days = int(match.group(1))
        if not 1 <= days <= 366:
            return query
        end = date.today()
        start = end - timedelta(days=days - 1)
        return SemanticQueryRequest(
            operation="new_query",
            entity=query.entity,
            metrics=list(query.metrics),
            dimensions=list(query.dimensions),
            filters=list(query.filters),
            time_range=SemanticTimeRange(start.isoformat(), end.isoformat(), model.entity(query.entity).time_field),
            time_grain=query.time_grain,
            order_by=list(query.order_by),
            limit=query.limit,
            unresolved_slots=[x for x in query.unresolved_slots if x != "time_range"],
        )

    @staticmethod
    def _from_supervisor_params(params: dict[str, Any], model: SemanticModel) -> SemanticParseResult | None:
        """Validate model-produced Supervisor slots when parser JSON is empty.

        This is deliberately a semantic-model lookup, not a business keyword rule:
        unrecognised slots are rejected and never converted to physical fields.
        """
        if not isinstance(params, dict) or not params:
            return None
        metric_values = params.get("metrics") or [params.get("metric")]
        metric_values = [str(x) for x in metric_values if x]
        metrics = []
        for value in metric_values:
            norm = value.strip().lower()
            hit = next((m for m in model.metrics if norm == m.id.lower() or norm in {a.lower() for a in m.aliases}), None)
            if hit is None:
                return SemanticParseResult(False, error=f"unknown_metric:{value}")
            metrics.append(hit.id)
        if not metrics:
            return None
        entity = model.metric(metrics[0]).entity
        raw_dimension = params.get("dimension") or params.get("group_by") or params.get("granularity") or ""
        dimensions: list[str] = []
        time_grain = None
        if raw_dimension:
            dimension_text = str(raw_dimension).strip().lower()
            if dimension_text in {"day", "week", "month", "quarter", "year", "日", "周", "月", "季度", "年"}:
                time_grain = {"日": "day", "周": "week", "月": "month", "季度": "quarter", "年": "year"}.get(dimension_text, dimension_text)
            else:
                dim = next((d for d in model.dimensions if dimension_text == d.id.lower() or dimension_text in {a.lower() for a in d.aliases}), None)
                if dim is None:
                    return SemanticParseResult(False, error=f"unknown_dimension:{raw_dimension}")
                dimensions.append(dim.id)
        raw_time = params.get("time_range") or params.get("date_range")
        time_range = SemanticParser._normalise_time_range(raw_time, model.entity(entity).time_field)
        if time_range is None and raw_time:
            return SemanticParseResult(False, error="invalid_time_range")
        if not time_grain and (
            str(params.get("aggregation") or "").lower() in {"trend", "趋势"}
            or params.get("trend") is True
            or params.get("need_trend") is True
        ):
            time_grain = "day"
        return SemanticParseResult(
            True,
            query=SemanticQueryRequest(
                operation="new_query", entity=entity, metrics=metrics, dimensions=dimensions,
                time_range=time_range, time_grain=time_grain, limit=20,
            ),
        )

    @staticmethod
    def _normalise_time_range(value: Any, field: str) -> SemanticTimeRange | None:
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return SemanticTimeRange(str(value[0]), str(value[1]), field)
        text = str(value or "").strip()
        if not text:
            return None
        months = re.findall(r"(20\d{2})[-/.年](\d{1,2})(?:月)?", text)
        if len(months) >= 2:
            y1, m1 = map(int, months[0])
            y2, m2 = map(int, months[-1])
            return SemanticTimeRange(
                f"{y1:04d}-{m1:02d}-01",
                f"{y2:04d}-{m2:02d}-{calendar.monthrange(y2, m2)[1]:02d}",
                field,
            )
        if len(months) == 1:
            year, month = map(int, months[0])
            return SemanticTimeRange(
                f"{year:04d}-{month:02d}-01",
                f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}",
                field,
            )
        dates = re.findall(r"20\d{2}-\d{2}-\d{2}", text)
        if len(dates) >= 2:
            return SemanticTimeRange(dates[0], dates[-1], field)
        return None
