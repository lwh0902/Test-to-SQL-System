"""Travel_b2b semantic query pipeline: snapshot merge, compile, execute, chart.

QueryHarness is the only production caller. ApplicationService must dispatch
here through A2A rather than compiling SQL on the supervisor path.
"""

from __future__ import annotations

import contextvars
from datetime import date
from typing import Any

from app.agents.active_analysis_state import ActiveAnalysisState
from app.agents.analysis_spec import AnalysisSpec
from app.agents.analysis_state_repository import get_analysis_state_repository
from app.agents.catalog_repository import get_catalog_repository
from app.agents.chart_from_spec import chart_from_spec
from app.agents.controlled_loop import run_turn as run_controlled_turn
from app.agents.query_outcome import QueryOutcome, QueryOutcomeStatus
from app.agents.semantic_coverage import validate_semantic_coverage
from app.agents.semantic_model import SemanticModel
from app.agents.semantic_parser import SemanticFilterRequest, SemanticParser, SemanticQueryRequest
from app.agents.semantic_patch import (
    apply_patch,
    followup_required,
    inheritance_errors,
    parse_deterministic_new_query,
    parse_deterministic_patch,
    patch_from_payload,
    SemanticPatch,
)
from app.agents.semantic_snapshot import SemanticSnapshot, snapshot_asdict
from app.agents.semantic_spec_builder import build_analysis_spec
from app.application.contracts import KERNEL_V2, TurnRequest, TurnResult
from app.application.kernel_route import annotate_trace_kernel
from app.models.schemas import ChartConfig
from app.services.semantic_model_service import get_semantic_model_service

query_event_sink: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "query_event_sink", default=None
)

_SNAPSHOT_FILTER_FIELDS = frozenset({
    "channel_name", "supplier_name", "product_name", "product_type",
    "city", "region_group", "country", "car_tier",
})


def _record_run_event(sink: Any, **event: Any) -> None:
    if sink is not None:
        sink.emit(**event)


def _clarification(
    req: TurnRequest,
    msg: str,
    *,
    slots: list[str] | None = None,
    stop_reason: str = "clarify",
    kernel_route: str = KERNEL_V2,
    extra_evidence: dict[str, Any] | None = None,
) -> TurnResult:
    slots = list(slots or ["metric"])
    body = (msg or "").strip() or "还需要补充信息才能准确查询。"
    if slots == ["dimension"]:
        body = body if "维度" in body else "还需要说明按哪个维度拆开，例如渠道、城市或品类。"
    evidence = {
        "clarify_slots": slots,
        "via_harness": True,
        "pipeline": "query_harness_v2",
        **(extra_evidence or {}),
    }
    return TurnResult(
        response_type="clarification",
        message=body,
        answer=body,
        terminal_status=QueryOutcomeStatus.INVALID_REQUEST.value,
        stop_reason=stop_reason,
        kernel_route=kernel_route,
        clarify_slots=slots,
        ux_hints=slots,
        evidence=evidence,
        rows=[],
        rows_count=0,
    )


def _snapshot_from_state(state: ActiveAnalysisState | None) -> SemanticSnapshot | None:
    if state is None:
        return None
    raw = getattr(state, "semantic_snapshot", None)
    if isinstance(raw, dict):
        return SemanticSnapshot.from_dict(raw)
    return None


def _ensure_trend_grain(query: SemanticQueryRequest, question: str) -> SemanticQueryRequest:
    if query.time_grain or "趋势" not in (question or ""):
        return query
    grain = "month"
    tr = query.time_range
    if tr and tr.start and tr.end:
        try:
            start = date.fromisoformat(str(tr.start)[:10])
            end = date.fromisoformat(str(tr.end)[:10])
            grain = "day" if (end - start).days <= 21 else "month"
        except ValueError:
            grain = "month"
    query.time_grain = grain
    return query


def _absorb_grounded_filters(
    query: SemanticQueryRequest, spec: AnalysisSpec
) -> SemanticQueryRequest:
    existing = {item.field for item in query.filters}
    extra = []
    for item in spec.filters or []:
        if item.field not in _SNAPSHOT_FILTER_FIELDS or item.field in existing:
            continue
        extra.append(SemanticFilterRequest(item.field, item.op or "=", item.value))
    if not extra:
        return query
    query.filters = list(query.filters) + extra
    return query


def _public_filters(query: SemanticQueryRequest) -> list[dict[str, Any]]:
    return [{"field": item.field, "op": item.op, "value": item.value} for item in query.filters]


def _public_time(query: SemanticQueryRequest) -> dict[str, str] | None:
    tr = query.time_range
    if tr is None or not tr.start:
        return None
    return {"start": tr.start, "end": tr.end, "field": tr.field or ""}


def turn_result_to_harness_payload(tr: TurnResult) -> dict[str, Any]:
    payload = tr.to_public_dict()
    chart = payload.get("chart")
    if chart is not None and hasattr(chart, "model_dump"):
        payload["chart"] = chart.model_dump()
    elif chart is not None and hasattr(chart, "dict"):
        payload["chart"] = chart.dict()
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    payload["evidence"] = {**evidence, "via_harness": True, "query_agent": "query"}
    payload["via_harness"] = True
    return payload


def turn_result_from_harness_payload(
    result: dict[str, Any] | None, *, kernel_route: str = KERNEL_V2
) -> TurnResult:
    payload = result.get("payload") if isinstance(result, dict) else None
    if not isinstance(payload, dict):
        payload = result if isinstance(result, dict) else {}
    chart = payload.get("chart")
    if isinstance(chart, dict):
        try:
            chart = ChartConfig.model_validate(chart)
        except Exception:
            chart = None
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    evidence = {
        **evidence,
        "via_harness": True,
        "query_agent": "query",
        "transport": "a2a_dispatcher",
    }
    tr = TurnResult(
        response_type=str(payload.get("response_type") or payload.get("type") or "answer"),
        message=str(payload.get("message") or payload.get("answer") or ""),
        answer=str(payload.get("answer") or payload.get("message") or ""),
        terminal_status=str(payload.get("terminal_status") or ""),
        stop_reason=str(payload.get("stop_reason") or ""),
        sql=str(payload.get("sql") or ""),
        columns=list(payload.get("columns") or []),
        rows=list(payload.get("rows") or []),
        rows_count=int(payload.get("rows_count") or 0),
        chart=chart,
        candidates=list(payload.get("candidates") or []),
        trace=list(payload.get("trace") or []),
        intent=payload.get("intent"),
        artifacts=list(payload.get("artifacts") or []),
        task_id=payload.get("task_id"),
        analysis_spec=payload.get("analysis_spec"),
        query_outcome=payload.get("query_outcome"),
        evidence=evidence,
        active_state_version=payload.get("active_state_version"),
        kernel_route=str(payload.get("kernel_route") or kernel_route),
        clarify_slots=list(payload.get("clarify_slots") or []),
        ux_hints=list(payload.get("ux_hints") or []),
        next_actions=list(payload.get("next_actions") or []),
    )
    if result and result.get("artifact_id") and not tr.task_id:
        tr.task_id = str(result.get("artifact_id"))
    return tr


def _resolve_query(
    *,
    question: str,
    original_question: str,
    snapshot: SemanticSnapshot | None,
    model: SemanticModel,
    supervisor_intent: str,
    supervisor_params: dict[str, Any] | None,
    event_sink: Any,
) -> tuple[SemanticQueryRequest | None, SemanticPatch | None, TurnResult | None]:
    is_followup = followup_required(
        supervisor_intent=supervisor_intent,
        question=question,
        snapshot=snapshot,
        model=model,
    )
    if is_followup and snapshot is not None:
        patch = parse_deterministic_patch(question, model)
        if patch is None:
            raw = SemanticParser().parse_patch(
                question, model, snapshot.to_public_dict(), original_question=original_question
            )
            patch = patch_from_payload(raw, model) if isinstance(raw, dict) else SemanticPatch(
                action="clarify", unresolved_slots=["query_condition"], source="parser"
            )
        if patch.action == "clarify" or patch.unresolved_slots:
            _record_run_event(
                event_sink, kind="semantic", status="failed", agent="query", step="patch",
                public_payload={"summary": "追问需要补充条件"},
            )
            return None, patch, _clarification(
                TurnRequest(question=question, user_id=0),
                "还需要补充信息才能准确查询。",
                slots=list(patch.unresolved_slots or ["dimension"]),
                stop_reason="semantic_clarify",
                extra_evidence={"patch_ops": [patch.action], "patch_source": patch.source},
            )
        merged = apply_patch(snapshot, patch)
        lost = inheritance_errors(snapshot, merged, patch)
        if lost:
            _record_run_event(
                event_sink, kind="semantic", status="failed", agent="query", step="inherit",
                public_payload={"summary": "追问未能继承上一轮口径"},
            )
            return None, patch, _clarification(
                TurnRequest(question=question, user_id=0),
                "这一轮追问没有完整继承上一轮已经确认的指标或时间，请换一种说法再试。",
                slots=["query_condition"],
                stop_reason="inheritance_break",
                extra_evidence={"inheritance_errors": lost, "patch_ops": [patch.action]},
            )
        return _ensure_trend_grain(merged, question), patch, None

    _record_run_event(
        event_sink, kind="semantic", status="started", agent="query", step="parse",
        public_payload={"summary": "正在校验指标和筛选条件"},
    )
    seeded = parse_deterministic_new_query(question, model)
    if seeded is not None:
        patch = SemanticPatch(action="reset", source="deterministic")
        return _ensure_trend_grain(seeded, question), patch, None
    parsed = SemanticParser().parse(
        question,
        model,
        previous_spec=None,
        supervisor_params=supervisor_params,
        original_question=original_question,
    )
    if not parsed.ok or parsed.query is None:
        _record_run_event(
            event_sink, kind="semantic", status="failed", agent="query", step="parse",
            public_payload={"summary": "无法确认指标或条件"},
        )
        return None, None, _clarification(
            TurnRequest(question=question, user_id=0),
            "我还不能把这个问题可靠地映射到当前空间已定义的业务指标。请补充指标、对象或时间范围。",
            slots=["metric"],
            stop_reason="semantic_parse",
            extra_evidence={"semantic_parser_error": parsed.error},
        )
    if parsed.query.operation == "clarify" or parsed.query.unresolved_slots:
        _record_run_event(
            event_sink, kind="semantic", status="failed", agent="query", step="parse",
            public_payload={"summary": "需要补充查询条件"},
        )
        return None, SemanticPatch(action="reset" if parsed.query.operation == "new_query" else "clarify"), _clarification(
            TurnRequest(question=question, user_id=0),
            "还需要补充信息才能准确查询。",
            slots=list(parsed.query.unresolved_slots or ["metric"]),
            stop_reason="semantic_clarify",
        )
    patch = SemanticPatch(action="reset" if parsed.query.operation != "modify_query" else "noop", source="parser")
    return _ensure_trend_grain(parsed.query, question), patch, None


def run_semantic_query_runtime(
    req: TurnRequest,
    cat: Any = None,
    *,
    decision: Any = None,
    original_question: str | None = None,
    event_sink: Any = None,
    kernel_route: str = KERNEL_V2,
    mysql_connection: dict | None = None,
    pinned_query_id: str | None = None,
) -> TurnResult:
    """Merge snapshot, compile, execute, persist snapshot, attach chart."""
    from app.services.authorized_connection_service import (
        ConnectionUnavailable,
        resolve_authorized_connection,
    )

    sink = event_sink if event_sink is not None else query_event_sink.get()
    question = req.question or ""
    orig_q = original_question or question
    supervisor_intent = str(getattr(decision, "intent", "") or "")
    supervisor_params = None
    if decision is not None and getattr(decision, "task_spec", None) is not None:
        supervisor_params = getattr(decision.task_spec, "params", None)

    if cat is None:
        try:
            cat = get_catalog_repository().load(req.space_id)
        except Exception:
            cat = None
    if cat is None:
        return TurnResult(
            response_type="error",
            message="当前空间还没有可用的数据目录。",
            answer="当前空间还没有可用的数据目录。",
            terminal_status=QueryOutcomeStatus.INVALID_REQUEST.value,
            stop_reason="catalog_missing",
            kernel_route=kernel_route,
            rows=[],
            rows_count=0,
            evidence={"via_harness": True},
        )

    try:
        conn = mysql_connection if mysql_connection and mysql_connection.get("database") else resolve_authorized_connection(
            user_id=req.user_id,
            space_id=req.space_id,
        )
    except ConnectionUnavailable:
        return TurnResult(
            response_type="error",
            message="当前空间的数据源不可用或无权访问。",
            answer="当前空间的数据源不可用或无权访问。",
            terminal_status=QueryOutcomeStatus.PERMISSION_DENIED.value,
            stop_reason="data_source_unavailable",
            kernel_route=kernel_route,
            rows=[],
            rows_count=0,
            evidence={"via_harness": True},
        )

    state_repo = get_analysis_state_repository()
    prior = None
    state_invalidated = False
    if req.session_id:
        prior = state_repo.load(
            req.session_id,
            req.user_id,
            req.space_id,
            catalog_fingerprint=cat.schema_fingerprint or None,
        )
        if prior is None and state_repo.exists(req.session_id, req.user_id, req.space_id):
            raw = state_repo.load(req.session_id, req.user_id, req.space_id)
            if raw is not None:
                stored_fp = getattr(raw, "catalog_fingerprint", "") or ""
                if stored_fp and cat.schema_fingerprint and stored_fp != cat.schema_fingerprint:
                    state_invalidated = True
                    prior = None
                else:
                    prior = raw

    semantic_model = get_semantic_model_service().load_or_build(req.space_id, cat)
    snapshot = _snapshot_from_state(prior)
    expected_state_version = int(getattr(prior, "version", 0) or 0) if prior else 0
    persist_state = True
    skip_nl_guards = False
    pinned_id = str(pinned_query_id or "").strip()
    if pinned_id:
        from app.core.database import engine as system_engine
        from app.agents.session_query_store import load_query_by_id

        with system_engine.connect() as system_conn:
            loaded = load_query_by_id(
                system_conn, pinned_id, user_id=int(req.user_id), space_id=req.space_id
            )
        if loaded is None:
            return TurnResult(
                response_type="error",
                message="磁贴对应的查询快照不存在。",
                answer="磁贴对应的查询快照不存在。",
                terminal_status=QueryOutcomeStatus.INVALID_REQUEST.value,
                stop_reason="query_not_found",
                kernel_route=kernel_route,
                rows=[],
                rows_count=0,
                evidence={"via_harness": True, "pinned_query_id": pinned_id},
            )
        snapshot = loaded["snapshot"]
        merged = snapshot.to_query()
        patch = SemanticPatch(action="noop", source="pinned")
        persist_state = False
        skip_nl_guards = True
        prior = None
        expected_state_version = 0
        blocked = get_semantic_model_service().unpublished_metric_ids(
            req.space_id, list(snapshot.metrics)
        )
        if blocked:
            return TurnResult(
                response_type="error",
                message="草稿或未发布指标不能出数。",
                answer="草稿或未发布指标不能出数。",
                terminal_status=QueryOutcomeStatus.INVALID_REQUEST.value,
                stop_reason="unpublished_metric",
                kernel_route=kernel_route,
                rows=[],
                rows_count=0,
                evidence={
                    "via_harness": True,
                    "pinned_query_id": pinned_id,
                    "unpublished_metrics": blocked,
                    "semantic_parser": False,
                    "patch_source": "pinned",
                    "query_id": pinned_id,
                },
            )
    else:
        merged, patch, early = _resolve_query(
            question=question,
            original_question=orig_q,
            snapshot=snapshot,
            model=semantic_model,
            supervisor_intent=supervisor_intent,
            supervisor_params=supervisor_params if isinstance(supervisor_params, dict) else None,
            event_sink=sink,
        )
        if early is not None:
            early.kernel_route = kernel_route
            ev = early.evidence if isinstance(early.evidence, dict) else {}
            early.evidence = {
                **ev,
                "semantic_model_version": semantic_model.version,
                "via_harness": True,
            }
            return early
        assert merged is not None
        patch = patch or SemanticPatch(action="noop", source="runtime")

    try:
        semantic_spec = build_analysis_spec(
            merged, semantic_model, cat, original_question=question
        )
    except ValueError:
        _record_run_event(
            sink, kind="semantic", status="failed", agent="query", step="validate",
            public_payload={"summary": "指标或维度未在当前空间确认"},
        )
        return _clarification(
            req,
            "这个问题中的指标或维度还没有在当前空间确认。",
            slots=["metric"],
            stop_reason="semantic_validation",
            kernel_route=kernel_route,
            extra_evidence={"semantic_model_version": semantic_model.version},
        )

    entity_values: list[Any] = []
    if not skip_nl_guards:
        try:
            from app.services.entity_value_service import (
                load_entity_values,
                resolve_filter_values,
                sync_entity_values_if_stale,
            )

            if conn and conn.get("database"):
                sync_entity_values_if_stale(
                    space_id=req.space_id, model=semantic_model, connection=conn
                )
            field_by_dimension = {d.id: d.field for d in semantic_model.dimensions}
            entity_values = load_entity_values(
                req.space_id, field_by_dimension.keys(), field_by_dimension=field_by_dimension
            )
            resolution = resolve_filter_values(
                semantic_spec.filters, orig_q or question, entity_values
            )
            if resolution.ambiguities:
                _record_run_event(
                    sink, kind="semantic", status="failed", agent="query", step="grounding",
                    public_payload={"summary": "需要补充业务名称"},
                )
                return _clarification(
                    req,
                    "找到了多个可能的业务名称，请补充更完整的渠道、供应商或产品名称。",
                    slots=["subject"],
                    stop_reason="entity_ambiguous",
                    kernel_route=kernel_route,
                )
            semantic_spec.filters = resolution.filters
            merged = _absorb_grounded_filters(merged, semantic_spec)
        except Exception:
            pass

    run_coverage = (not skip_nl_guards) and patch.action in {"noop", "reset", "add_dimensions", "add_filters"}
    if run_coverage:
        try:
            coverage = validate_semantic_coverage(
                orig_q or question,
                semantic_spec,
                semantic_model,
                entity_values,
            )
            if not coverage.ok:
                _record_run_event(
                    sink, kind="semantic", status="failed", agent="query", step="validate",
                    public_payload={"summary": "查询条件未完整确认"},
                )
                return _clarification(
                    req,
                    "我识别到的问题条件没有完整进入查询计划，为避免返回错误数据，请换一种说法或稍后重试。",
                    slots=["query_condition"],
                    stop_reason="semantic_coverage",
                    kernel_route=kernel_route,
                    extra_evidence={
                        "semantic_coverage_errors": coverage.errors,
                        "semantic_model_version": semantic_model.version,
                    },
                )
        except Exception:
            pass

    _record_run_event(
        sink, kind="semantic", status="completed", agent="query", step="parse",
        public_payload={"summary": "已生成受约束查询计划"},
    )

    is_followup = False if skip_nl_guards else (
        patch.action not in {"reset", "noop"} or (
            snapshot is not None and followup_required(
                supervisor_intent=supervisor_intent, question=question, snapshot=snapshot, model=semantic_model
            )
        )
    )
    loop = run_controlled_turn(
        question,
        cat,
        state=prior,
        mysql_connection=conn if (conn and conn.get("database")) else None,
        session_id=req.session_id or "",
        user_id=req.user_id,
        space_id=req.space_id,
        allow_heavy=False,
        prebuilt_spec=semantic_spec,
        semantic_followup=is_followup,
        event_sink=sink,
    )

    if loop.state is not None and loop.action in {"answer", "query", "stop_success", "stop_empty"}:
        loop.state.semantic_snapshot = snapshot_asdict(
            SemanticSnapshot.from_query(merged, model_version=semantic_model.version)
        )

    query_id = pinned_id
    if persist_state and loop.state is not None and req.session_id and loop.action in {
        "answer", "query", "stop_success", "stop_empty"
    }:
        try:
            if not loop.state.catalog_fingerprint:
                loop.state.catalog_fingerprint = cat.schema_fingerprint or ""
            if not loop.state.catalog_version:
                loop.state.catalog_version = str(getattr(cat, "version", "") or "")
            saved = state_repo.save(
                loop.state,
                expected_version=expected_state_version,
            )
            query_id = str(saved.get("query_id") or "")
            if not saved.get("ok"):
                _record_run_event(
                    sink, kind="state", status="failed", agent="state", step="persist",
                    public_payload={"summary": "会话上下文未保存", "error_code": saved.get("error", "unknown")},
                )
        except Exception as exc:
            _record_run_event(
                sink, kind="state", status="failed", agent="state", step="persist",
                public_payload={"summary": "会话上下文未保存", "error_code": type(exc).__name__},
            )

    patch_ops = ["pinned_refresh"] if skip_nl_guards else (
        [patch.action] if patch.action not in {"noop"} else list(loop.patch_ops or [])
    )
    if loop.action == "clarify":
        tr = _clarification(
            req,
            loop.answer_text or "请补充信息",
            slots=list(loop.clarify_slots or []),
            stop_reason="clarify",
            kernel_route=kernel_route,
        )
        tr.analysis_spec = loop.spec.to_dict() if loop.spec else None
        tr.active_state_version = (
            int(getattr(loop.state, "version", 0) or 0) if loop.state else None
        )
    elif loop.action == "refuse":
        msg = loop.answer_text or "请求被拒绝"
        term = "SQL_REJECTED" if ("写" in msg or "不安全" in msg or "拒绝" in msg) else "INVALID_REQUEST"
        tr = TurnResult(
            response_type="error",
            message=msg,
            answer=msg,
            terminal_status=term,
            stop_reason="refuse",
            kernel_route=kernel_route,
            sql=loop.sql or "",
            analysis_spec=loop.spec.to_dict() if loop.spec else None,
            rows=[],
            rows_count=0,
            evidence={"via_harness": True},
        )
        if term == "SQL_REJECTED":
            tr.query_outcome = QueryOutcome.sql_rejected(message=msg, sql=loop.sql or "").to_public_dict()
    else:
        outcome = loop.outcome
        if outcome is None and loop.duplicate_skipped and loop.state:
            rows = list(loop.state.last_result_preview or [])
            cols = list(rows[0].keys()) if rows else []
            rc = len(rows)
            term = "SUCCESS_WITH_DATA" if rows else "SUCCESS_EMPTY"
            msg = loop.answer_text or ""
        elif outcome is not None:
            rows = list(outcome.rows_preview or [])
            cols = list(outcome.columns or [])
            rc = int(outcome.rows_count or 0)
            term = outcome.status.value
            msg = loop.answer_text or ""
        else:
            rows, cols, rc, term = [], [], 0, ""
            msg = loop.answer_text or ""

        chart = None
        spec_for_chart = loop.spec or semantic_spec
        if rows and cols:
            chart_cfg = chart_from_spec(spec_for_chart, columns=cols, rows=rows, model=semantic_model)
            if chart_cfg is not None:
                chart = chart_cfg.model_dump() if hasattr(chart_cfg, "model_dump") else chart_cfg

        tr = TurnResult(
            response_type="answer",
            message=msg,
            answer=msg,
            terminal_status=term,
            stop_reason=(
                "reuse" if loop.duplicate_skipped else (
                    "stop_ok" if term == "SUCCESS_WITH_DATA" else (
                        "stop_empty" if term == "SUCCESS_EMPTY" else "stop_ok"
                    )
                )
            ),
            sql=loop.sql or (loop.state.last_sql if loop.state else "") or "",
            columns=cols,
            rows=rows,
            rows_count=rc,
            chart=chart,
            kernel_route=kernel_route,
            analysis_spec=loop.spec.to_dict() if loop.spec else None,
            query_outcome=outcome.to_public_dict() if outcome else None,
            evidence=loop.evidence.to_dict() if loop.evidence else None,
            active_state_version=int(getattr(loop.state, "version", 0) or 0) if loop.state else None,
        )
        if outcome:
            tr.query_outcome = outcome.to_public_dict()
            tr.terminal_status = outcome.status.value

    tr.trace = annotate_trace_kernel(
        [
            {
                "node": "query_harness_v2",
                "status": "done",
                "output": {
                    "action": loop.action,
                    "latency_ms": loop.latency_ms,
                    "clarify_slots": list(loop.clarify_slots or []),
                    "is_followup": bool(is_followup),
                    "duplicate_skipped": loop.duplicate_skipped,
                    "patch_ops": patch_ops,
                    "agents_called": ["query", *list(loop.agents_called or [])],
                    "action_trace": list(loop.action_trace or []),
                    "via_harness": True,
                },
            }
        ],
        kernel_route,
    )
    base_ev = tr.evidence if isinstance(tr.evidence, dict) else {}
    tr.evidence = {
        **base_ev,
        "catalog_version": cat.version,
        "schema_fingerprint": cat.schema_fingerprint,
        "catalog_readiness": cat.readiness.to_dict() if cat.readiness else None,
        "catalog_source": "repository",
        "pipeline": "query_harness_v2",
        "duplicate_skipped": bool(loop.duplicate_skipped),
        "is_followup": bool(is_followup),
        "agents_called": ["query", *list(loop.agents_called or [])],
        "patch_ops": patch_ops,
        "patch_source": patch.source,
        "state_invalidated": state_invalidated,
        "semantic_model_version": semantic_model.version,
        "semantic_model_provenance": semantic_model.provenance,
        "semantic_parser": False if skip_nl_guards else True,
        "active_state_version": tr.active_state_version,
        "via_harness": True,
        "query_agent": "query",
        "query_id": query_id or pinned_id,
        "pinned_query_id": pinned_id,
        "transport": "a2a_dispatcher",
        "metrics": list(merged.metrics),
        "dimensions": list(merged.dimensions),
        "filters": _public_filters(merged),
        "time_range": _public_time(merged),
        "time_grain": merged.time_grain,
        "entity": merged.entity,
    }
    if cat.readiness and str(getattr(cat.readiness.status, "value", cat.readiness.status)) == "DEGRADED":
        limits = list(cat.readiness.limited_capabilities or [])
        if limits and tr.message and "受限" not in (tr.message or ""):
            tr.message = (tr.message or "") + f"（Catalog DEGRADED，受限: {', '.join(limits[:4])}）"
            tr.answer = tr.message
    return tr
