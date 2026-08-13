"""AnalysisApplicationService — unique product entry for JSON + SSE (R1a–R1c).

Business logic lives here. FastAPI routes only adapt transport.
kernel_route from pilot flags: analysis_kernel_v2 | legacy_rollback (R1c).
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator, Optional

from app.application.contracts import (
    KERNEL_LEGACY,
    KERNEL_V2,
    TurnRequest,
    TurnResult,
    preview_rows,
)
from app.application.data_plane_precheck import run_data_plane_precheck
from app.application.kernel_route import annotate_trace_kernel, resolve_kernel_route
from app.application.l0_write_guard import detect_write_intent, refuse_message
from app.agents.query_outcome import (
    QueryOutcome,
    QueryOutcomeStatus,
    build_outcome_from_agent_state,
    build_outcome_from_query_payload,
    next_actions_for_outcome,
)
from app.models.schemas import ChartConfig, QueryIntent
from app.agents.analysis_pipeline import run_analysis
from app.agents.analysis_state_repository import get_analysis_state_repository
from app.agents.catalog_repository import get_catalog_repository
from app.agents.controlled_loop import run_turn as run_controlled_turn
from app.agents.diagnosis_summary_repository import get_diagnosis_summary_repository
from app.agents.semantic_catalog import ReadinessStatus, analysis_allowed
from app.agents.supervisor_decision import SupervisorDecision, decide as supervisor_decide
from app.agents.semantic_parser import SemanticParser
from app.agents.semantic_spec_builder import build_analysis_spec
from app.services.semantic_model_service import get_semantic_model_service
from app.pilot.flags import PilotFlags, default_flags
from app.services.agent import AgentState, get_graph


def _should_generate_analysis_answer(response_type: str, has_data: bool, has_plan: bool) -> bool:
    return not has_plan and response_type == "answer"


def _get_async_llm_client():
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/anthropic"),
    )


def _state_from_request(req: TurnRequest) -> AgentState:
    return AgentState(
        question=req.question,
        user_role=req.user_role,
        workspace_id=req.workspace_id,
        space_id=req.space_id,
        user_id=req.user_id,
        session_id=req.session_id,
        selected_metric=req.selected_metric,
        selected_query_type=req.selected_query_type,
    )


def _extract_data_map(accumulated: dict) -> tuple[Any, Any]:
    data_map = None
    db_identity = None
    if accumulated.get("response_type") == "data_map" or accumulated.get("data_map"):
        data_map = accumulated.get("data_map")
        db_identity = accumulated.get("db_identity")
        if not data_map:
            for step in accumulated.get("trace") or []:
                if not isinstance(step, dict):
                    continue
                if step.get("node") == "schema_help_responder" and step.get("output"):
                    output = step["output"] or {}
                    data_map = output.get("data_map") or data_map
                    db_identity = output.get("db_identity") or db_identity
                    break
    return data_map, db_identity


def _attach_outcome(tr: TurnResult, outcome: QueryOutcome | None = None) -> TurnResult:
    """Ensure terminal_status / query_outcome / stop_reason align with QueryOutcome."""
    if outcome is None:
        payload = {
            "message": tr.message,
            "error_code": tr.terminal_status or "",
            "sql": tr.sql,
            "columns": tr.columns,
            "rows": tr.rows,
            "rows_count": tr.rows_count,
            "type": tr.response_type,
            "query_outcome": tr.query_outcome if isinstance(tr.query_outcome, dict) else None,
        }
        # Prefer explicit agent-state style bridge
        class _S:
            pass

        s = _S()
        s.response_type = tr.response_type
        s.message = tr.message
        s.error_code = tr.terminal_status or ""
        s.sql = tr.sql
        s.rows = tr.rows
        s.columns = tr.columns
        s.trace_id = tr.trace_id
        outcome = build_outcome_from_agent_state(s)

    # Never disguise reject/error/timeout as SUCCESS_EMPTY via rows=0 alone
    st = outcome.status
    tr.query_outcome = outcome.to_public_dict()
    tr.terminal_status = st.value
    if st == QueryOutcomeStatus.SQL_REJECTED:
        tr.response_type = "error"
        tr.stop_reason = tr.stop_reason or "sql_rejected"
        tr.rows = []
        tr.rows_count = 0
        tr.data_map = None
    elif st == QueryOutcomeStatus.PERMISSION_DENIED:
        tr.response_type = "error"
        tr.stop_reason = tr.stop_reason or "stop_denied"
        tr.rows = []
        tr.rows_count = 0
    elif st == QueryOutcomeStatus.SUCCESS_EMPTY:
        tr.stop_reason = tr.stop_reason or "stop_empty"
    elif st == QueryOutcomeStatus.SUCCESS_WITH_DATA:
        tr.stop_reason = tr.stop_reason or "stop_ok"
    elif st in {
        QueryOutcomeStatus.EXECUTION_ERROR,
        QueryOutcomeStatus.TIMEOUT,
        QueryOutcomeStatus.CANCELLED,
        QueryOutcomeStatus.INVALID_REQUEST,
    }:
        if tr.response_type == "answer":
            tr.response_type = "error" if st != QueryOutcomeStatus.INVALID_REQUEST else "clarification"
        tr.stop_reason = tr.stop_reason or (
            "stop_error" if st != QueryOutcomeStatus.INVALID_REQUEST else "clarify"
        )
        if st != QueryOutcomeStatus.INVALID_REQUEST:
            tr.rows = []
            tr.rows_count = 0
    if not tr.message:
        tr.message = outcome.message or tr.message
        tr.answer = tr.message
    return tr


def _l0_refuse_result(req: TurnRequest, *, kernel_route: str) -> TurnResult:
    decision = detect_write_intent(req.question)
    msg = refuse_message()
    outcome = QueryOutcome.sql_rejected(
        message=msg,
        sql=decision.extracted_sql or "",
        error_code="SQL_REJECTED",
    )
    tr = TurnResult(
        response_type="error",
        message=msg,
        answer=msg,
        terminal_status=QueryOutcomeStatus.SQL_REJECTED.value,
        stop_reason="sql_rejected",
        sql=decision.extracted_sql or "",
        columns=[],
        rows=[],
        rows_count=0,
        kernel_route=kernel_route,
        trace_id="",
        trace=annotate_trace_kernel(
            [
                {
                    "node": "l0_write_guard",
                    "status": "denied",
                    "l0_refuse_write": True,
                    "matched": decision.matched,
                    "reason": decision.reason,
                }
            ],
            kernel_route,
        ),
        query_outcome=outcome.to_public_dict(),
    )
    return tr


def _blocked_for_pilot_result(req: TurnRequest, precheck: Any, *, kernel_route: str) -> TurnResult:
    missing = list(getattr(precheck, "missing", None) or [])
    status = str(getattr(precheck, "status", None) or "BLOCKED_FOR_PILOT")
    msg = (
        f"空间预检失败（{status}）。缺失数据面能力: {', '.join(missing) or 'unknown'}。"
        "真人脚本与试点评分不得启动。请检查空间访问、连接与只读账号。"
    )
    outcome = QueryOutcome.permission_denied(
        message=msg,
        error_code="PERMISSION_DENIED",
    )
    pre_dict = precheck.to_dict() if hasattr(precheck, "to_dict") else {}
    tr = TurnResult(
        response_type="error",
        message=msg,
        answer=msg,
        terminal_status=QueryOutcomeStatus.PERMISSION_DENIED.value,
        stop_reason="blocked_for_pilot",
        rows=[],
        rows_count=0,
        kernel_route=kernel_route,
        trace=annotate_trace_kernel(
            [
                {
                    "node": "data_plane_precheck",
                    "status": "denied",
                    "output": pre_dict,
                }
            ],
            kernel_route,
        ),
        query_outcome=outcome.to_public_dict(),
        evidence={"data_plane_precheck": pre_dict},
    )
    return tr


def _result_from_graph_dict(
    result: dict[str, Any],
    *,
    message_override: str | None = None,
    kernel_route: str = KERNEL_LEGACY,
    extra: dict | None = None,
) -> TurnResult:
    rows = list(result.get("rows") or [])
    rows_count = result.get("rows_count")
    if rows_count is None:
        rows_count = len(rows)
    msg = message_override if message_override is not None else (result.get("message") or "")
    data_map, db_identity = _extract_data_map(result)
    terminal = str(result.get("terminal_status") or result.get("error_code") or "")
    tr = TurnResult(
        response_type=str(result.get("response_type") or result.get("type") or "answer"),
        message=msg,
        answer=msg,
        trace_id=str(result.get("trace_id") or ""),
        terminal_status=terminal,
        stop_reason=str(result.get("stop_reason") or ""),
        sql=str(result.get("sql") or ""),
        columns=list(result.get("columns") or []),
        rows=rows,
        rows_count=int(rows_count or 0),
        chart=result.get("chart"),
        candidates=list(result.get("candidates") or []),
        trace=list(result.get("trace") or []),
        intent=result.get("intent"),
        artifacts=list(result.get("artifacts") or []),
        task_id=result.get("task_id"),
        analysis_spec=result.get("analysis_spec"),
        query_outcome=result.get("query_outcome"),
        evidence=result.get("evidence"),
        active_state_version=result.get("active_state_version"),
        kernel_route=kernel_route,
        data_map=data_map,
        db_identity=db_identity,
        plan=result.get("plan"),
        plan_results=result.get("plan_results"),
    )
    if extra:
        for k, v in extra.items():
            if hasattr(tr, k) and v is not None:
                setattr(tr, k, v)
    return _attach_outcome(tr)


def _seed_has_success_with_data(seed: dict | None) -> bool:
    if not seed:
        return False
    rows = seed.get("rows") or []
    rc = seed.get("rows_count")
    try:
        rc_i = int(rc) if rc is not None else len(rows)
    except Exception:
        rc_i = len(rows)
    return rc_i > 0 and bool(rows or rc_i)


class AnalysisApplicationService:
    """Single product entry. JSON and SSE both call handle_turn / handle_turn_stream."""

    def __init__(
        self,
        graph: Any = None,
        *,
        kernel_route: str | None = None,
        flags: PilotFlags | None = None,
        skip_data_plane_precheck: bool = False,
        catalog_repo: Any = None,
        state_repo: Any = None,
        pending_query_repo: Any = None,
        diagnosis_repo: Any = None,
        diagnosis_agent_call: Any = None,
    ) -> None:
        self._graph = graph
        self._flags = flags
        # Explicit kernel_route wins; else resolve from flags (R1c).
        if kernel_route:
            self.kernel_route = kernel_route
        else:
            self.kernel_route = resolve_kernel_route(flags if flags is not None else default_flags())
        self.skip_data_plane_precheck = skip_data_plane_precheck
        self._catalog_repo = catalog_repo
        self._state_repo = state_repo
        self._pending_query_repo = pending_query_repo
        self._diagnosis_repo = diagnosis_repo
        # None → production dispatcher_agent_call (R5.5b). Tests may inject component_only.
        self._diagnosis_agent_call = diagnosis_agent_call

    def _graph_or_default(self) -> Any:
        return self._graph if self._graph is not None else get_graph()

    def _catalog_repo_or_default(self):
        return self._catalog_repo if self._catalog_repo is not None else get_catalog_repository()

    def _state_repo_or_default(self):
        return self._state_repo if self._state_repo is not None else get_analysis_state_repository()

    def _pending_query_repo_or_default(self):
        if self._pending_query_repo is not None:
            return self._pending_query_repo
        from app.agents.pending_query_repository import get_pending_query_repository

        return get_pending_query_repository()

    def _diagnosis_repo_or_default(self):
        return (
            self._diagnosis_repo
            if self._diagnosis_repo is not None
            else get_diagnosis_summary_repository()
        )

    def _run_precheck(self, req: TurnRequest):
        if self.skip_data_plane_precheck:
            return None
        return run_data_plane_precheck(
            space_id=req.space_id,
            user_id=req.user_id,
            user_role=req.user_role,
        )

    def _load_catalog_for_turn(self, req: TurnRequest):
        """Load persisted SemanticCatalog for space (R2). None if missing."""
        if self.kernel_route != KERNEL_V2:
            return None
        try:
            return self._catalog_repo_or_default().load(req.space_id)
        except Exception:
            return None

    def _catalog_block_if_needed(self, req: TurnRequest, cat) -> TurnResult | None:
        """BLOCKED catalog cannot enter analysis on v2 path."""
        if cat is None:
            # Missing catalog: allow legacy-compatible help/chat via graph for migration,
            # but stamp evidence so R2 main-chain visibility is honest.
            return None
        st = cat.readiness.status if cat.readiness else ReadinessStatus.BLOCKED
        if isinstance(st, str):
            try:
                st = ReadinessStatus(st)
            except Exception:
                st = ReadinessStatus.BLOCKED
        if st == ReadinessStatus.BLOCKED:
            reasons = list(cat.readiness.reasons if cat.readiness else []) or ["catalog blocked"]
            msg = (
                "当前数据源建档状态为 BLOCKED，暂时不能分析。\n"
                f"原因：{'；'.join(reasons[:5])}\n\n"
                "请重新配置连接并执行「分析建档 / Profile」，确认 READY 后再提问。"
            )
            outcome = QueryOutcome.invalid_request(message=msg)
            outcome.error_code = "CATALOG_BLOCKED"
            return TurnResult(
                response_type="error",
                message=msg,
                answer=msg,
                terminal_status="INVALID_REQUEST",
                stop_reason="catalog_blocked",
                kernel_route=self.kernel_route,
                query_outcome=outcome.to_public_dict(),
                ux_hints=["重新 Profile", "检查库权限与连通性"],
                next_actions=[
                    {"id": "run_catalog_profile", "label": "重新分析建档"},
                    {"id": "open_space_settings", "label": "检查数据源连接"},
                ],
                evidence={
                    "catalog_readiness": cat.readiness.to_dict() if cat.readiness else {},
                    "schema_fingerprint": cat.schema_fingerprint,
                },
                rows=[],
                rows_count=0,
            )
        return None

    def _schema_inventory_from_catalog(
        self,
        req: TurnRequest,
        cat,
        decision: SupervisorDecision | None = None,
    ) -> TurnResult:
        """Build OP-facing schema briefing + data_map from SemanticCatalog."""
        import re

        q = (decision.resolved_question if decision else None) or req.question or ""
        explainish = bool(
            re.search(
                r"干嘛|干什么|做什么|用途|分别|是什么|介绍|含义|说明|职责|用来|有什么用|干什么用",
                q,
            )
        )

        def _human_title(name: str, comment: str = "") -> str:
            c = (comment or "").strip()
            if c:
                # strip trailing 表
                return re.sub(r"表$", "", c) or c
            # snake_case → rough CN-ish label fallback: keep readable tech name
            return name.replace("_", " ")

        def _purpose(name: str, comment: str = "") -> str:
            c = (comment or "").strip()
            if c:
                return c if c.endswith(("。", ".", "！", "!")) else f"{c}"
            # light fallback from name tokens — no ecommerce dictionary
            n = (name or "").lower()
            if "user" in n:
                return "用户/账号相关信息"
            if "log" in n:
                return "系统或接口调用日志"
            if "event" in n:
                return "业务或功能事件流水"
            if "scan" in n:
                return "扫描/质检相关记录"
            if "order" in n:
                return "订单相关业务数据"
            if "product" in n:
                return "商品相关业务数据"
            if "refund" in n:
                return "退款相关业务数据"
            if "traffic" in n or "page" in n:
                return "流量/访问相关事件"
            return "业务数据表（详见字段注释）"

        def _col_role_buckets(columns) -> tuple[list[str], list[str], list[str], list[str]]:
            keys, times, measures, dims = [], [], [], []
            for c in columns or []:
                nm = getattr(c, "name", "") or ""
                role = str(getattr(c, "role", "") or "")
                if getattr(c, "is_primary_key", False) or getattr(c, "is_foreign_key", False):
                    keys.append(nm)
                if role in {"time", "date"} or re.search(r"(_at|_time|date|time)$", nm, re.I):
                    times.append(nm)
                if role in {"amount", "measure", "ratio", "continuous_numeric"}:
                    measures.append(nm)
                if role in {"enum_dimension", "status", "text", "description_text"}:
                    dims.append(nm)
            return keys[:8], times[:6], measures[:8], dims[:8]

        tables_out: list[dict[str, Any]] = []
        field_count = 0
        lines_detail: list[str] = []
        for t in list(getattr(cat, "tables", None) or [])[:30]:
            name = getattr(t, "name", "") or ""
            comment = getattr(t, "comment", "") or ""
            title = _human_title(name, comment)
            purpose = _purpose(name, comment)
            cols_payload = []
            for c in list(getattr(t, "columns", None) or []):
                cname = getattr(c, "name", "") or ""
                ctype = str(getattr(c, "data_type", None) or getattr(c, "type", "") or "")
                ccomment = getattr(c, "comment", "") or ""
                cols_payload.append({"name": cname, "type": ctype, "comment": ccomment})
            field_count += len(cols_payload)
            keys, times, measures, dims = _col_role_buckets(getattr(t, "columns", None) or [])
            # human highlight fields
            highlights = []
            for c in cols_payload:
                if c.get("comment") and c["name"] not in {"id", "workspace_id", "datasource_id"}:
                    highlights.append(f"{c['comment']}（{c['name']}）")
                if len(highlights) >= 3:
                    break
            tables_out.append(
                {
                    "name": name,
                    "title": title,
                    "description": purpose,
                    "columns": cols_payload,
                    "key_columns": keys,
                    "time_columns": times,
                    "measure_columns": measures,
                    "dimension_columns": dims,
                }
            )
            if explainish:
                extra = f" 关键信息：{'；'.join(highlights)}。" if highlights else ""
                lines_detail.append(f"· {title}（{name}）：{purpose}。{extra}".rstrip())
            else:
                lines_detail.append(f"· {title}（{name}）：{purpose}")

        space = req.space_id or getattr(cat, "database_id", "") or ""
        # space one-liner from table titles
        if space == "tech_quality":
            blurb = "这个库主要是技术质量相关数据：用户、扫描质检、功能事件和 API 调用，适合看失败率、错误类型和接口健康度。"
        elif space == "ecommerce":
            blurb = "这个库主要是电商经营数据：用户、订单、商品、退款和流量，适合看订单量、金额、退款和渠道结构。"
        else:
            titles = [t["title"] for t in tables_out[:4]]
            blurb = (
                f"当前空间 `{space}` 共 {len(tables_out)} 张业务表"
                + (f"，主要包括{'、'.join(titles)}等。" if titles else "。")
            )

        if not tables_out:
            msg = f"{blurb}\n\n当前还没有可展示的表元数据，请先完成库表建档。"
            return TurnResult(
                response_type="answer",
                message=msg,
                answer=msg,
                terminal_status="SUCCESS_EMPTY",
                stop_reason="schema_inventory_empty",
                kernel_route=self.kernel_route,
            )

        header = blurb
        if explainish:
            body = "各表是做什么的：\n" + "\n".join(lines_detail)
        else:
            body = (
                f"共 {len(tables_out)} 张表、约 {field_count} 个字段。\n"
                + "\n".join(lines_detail)
                + "\n\n想了解某张表的字段，或直接问例如「扫描失败率多少」。"
            )
        msg = f"{header}\n\n{body}"

        # recommended questions grounded on this catalog (no cross-space ecommerce defaults)
        rec_qs: list[dict[str, Any]] = []
        for t in tables_out[:4]:
            rec_qs.append(
                {
                    "text": f"{t['title']}大概有多少条？",
                    "table": t["name"],
                    "intent": "data_query",
                    "source": "schema",
                }
            )
        # status/error oriented if present
        for t in tables_out:
            col_names = {c["name"] for c in t.get("columns") or []}
            if "status" in col_names and re.search(r"scan|order|log", t["name"], re.I):
                rec_qs.append(
                    {
                        "text": f"{t['title']}里失败/异常大概多少？",
                        "table": t["name"],
                        "intent": "data_query",
                        "source": "schema",
                    }
                )
                break
        if any(t["name"] == "api_logs" for t in tables_out):
            rec_qs.append(
                {
                    "text": "api_logs 里错误请求大概多少？",
                    "table": "api_logs",
                    "intent": "data_query",
                    "source": "schema",
                }
            )

        data_map = {
            "space_id": space,
            "mode": "schema",
            "summary": {
                "table_count": len(tables_out),
                "field_count": field_count,
                "metric_count": 0,
            },
            "tables": tables_out,
            "metrics": [],
            "recommended_questions": rec_qs[:8],
        }

        identity = getattr(cat, "identity", None) or {}
        db_identity = {
            "space_id": space,
            "space_name": identity.get("space_name") or space,
            "is_preset": bool(identity.get("is_preset") or space in {"tech_quality", "ecommerce"}),
            "table_count": len(tables_out),
            "connection": {
                "db_type": identity.get("engine") or identity.get("db_type") or "mysql",
                "host_masked": str(identity.get("host") or "127.0.0.1")[:3] + "***",
                "port": identity.get("port") or 3306,
                "db_name": identity.get("database") or identity.get("db_name") or "",
            },
        }

        return TurnResult(
            response_type="data_map",
            message=msg,
            answer=msg,
            terminal_status="SUCCESS_WITH_DATA",
            stop_reason="schema_inventory",
            kernel_route=self.kernel_route,
            data_map=data_map,
            db_identity=db_identity,
            evidence={
                "pipeline": "schema_inventory_v2",
                "catalog_source": "repository",
                "explain": explainish,
                "table_count": len(tables_out),
            },
            next_actions=[
                {"id": f"q_{i}", "label": q["text"]}
                for i, q in enumerate(rec_qs[:5])
            ],
        )

    def _attach_supervisor(self, tr: TurnResult, decision: SupervisorDecision | None) -> TurnResult:
        if decision is None:
            return tr
        safe = decision.safe_event()["data"]
        tr.supervisor_decision = safe
        tr.task_spec = decision.task_spec.to_dict()
        # single supervisor_decision node in trace
        node = {
            "node": "supervisor_decision",
            "event": "supervisor_decision",
            "status": "done",
            "output": safe,
            "data": safe,
        }
        existing = list(tr.trace or [])
        # drop any prior supervisor nodes to keep exactly one
        existing = [
            s
            for s in existing
            if not (
                isinstance(s, dict)
                and (s.get("node") == "supervisor_decision" or s.get("event") == "supervisor_decision")
            )
        ]
        tr.trace = annotate_trace_kernel([node] + existing, self.kernel_route)
        if tr.intent is None:
            tr.intent = {"intent": decision.intent, "route": decision.route}
        return tr

    def _run_supervisor(self, req: TurnRequest) -> SupervisorDecision:
        """Exactly one SupervisorDecision per analyzable turn (R5.5a).

        Production default: L1 flash ON. Opt-out only via DATAPILOT_SUPERVISOR_L1=0.
        Tests force L1 network fail via conftest adapter → automatic L2 degrade.
        """
        flag = os.getenv("DATAPILOT_SUPERVISOR_L1", "1").strip().lower()
        skip_l1 = flag in {"0", "false", "no", "off"}
        wm = None
        recent = None
        try:
            if req.session_id:
                from app.services.persistence import load_recent_messages

                recent = load_recent_messages(
                    req.session_id, limit=4, user_id=req.user_id, space_id=req.space_id
                )
                st = self._state_repo_or_default().load(req.session_id, req.user_id, req.space_id)
                if st is not None:
                    wm = {
                        "last_result_status": "success" if st.last_result_preview else "no_data",
                        "last_route": "data_query",
                        "last_target": st.active_subject,
                        "last_spec": {
                            "subject": st.active_subject,
                            "measures": list(st.measures or [])[:3],
                            "dimensions": list(st.dimensions or [])[:4],
                        },
                    }
        except Exception:
            wm = None
        return supervisor_decide(
            req.question or "",
            recent_messages=recent,
            working_memory=wm,
            space_id=req.space_id,
            skip_l1=skip_l1,
        )

    def _pending_time_result(
        self,
        req: TurnRequest,
        message: str,
        *,
        decision: SupervisorDecision | None = None,
        start: str = "",
        end: str = "",
        reason: str = "",
    ) -> TurnResult:
        tr = TurnResult(
            response_type="clarification",
            message=message,
            answer=message,
            terminal_status="INVALID_REQUEST",
            stop_reason="pending_time_confirmation",
            kernel_route=self.kernel_route,
            sql="",
            rows=[],
            rows_count=0,
            clarify_slots=["time_confirmation"],
            evidence={
                "pending_time_confirmation": True,
                "candidate_time_range": {"start": start, "end": end},
                "reason": reason,
            },
            next_actions=[
                {"id": "confirm_time", "label": "确定"},
                {"id": "replace_time", "label": "修改时间"},
            ],
        )
        return self._attach_supervisor(tr, decision) if decision else tr

    def _pause_for_time_confirmation(
        self, req: TurnRequest, decision: SupervisorDecision
    ) -> TurnResult | None:
        if decision.intent not in {"data_query", "follow_up"} or not req.session_id:
            return None
        from app.agents.pending_query_repository import PendingTimeConfirmation
        from app.agents.time_confirmation import detect_time_confirmation

        candidate = detect_time_confirmation(req.question)
        if candidate is None:
            return None
        semantic_version = ""
        try:
            cat = self._catalog_repo_or_default().load(req.space_id)
            if cat is not None:
                semantic_version = get_semantic_model_service().load_or_build(
                    req.space_id, cat
                ).version
        except Exception:
            pass
        self._pending_query_repo_or_default().save(
            req.session_id,
            req.user_id,
            req.space_id,
            PendingTimeConfirmation(
                original_question=req.question,
                start=candidate.start,
                end=candidate.end,
                reason=candidate.reason,
                prompt=candidate.prompt,
                semantic_model_version=semantic_version,
            ),
        )
        return self._pending_time_result(
            req,
            candidate.prompt,
            decision=decision,
            start=candidate.start,
            end=candidate.end,
            reason=candidate.reason,
        )

    def _resolve_pending_time(self, req: TurnRequest) -> tuple[str, TurnResult | None]:
        """Return an explicit resumed question, a response, or an empty string.

        Confirmed work remains pending until the resumed query actually succeeds.
        This makes a transient model/provider failure safely retryable.
        """
        if not req.session_id:
            return "", None
        repo = self._pending_query_repo_or_default()
        pending = repo.load(req.session_id, req.user_id, req.space_id)
        if pending is None:
            return "", None
        from app.agents.pending_query_repository import PendingTimeConfirmation
        from app.agents.time_confirmation import classify_confirmation_reply

        reply = classify_confirmation_reply(req.question)
        if reply.action == "new_query":
            repo.delete(req.session_id, req.user_id, req.space_id)
            return "", None
        if reply.action == "reject":
            return "", self._pending_time_result(
                req,
                "好的，请提供时间，例如“2026年7月”或“最近30天”。",
                start=pending.start,
                end=pending.end,
                reason=pending.reason,
            )
        start = reply.start if reply.action == "replace" else pending.start
        end = reply.end if reply.action == "replace" else pending.end
        if reply.action == "replace":
            repo.save(
                req.session_id,
                req.user_id,
                req.space_id,
                PendingTimeConfirmation(
                    original_question=pending.original_question,
                    start=start,
                    end=end,
                    reason=pending.reason,
                    prompt=pending.prompt,
                    semantic_model_version=pending.semantic_model_version,
                ),
            )
        return f"{pending.original_question}，时间范围为{start}至{end}", None

    def _catalog_not_ready_result(self, req: TurnRequest, decision: SupervisorDecision | None = None) -> TurnResult:
        space = req.space_id or "当前空间"
        msg = (
            f"还不能分析「{space}」里的数据：尚未完成数据源建档（Catalog）。\n\n"
            "请按顺序完成：\n"
            "1. 在空间设置中配置只读 MySQL 连接\n"
            "2. 点击「分析建档 / Profile」生成数据地图\n"
            "3. 确认状态为 READY 后再提问\n\n"
            "建档完成后可以这样问：「2024 年订单总金额是多少」。"
        )
        tr = TurnResult(
            response_type="error",
            message=msg,
            answer=msg,
            terminal_status="INVALID_REQUEST",
            stop_reason="catalog_not_ready",
            kernel_route=self.kernel_route,
            ux_hints=[
                "需要先完成 Catalog Profile",
                "Profile 成功后 readiness=READY",
            ],
            next_actions=[
                {"id": "open_space_settings", "label": "去配置数据源"},
                {"id": "run_catalog_profile", "label": "开始分析建档"},
                {"id": "example_query", "label": "示例：2024年订单总金额"},
            ],
            evidence={
                "reason": "catalog_not_ready",
                "space_id": req.space_id,
                "ux": "guided_onboarding",
            },
            rows=[],
            rows_count=0,
        )
        return self._attach_supervisor(tr, decision) if decision else tr

    def _enrich_clarification(
        self,
        req: TurnRequest,
        msg: str,
        *,
        decision: SupervisorDecision | None = None,
        slots: list[str] | None = None,
        candidates: list | None = None,
        stop_reason: str = "clarify",
    ) -> TurnResult:
        q = (req.question or "").strip()
        slots = list(slots or [])
        # heuristic slots if planner didn't provide
        if not slots:
            if any(k in q for k in ("销售", "金额", "GMV", "订单", "率", "数量")) and not any(
                t in q for t in ("年", "月", "周", "日", "季度", "202", "去年", "本月", "上周")
            ):
                slots.append("time_range")
            if len(q) <= 8 or q in {"查一下", "看看", "分析", "帮我看看情况", "怎么样"}:
                if "metric" not in slots:
                    slots.append("metric")
                if "time_range" not in slots:
                    slots.append("time_range")

        space = (req.space_id or "").strip()
        is_tq = space == "tech_quality"
        is_ecom = space == "ecommerce"

        slot_labels = {
            "time_range": "时间范围（如最近7天、上月、2024年）",
            "metric": (
                "指标/度量（如扫描失败率、失败次数）"
                if is_tq
                else "指标/度量（如订单总金额、订单数）"
                if is_ecom
                else "指标/度量"
            ),
            "dimension": (
                "拆分维度（如错误类型、状态码）"
                if is_tq
                else "拆分维度（如地区、渠道）"
                if is_ecom
                else "拆分维度"
            ),
            "subject": "分析对象/表",
        }
        hints = [slot_labels.get(s, s) for s in slots]
        # friendlier body
        body = (msg or "").strip()
        if body.startswith("请指定") or "未能确定" in body or "缺少" in body or len(body) < 12:
            lead = "还差一些信息才能准确查询。"
            if hints:
                body = lead + "请补充：" + "、".join(hints) + "。"
            else:
                body = lead + "请说明要看的指标和时间范围。"
        examples = []
        if "time_range" in slots and "metric" in slots:
            if is_tq:
                examples = ["最近7天扫描失败率", "近30天 api_logs 错误请求有多少", "本月扫描记录有多少"]
            elif is_ecom:
                examples = ["2024年订单总金额是多少", "上月按地区的销售额", "近30天订单数趋势"]
            else:
                examples = ["最近7天有多少条记录", "本月按状态拆开", "看一下主要业务表"]
        elif "time_range" in slots:
            examples = ["最近7天", "最近30天", "本月"]
        elif "metric" in slots:
            if is_tq:
                examples = ["扫描失败率", "失败扫描数", "API 错误请求数"]
            elif is_ecom:
                examples = ["订单总金额", "订单数量", "客单价"]
            else:
                examples = ["总数", "失败率", "按状态拆开"]
        else:
            if is_tq:
                examples = ["最近7天扫描失败率", "这个库有什么表", "看看最近扫描记录"]
            elif is_ecom:
                examples = ["2024年订单总金额是多少", "这个库有什么表"]
            else:
                examples = ["这个库有什么表", "最近7天有多少条记录"]

        if examples and "例如" not in body:
            body = body + "\n\n可以这样问：\n· " + "\n· ".join(examples[:3])

        cands = list(candidates or [])
        if not cands:
            if "time_range" in slots and "metric" not in slots:
                cands = [
                    {"key": "last_7d", "name": "最近7天", "description": "相对时间", "default_query_type": "metric", "default_time_range": "last_7d"},
                    {"key": "last_30d", "name": "最近30天", "description": "相对时间", "default_query_type": "metric", "default_time_range": "last_30d"},
                    {"key": "this_month", "name": "本月", "description": "自然月", "default_query_type": "metric", "default_time_range": "this_month"},
                ]
            elif is_tq:
                cands = [
                    {"key": "scan_fail_rate", "name": "扫描失败率", "description": "scan_records 失败占比", "default_query_type": "metric", "default_time_range": ""},
                    {"key": "scan_fail_count", "name": "失败扫描数", "description": "status=failed 计数", "default_query_type": "metric", "default_time_range": ""},
                    {"key": "api_errors", "name": "API 错误请求", "description": "api_logs 异常", "default_query_type": "metric", "default_time_range": ""},
                ]
            elif is_ecom:
                cands = [
                    {"key": "order_amount_sum", "name": "订单总金额", "description": "对金额字段求和", "default_query_type": "metric", "default_time_range": ""},
                    {"key": "order_count", "name": "订单数量", "description": "统计订单行数", "default_query_type": "metric", "default_time_range": ""},
                    {"key": "by_region", "name": "按地区拆开", "description": "在已有指标上按地区分组", "default_query_type": "breakdown", "default_time_range": ""},
                ]
            else:
                cands = [
                    {"key": "last_7d", "name": "最近7天", "description": "相对时间", "default_query_type": "metric", "default_time_range": "last_7d"},
                    {"key": "schema", "name": "这个库有什么表", "description": "库表导览", "default_query_type": "schema", "default_time_range": ""},
                ]

        tr = TurnResult(
            response_type="clarification",
            message=body,
            answer=body,
            terminal_status="INVALID_REQUEST",
            stop_reason=stop_reason,
            kernel_route=self.kernel_route,
            candidates=cands,
            clarify_slots=slots,
            ux_hints=hints or ["补充指标或时间后再问"],
            next_actions=[{"id": f"ex_{i}", "label": ex} for i, ex in enumerate(examples[:3])],
            evidence={
                "clarify_slots": slots,
                "ux_hints": hints,
                "examples": examples[:3],
            },
            rows=[],
            rows_count=0,
        )
        return self._attach_supervisor(tr, decision) if decision else tr

    def _chat_help_result(self, req: TurnRequest, decision: SupervisorDecision) -> TurnResult:
        intent = decision.intent
        if intent == "help":
            msg = (
                "我是 DataPilot 数据分析助手。你可以：\n"
                "· 用自然语言查指标（例：2024 年订单总金额）\n"
                "· 追问拆维（例：按地区拆开）\n"
                "· 在有查询结果后请求深度诊断报告\n"
                "当前环境为只读分析，不会执行修改/删除。"
            )
            tr = TurnResult(
                response_type="help",
                message=msg,
                answer=msg,
                terminal_status="",
                stop_reason="help",
                kernel_route=self.kernel_route,
                ux_hints=["先确保空间已 Profile", "查数成功后再诊断"],
                next_actions=[
                    {"id": "ex_q", "label": "2024年订单总金额是多少"},
                    {"id": "ex_schema", "label": "这个库有什么表"},
                ],
                rows=[],
                rows_count=0,
            )
            return self._attach_supervisor(tr, decision)
        if intent == "clarification":
            raw = decision.resolved_question or "请补充指标、时间范围或说明要继续上一轮的哪个维度。"
            # avoid dumping internal resolved template as-is when it looks like a system guess
            if raw.startswith("请说明要查询") or "未能确定" in raw:
                raw = "还需要更具体一点。"
            return self._enrich_clarification(req, raw, decision=decision, stop_reason="clarification")

        msg = "你好！我是 DataPilot。可以直接问数据问题，例如「2024 年订单总金额」。"
        tr = TurnResult(
            response_type="answer",
            message=msg,
            answer=msg,
            terminal_status="",
            stop_reason="chat",
            kernel_route=self.kernel_route,
            rows=[],
            rows_count=0,
            next_actions=[{"id": "ex_q", "label": "2024年订单总金额是多少"}],
        )
        if "你好" in (req.question or "") or len((req.question or "").strip()) <= 6:
            tr.response_type = "chat" if tr.response_type == "answer" else tr.response_type
            # keep frontend-friendly: many UIs treat chat as answer
            if tr.response_type == "chat":
                tr.response_type = "answer"
        return self._attach_supervisor(tr, decision)

    async def _dispatch_v2(self, req: TurnRequest, cat, decision: SupervisorDecision) -> TurnResult:
        """Dispatch by TaskSpec only — no parallel keyword routers."""
        intent = decision.intent
        task_type = decision.task_spec.task_type

        if intent in {"chat", "help", "clarification"}:
            return self._chat_help_result(req, decision)

        if intent == "summary_cite" or task_type == "summary_cite":
            cited = self._cite_diagnosis_summary(req)
            if cited is not None:
                return self._attach_supervisor(cited, decision)
            # no summary yet → clarify
            msg = "当前会话还没有可引用的诊断摘要。请先完成有数据的查询并生成诊断报告。"
            tr = TurnResult(
                response_type="clarification",
                message=msg,
                answer=msg,
                terminal_status="INVALID_REQUEST",
                stop_reason="no_summary",
                kernel_route=self.kernel_route,
            )
            return self._attach_supervisor(tr, decision)

        if intent == "diagnosis" or task_type == "diagnosis_playbook":
            tr = await self._run_v2_diagnosis(req, cat)
            return self._attach_supervisor(tr, decision)

        if intent in {"data_query", "follow_up", "schema_understanding", "table_query"}:
            if cat is None or not analysis_allowed(cat):
                return self._catalog_not_ready_result(req, decision)
            # schema_understanding: catalog-driven OP briefing (not bare table names)
            if intent == "schema_understanding" and task_type == "schema_inventory":
                tr = self._schema_inventory_from_catalog(req, cat, decision)
                return self._attach_supervisor(tr, decision)
            # use resolved_question for analysis
            resolved = decision.resolved_question or req.question
            # temporarily swap question for planner
            orig_q = req.question
            try:
                req.question = resolved
                tr = self._run_v2_analysis(
                    req, cat, decision=decision, original_question=orig_q
                )
            finally:
                req.question = orig_q
            return self._attach_supervisor(tr, decision)

        # unknown intent → clarify (should be rare; L2 already maps closed set)
        msg = "暂未识别意图，请说明要查询的指标与时间范围。"
        tr = TurnResult(
            response_type="clarification",
            message=msg,
            answer=msg,
            terminal_status="INVALID_REQUEST",
            stop_reason="unknown_intent",
            kernel_route=self.kernel_route,
        )
        return self._attach_supervisor(tr, decision)

    def _persist_turn(self, req: TurnRequest, tr: TurnResult) -> None:
        """Write user+assistant messages to MySQL under user/session/space isolation."""
        if not req.session_id:
            return
        try:
            from app.services.persistence import persist_turn_result

            persist_turn_result(
                session_id=req.session_id,
                user_id=int(req.user_id),
                space_id=str(req.space_id or ""),
                question=req.question or "",
                result=tr,
            )
        except Exception:
            # Persistence must not break the user-facing turn.
            pass

    async def handle_turn(self, req: TurnRequest) -> TurnResult:
        """Blocking JSON path — always returns a final TurnResult (incl. diagnosis)."""
        # L0 write refuse — before graph / LLM / schema (both kernel routes)
        if detect_write_intent(req.question).refuse:
            tr = _l0_refuse_result(req, kernel_route=self.kernel_route)
            self._persist_turn(req, tr)
            return tr

        pre = self._run_precheck(req)
        if pre is not None and not pre.ok:
            tr = _blocked_for_pilot_result(req, pre, kernel_route=self.kernel_route)
            self._persist_turn(req, tr)
            return tr

        cat = self._load_catalog_for_turn(req)
        blocked = self._catalog_block_if_needed(req, cat)
        if blocked is not None:
            self._persist_turn(req, blocked)
            return blocked

        # R5.5a: single SupervisorDecision control plane on v2
        if self.kernel_route == KERNEL_V2:
            resumed_question, pending_response = self._resolve_pending_time(req)
            if pending_response is not None:
                self._persist_turn(req, pending_response)
                return pending_response
            original_reply = req.question
            if resumed_question:
                req.question = resumed_question
            try:
                decision = self._run_supervisor(req)
                paused = self._pause_for_time_confirmation(req, decision)
                tr = paused if paused is not None else await self._dispatch_v2(req, cat, decision)
            finally:
                req.question = original_reply
            if resumed_question and tr.terminal_status in {
                QueryOutcomeStatus.SUCCESS_WITH_DATA.value,
                QueryOutcomeStatus.SUCCESS_EMPTY.value,
            }:
                self._pending_query_repo_or_default().delete(
                    req.session_id, req.user_id, req.space_id
                )
            self._persist_turn(req, tr)
            return tr

        graph = self._graph_or_default()
        state = _state_from_request(req)
        result = await graph.ainvoke(state)
        if not isinstance(result, dict):
            result = {}

        # Stamp kernel_route into graph trace
        if isinstance(result.get("trace"), list):
            result["trace"] = annotate_trace_kernel(result.get("trace"), self.kernel_route)

        rtype = result.get("response_type") or "answer"
        if rtype == "deep_diagnosis" or result.get("route") == "deep_diagnosis":
            tr = await self._run_deep_diagnosis_final(req, result)
        else:
            tr = _result_from_graph_dict(result, kernel_route=self.kernel_route)

        if cat is not None:
            tr.evidence = {
                **(tr.evidence if isinstance(tr.evidence, dict) else {}),
                "catalog_version": cat.version,
                "schema_fingerprint": cat.schema_fingerprint,
                "catalog_readiness": cat.readiness.to_dict() if cat.readiness else None,
                "catalog_source": "repository",
            }
            if cat.readiness and str(getattr(cat.readiness.status, "value", cat.readiness.status)) == "DEGRADED":
                # surface limited capabilities without blocking
                limits = list(cat.readiness.limited_capabilities or [])
                if limits and tr.message and "受限" not in tr.message:
                    tr.message = (tr.message or "") + f"（Catalog DEGRADED，受限: {', '.join(limits[:4])}）"
                    tr.answer = tr.message
        self._persist_turn(req, tr)
        return tr

    def _run_v2_analysis(
        self,
        req: TurnRequest,
        cat,
        *,
        decision: SupervisorDecision | None = None,
        original_question: str | None = None,
    ) -> TurnResult:
        """R3+R4: controlled loop with ActiveAnalysisState load/save + live MySQL."""
        try:
            from app.services.authorized_connection_service import (
                ConnectionUnavailable,
                resolve_authorized_connection,
            )

            conn = resolve_authorized_connection(
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
                kernel_route=self.kernel_route,
                rows=[],
                rows_count=0,
            )

        state_repo = self._state_repo_or_default()
        prior = None
        state_invalidated = False
        if req.session_id:
            prior = state_repo.load(
                req.session_id,
                req.user_id,
                req.space_id,
                catalog_fingerprint=cat.schema_fingerprint or None,
            )
            # If file exists but fingerprint rejected → mark invalidated
            if prior is None and state_repo.exists(req.session_id, req.user_id, req.space_id):
                # try load without fp check to detect stale catalog
                raw = state_repo.load(req.session_id, req.user_id, req.space_id)
                if raw is not None:
                    # exists path already returned None only on fp mismatch when fp passed;
                    # re-check explicitly
                    stored_fp = getattr(raw, "catalog_fingerprint", "") or ""
                    if stored_fp and cat.schema_fingerprint and stored_fp != cat.schema_fingerprint:
                        state_invalidated = True
                        prior = None
                    else:
                        prior = raw
                else:
                    # fingerprint gate inside load returned None — confirm stale
                    try:
                        import json
                        p = state_repo._path(req.session_id, req.user_id, req.space_id)
                        data = json.loads(p.read_text(encoding="utf-8"))
                        stored_fp = str(data.get("catalog_fingerprint") or "")
                        if stored_fp and cat.schema_fingerprint and stored_fp != cat.schema_fingerprint:
                            state_invalidated = True
                    except Exception:
                        pass

        semantic_model = get_semantic_model_service().load_or_build(req.space_id, cat)
        prior_spec = prior.to_spec(original_question=req.question).to_dict() if prior else None
        parsed = SemanticParser().parse(
            req.question,
            semantic_model,
            previous_spec=prior_spec,
            supervisor_params=(decision.task_spec.params if decision else None),
            original_question=original_question,
        )
        if not parsed.ok or parsed.query is None:
            tr = self._enrich_clarification(
                req,
                "我还不能把这个问题可靠地映射到当前空间已定义的业务指标。请补充指标、对象或时间范围。",
                slots=["metric"],
                stop_reason="semantic_parse",
            )
            tr.evidence = {
                **(tr.evidence if isinstance(tr.evidence, dict) else {}),
                "semantic_parser_error": parsed.error,
                "semantic_model_version": semantic_model.version,
            }
            return tr
        if parsed.query.operation == "clarify" or parsed.query.unresolved_slots:
            return self._enrich_clarification(
                req,
                "还需要补充信息才能准确查询。",
                slots=list(parsed.query.unresolved_slots or ["metric"]),
                stop_reason="semantic_clarify",
            )
        try:
            semantic_spec = build_analysis_spec(
                parsed.query, semantic_model, cat, original_question=req.question
            )
        except ValueError:
            return self._enrich_clarification(
                req,
                "这个问题中的指标或维度还没有在当前空间确认。",
                slots=["metric"],
                stop_reason="semantic_validation",
            )

        # Business-name grounding: auto-sync bounded distinct values (channels,
        # suppliers, products) into the system DB, then repair a model-shortened
        # name only if the user's original wording has exactly one full match.
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
            values = load_entity_values(
                req.space_id, field_by_dimension.keys(), field_by_dimension=field_by_dimension
            )
            resolution = resolve_filter_values(
                semantic_spec.filters, original_question or req.question, values
            )
            if resolution.ambiguities:
                return self._enrich_clarification(
                    req,
                    "找到了多个可能的业务名称，请补充更完整的渠道、供应商或产品名称。",
                    slots=["subject"],
                    stop_reason="entity_ambiguous",
                )
            semantic_spec.filters = resolution.filters
        except Exception:
            # Dictionary sync is an accuracy enhancement. Its temporary failure must
            # not block a safe query that already has a complete semantic plan.
            pass

        # Final semantic guard: a SQL-safe plan is not necessarily an answer to
        # the user's question.  Reject before compilation if an explicit model
        # dimension, exact business name, or time range disappeared.
        try:
            from app.agents.semantic_coverage import validate_semantic_coverage

            coverage = validate_semantic_coverage(
                original_question or req.question,
                semantic_spec,
                semantic_model,
                values if "values" in locals() else [],
            )
            if not coverage.ok:
                tr = self._enrich_clarification(
                    req,
                    "我识别到的问题条件没有完整进入查询计划，为避免返回错误数据，请换一种说法或稍后重试。",
                    slots=["query_condition"],
                    stop_reason="semantic_coverage",
                )
                tr.evidence = {
                    **(tr.evidence if isinstance(tr.evidence, dict) else {}),
                    "semantic_coverage_errors": coverage.errors,
                    "semantic_model_version": semantic_model.version,
                }
                return tr
        except Exception:
            # If the guard itself is unavailable, preserve the established parser
            # validation path rather than turning a safe request into an outage.
            pass

        loop = run_controlled_turn(
            req.question,
            cat,
            state=prior,
            mysql_connection=conn if (conn and conn.get("database")) else None,
            session_id=req.session_id or "",
            user_id=req.user_id,
            space_id=req.space_id,
            allow_heavy=False,
            prebuilt_spec=semantic_spec,
            semantic_followup=parsed.query.operation == "modify_query",
        )

        # Persist state after successful answer / query with state
        if loop.state is not None and req.session_id and loop.action in {
            "answer", "query", "stop_success", "stop_empty"
        }:
            try:
                if not loop.state.catalog_fingerprint:
                    loop.state.catalog_fingerprint = cat.schema_fingerprint or ""
                if not loop.state.catalog_version:
                    loop.state.catalog_version = str(getattr(cat, "version", "") or "")
                state_repo.save(loop.state)
            except Exception:
                pass

        # Map controlled loop → product TurnResult
        if loop.action == "clarify":
            tr = self._enrich_clarification(
                req,
                loop.answer_text or "请补充信息",
                slots=list(loop.clarify_slots or []),
                stop_reason="clarify",
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
                kernel_route=self.kernel_route,
                sql=loop.sql or "",
                analysis_spec=loop.spec.to_dict() if loop.spec else None,
                rows=[],
                rows_count=0,
            )
            if term == "SQL_REJECTED":
                tr = _attach_outcome(tr, QueryOutcome.sql_rejected(message=msg, sql=loop.sql or ""))
        else:
            outcome = loop.outcome
            # duplicate reuse may have no fresh outcome — use state preview
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
                kernel_route=self.kernel_route,
                analysis_spec=loop.spec.to_dict() if loop.spec else None,
                query_outcome=outcome.to_public_dict() if outcome else None,
                evidence=loop.evidence.to_dict() if loop.evidence else None,
                active_state_version=int(getattr(loop.state, "version", 0) or 0) if loop.state else None,
            )
            if outcome:
                tr = _attach_outcome(tr, outcome)

        tr.trace = annotate_trace_kernel(
            [
                {
                    "node": "controlled_loop_v2",
                    "status": "done",
                    "output": {
                        "action": loop.action,
                        "latency_ms": loop.latency_ms,
                        "clarify_slots": list(loop.clarify_slots or []),
                        "is_followup": loop.is_followup,
                        "duplicate_skipped": loop.duplicate_skipped,
                        "patch_ops": list(loop.patch_ops or []),
                        "agents_called": list(loop.agents_called or []),
                        "action_trace": list(loop.action_trace or []),
                    },
                }
            ],
            self.kernel_route,
        )
        base_ev = tr.evidence if isinstance(tr.evidence, dict) else {}
        tr.evidence = {
            **base_ev,
            "catalog_version": cat.version,
            "schema_fingerprint": cat.schema_fingerprint,
            "catalog_readiness": cat.readiness.to_dict() if cat.readiness else None,
            "catalog_source": "repository",
            "pipeline": "controlled_loop_v2",
            "duplicate_skipped": bool(loop.duplicate_skipped),
            "is_followup": bool(loop.is_followup),
            "agents_called": list(loop.agents_called or []),
            "patch_ops": list(loop.patch_ops or []),
            "state_invalidated": state_invalidated,
            "semantic_model_version": semantic_model.version,
            "semantic_model_provenance": semantic_model.provenance,
            "semantic_parser": True,
            "active_state_version": tr.active_state_version,
        }
        if cat.readiness and str(getattr(cat.readiness.status, "value", cat.readiness.status)) == "DEGRADED":
            limits = list(cat.readiness.limited_capabilities or [])
            if limits and tr.message and "受限" not in (tr.message or ""):
                tr.message = (tr.message or "") + f"（Catalog DEGRADED，受限: {', '.join(limits[:4])}）"
                tr.answer = tr.message
        return tr

    def _seed_from_active_state(self, req: TurnRequest) -> dict | None:
        """Build diagnosis seed from ActiveAnalysisState (R4/R5)."""
        if not req.session_id:
            return None
        try:
            st = self._state_repo_or_default().load(req.session_id, req.user_id, req.space_id)
        except Exception:
            st = None
        if st is None:
            return None
        rows = list(st.last_result_preview or [])
        if not rows and not st.last_sql:
            return None
        cols = list(rows[0].keys()) if rows else []
        return {
            "columns": cols,
            "rows": preview_rows(rows, 100),
            "rows_count": len(rows),
            "sql": st.last_sql or "",
            "query_outcome": {
                "status": "SUCCESS_WITH_DATA" if rows else "SUCCESS_EMPTY",
                "rows_count": len(rows),
            },
        }

    def _cite_diagnosis_summary(self, req: TurnRequest) -> TurnResult | None:
        if not req.session_id:
            return None
        try:
            summary = self._diagnosis_repo_or_default().load(
                req.session_id, req.user_id, req.space_id
            )
        except Exception:
            summary = None
        if summary is None:
            return None
        msg = summary.answer_about(req.question)
        return TurnResult(
            response_type="answer",
            message=msg,
            answer=msg,
            terminal_status="SUCCESS_WITH_DATA" if summary.approved else "",
            stop_reason="cite_summary",
            kernel_route=self.kernel_route,
            evidence={
                "from_diagnosis_summary": True,
                "diagnosis_approved": bool(summary.approved),
                "one_liner": summary.one_liner,
                "next_steps": list(summary.next_steps or []),
                "pipeline": "diagnosis_summary_cite",
            },
        )

    async def _run_v2_diagnosis(self, req: TurnRequest, cat) -> TurnResult:
        """R5: admission → evidence diagnosis pipeline → summary writeback."""
        import time as _time

        t0 = _time.perf_counter()
        # Prefer ActiveAnalysisState seed; fallback to chat message meta
        seed = self._seed_from_active_state(req)
        if seed is None:
            seed = await self._load_seed_query(req)

        from app.agents.diagnosis_admission import admit_diagnosis, outcome_from_seed
        from app.agents.dispatcher_agent_call import dispatcher_agent_call
        from app.agents.diagnosis_pipeline import run_evidence_diagnosis
        from app.agents.diagnosis_summary import bundle_to_dict

        outcome = outcome_from_seed(seed)
        admission = admit_diagnosis(outcome, explicit_intent=True)
        if not admission.allowed:
            elapsed_ms = (_time.perf_counter() - t0) * 1000
            missing = (
                "blocked_status" in admission.reason
                or "missing" in admission.reason.lower()
                or admission.outcome_status in {"", "MISSING"}
            )
            if missing:
                msg = (
                    "还不能启动深度诊断：当前会话里没有可用的查询结果。\n\n"
                    "深度诊断需要先有一次成功的查数（状态为 SUCCESS_WITH_DATA）。\n"
                    "建议先问：\n"
                    "· 2024年订单总金额是多少\n"
                    "· 或任意你关心的指标\n\n"
                    "有结果后再说「请做深度诊断生成报告」。"
                )
            else:
                msg = (
                    f"诊断未准入：{admission.reason}\n\n"
                    "请先完成有数据的查询，或检查是否权限/SQL 被拒绝。"
                )
            # Normalize terminal: missing seed → SUCCESS_EMPTY; preserve deny statuses
            term = admission.outcome_status or QueryOutcomeStatus.SUCCESS_EMPTY.value
            if term in {"", "MISSING"}:
                term = QueryOutcomeStatus.SUCCESS_EMPTY.value
            if term == QueryOutcomeStatus.PERMISSION_DENIED.value:
                qo = outcome or QueryOutcome.permission_denied(message=msg)
            elif term == QueryOutcomeStatus.SQL_REJECTED.value:
                qo = outcome or QueryOutcome.sql_rejected(message=msg)
            else:
                qo = outcome or QueryOutcome.success_empty(message=msg)
            return TurnResult(
                # Use clarification so UI shows guided card, not a fake diagnosis shell
                response_type="clarification" if missing else "error",
                message=msg,
                answer=msg,
                terminal_status=term,
                stop_reason="admission_denied",
                kernel_route=self.kernel_route,
                rows=[],
                rows_count=0,
                ux_hints=["先完成有结果的查数", "再请求深度诊断"],
                next_actions=[
                    {"id": "ex_q", "label": "2024年订单总金额是多少"},
                    {"id": "ex_diag", "label": "请做深度诊断生成报告"},
                ],
                evidence={
                    "pipeline": "diagnosis_v2",
                    "admission": admission.reason,
                    "agents_called": [],
                    "enter_diagnosis_count": 0,
                    "latency_ms": elapsed_ms,
                    "diagnosis_approved": False,
                },
                query_outcome=qo.to_public_dict() if hasattr(qo, "to_public_dict") else None,
            )

        task_id = f"diag_{req.session_id or 'local'}_{int(t0)}"
        # R5.5b: production default = Dispatcher → Harness; never default to deterministic.
        agent_call = self._diagnosis_agent_call or dispatcher_agent_call
        using_component = getattr(agent_call, "__module__", "").endswith(
            "diagnosis_agents_deterministic"
        ) or getattr(agent_call, "EVIDENCE_CLASS", "") == "component_only"
        result = await run_evidence_diagnosis(
            question=req.question,
            seed_query=seed,
            explicit_intent=True,
            agent_call=agent_call,
            export_formats=(),  # export only after approved via separate path
            session_id=req.session_id or "",
            task_id=task_id,
            user_id=req.user_id,
            space_id=req.space_id,
        )

        # Persist summary for session cite / refresh
        if result.summary is not None and req.session_id:
            try:
                self._diagnosis_repo_or_default().save(
                    result.summary,
                    user_id=req.user_id,
                    space_id=req.space_id,
                    extra={
                        "approved": result.approved,
                        "agents_called": list(result.agents_called or []),
                        "report": result.report_payload,
                        "review": result.review_payload,
                    },
                )
            except Exception:
                pass
            # also stamp on ActiveAnalysisState if present
            try:
                st = self._state_repo_or_default().load(req.session_id, req.user_id, req.space_id)
                if st is not None:
                    st.approved_report_id = (
                        result.summary.report_ref if result.approved else None
                    )
                    self._state_repo_or_default().save(st)
            except Exception:
                pass

        elapsed_ms = (_time.perf_counter() - t0) * 1000
        msg = result.message or (
            result.summary.one_liner if result.summary else "诊断完成"
        )
        if result.approved and result.summary and result.summary.next_steps:
            msg = msg + "\n下一步：" + "；".join(result.summary.next_steps[:3])

        artifacts = []
        if result.report_payload:
            artifacts.append(
                {
                    "type": "ReportDocument",
                    "payload": result.report_payload,
                    "status": "approved" if result.approved else "draft",
                }
            )
        if result.review_payload:
            artifacts.append(
                {
                    "type": "ReviewResult",
                    "payload": result.review_payload,
                    "status": "done",
                }
            )
        if result.summary:
            artifacts.append(
                {
                    "type": "DiagnosisSummary",
                    "payload": bundle_to_dict(result.summary),
                    "status": "done",
                }
            )

        tr = TurnResult(
            response_type="deep_diagnosis",
            message=msg,
            answer=msg,
            terminal_status=(
                "SUCCESS_WITH_DATA" if result.approved else (admission.outcome_status or "")
            ),
            stop_reason=result.stop_reason or ("stop_ok" if result.approved else "stop_rejected"),
            sql=str((seed or {}).get("sql") or ""),
            columns=list((seed or {}).get("columns") or []),
            rows=list((seed or {}).get("rows") or []),
            rows_count=int((seed or {}).get("rows_count") or 0),
            kernel_route=self.kernel_route,
            artifacts=artifacts,
            task_id=task_id,
            analysis_spec=None,
            evidence={
                "pipeline": "diagnosis_v2",
                "diagnosis_approved": bool(result.approved),
                "agents_called": list(result.agents_called or []),
                "gap_duplicate_fills": result.gap_duplicate_fills,
                "unfounded_causal_count": result.unfounded_causal_count,
                "key_claim_citation_rate": result.key_claim_citation_rate,
                "one_liner": result.summary.one_liner if result.summary else "",
                "next_steps": list(result.summary.next_steps or []) if result.summary else [],
                "latency_ms": elapsed_ms,
                "catalog_version": getattr(cat, "version", None) if cat else None,
                "schema_fingerprint": getattr(cat, "schema_fingerprint", None) if cat else None,
                "agent_transport": (
                    "component_only" if using_component else "a2a_dispatcher"
                ),
                "evidence_class": (
                    "component_only" if using_component else "production_dispatcher"
                ),
            },
        )
        if outcome:
            tr = _attach_outcome(tr, outcome)
        tr.trace = annotate_trace_kernel(
            [
                {
                    "node": "diagnosis_pipeline_v2",
                    "status": "done",
                    "output": {
                        "admitted": result.admitted,
                        "approved": result.approved,
                        "agents_called": list(result.agents_called or []),
                        "stop_reason": result.stop_reason,
                        "latency_ms": elapsed_ms,
                        "agent_transport": tr.evidence.get("agent_transport") if isinstance(tr.evidence, dict) else None,
                    },
                }
            ],
            self.kernel_route,
        )
        return tr

    async def handle_turn_stream(
        self, req: TurnRequest
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yield (event_name, data) including exactly one terminal complete payload dict.

        Does not format SSE text — adapter + SseTerminalGuard do that.
        """
        # V2 has one product workflow.  JSON and SSE must not independently
        # implement Supervisor, pending confirmation, dispatch or persistence.
        # SSE is only a transport adapter around the canonical blocking result.
        if self.kernel_route == KERNEL_V2:
            yield "run_started", {
                "session_id": req.session_id or "",
                "space_id": req.space_id or "",
            }
            tr = await self.handle_turn(req)
            if isinstance(tr.task_spec, dict) and tr.task_spec.get("task_type") == "diagnosis_playbook":
                yield "agent_lifecycle", {
                    "agent": "supervisor",
                    "status": "completed",
                    "task_id": tr.task_id or "",
                    "session_id": req.session_id or "",
                    "phase": "diagnosis_dispatch",
                }
                if tr.stop_reason and tr.stop_reason not in {"stop_ok", ""}:
                    yield "diagnosis_stopped", {
                        "stop_reason": tr.stop_reason,
                        "task_id": tr.task_id or "",
                    }
            yield "complete", tr.to_public_dict()
            return

        if detect_write_intent(req.question).refuse:
            tr = _l0_refuse_result(req, kernel_route=self.kernel_route)
            self._persist_turn(req, tr)
            yield "complete", tr.to_public_dict()
            return

        pre = self._run_precheck(req)
        if pre is not None and not pre.ok:
            tr = _blocked_for_pilot_result(req, pre, kernel_route=self.kernel_route)
            self._persist_turn(req, tr)
            yield "complete", tr.to_public_dict()
            return

        cat = self._load_catalog_for_turn(req)
        blocked = self._catalog_block_if_needed(req, cat)
        if blocked is not None:
            self._persist_turn(req, blocked)
            yield "complete", blocked.to_public_dict()
            return

        graph = self._graph_or_default()
        state = _state_from_request(req)
        accumulated: dict[str, Any] = {}
        sent_count = 0

        async for output in graph.astream(state):
            if not isinstance(output, dict):
                continue
            for _node_name, node_output in output.items():
                if not node_output or not isinstance(node_output, dict):
                    continue
                accumulated.update(
                    {
                        k: v
                        for k, v in node_output.items()
                        if v is not None and v != "" and v != [] and v != {}
                    }
                )
                events = node_output.get("events") or []
                for evt in events[sent_count:]:
                    if not isinstance(evt, dict):
                        continue
                    name = evt.get("event") or "message"
                    # Internal nodes must not emit external terminal complete
                    if name == "complete":
                        name = "lifecycle_complete"
                    data = evt.get("data") if isinstance(evt.get("data"), dict) else {}
                    yield name, data
                sent_count = len(events)

        response_type = accumulated.get("response_type", "answer")
        if response_type == "deep_diagnosis" or accumulated.get("route") == "deep_diagnosis":
            async for item in self._stream_deep_diagnosis(req, accumulated):
                yield item
            return

        answer_text, intermediates = await self._maybe_stream_answer(req, accumulated)
        for ev_name, ev_data in intermediates:
            yield ev_name, ev_data

        if isinstance(accumulated.get("trace"), list):
            accumulated["trace"] = annotate_trace_kernel(accumulated.get("trace"), self.kernel_route)
        else:
            accumulated["trace"] = annotate_trace_kernel([], self.kernel_route)

        complete = _result_from_graph_dict(
            accumulated,
            message_override=answer_text if answer_text is not None else accumulated.get("message", ""),
            kernel_route=self.kernel_route,
        )
        if cat is not None:
            complete.evidence = {
                **(complete.evidence if isinstance(complete.evidence, dict) else {}),
                "catalog_version": cat.version,
                "schema_fingerprint": cat.schema_fingerprint,
                "catalog_readiness": cat.readiness.to_dict() if cat.readiness else None,
                "catalog_source": "repository",
            }
        self._persist_turn(req, complete)
        yield "complete", complete.to_public_dict()

    async def _maybe_stream_answer(
        self, req: TurnRequest, accumulated: dict
    ) -> tuple[str, list[tuple[str, dict]]]:
        """Optional LLM summary for answer type. Returns (text, intermediate events)."""
        has_plan = bool(accumulated.get("plan"))
        response_type = accumulated.get("response_type", "answer")
        has_data = response_type == "answer" and bool(accumulated.get("rows"))
        intermediates: list[tuple[str, dict]] = []

        if not _should_generate_analysis_answer(response_type, has_data, has_plan):
            return str(accumulated.get("message") or ""), intermediates

        history_lines: list[str] = []
        if req.session_id:
            try:
                from app.services.persistence import load_recent_messages

                history = load_recent_messages(
                    req.session_id, limit=6, user_id=req.user_id, space_id=req.space_id
                )
                for msg in history or []:
                    role_label = "用户" if msg.get("role") == "user" else "AI"
                    line = f"{role_label}: {msg.get('content')}"
                    if msg.get("role") == "assistant" and msg.get("meta"):
                        meta = msg["meta"]
                        if isinstance(meta, str):
                            import json as _json

                            meta = _json.loads(meta)
                        rows_count = meta.get("rows_count", 0)
                        if meta.get("sql"):
                            line += f" (执行了SQL，返回{rows_count}条数据)"
                    history_lines.append(line)
            except Exception:
                pass

        answer_text = ""
        try:
            client = _get_async_llm_client()
            intent = accumulated.get("intent")
            metric_name = ""
            if intent:
                if isinstance(intent, QueryIntent):
                    metric_name = intent.metric or ""
                elif isinstance(intent, dict):
                    metric_name = intent.get("metric", "") or ""

            rows = accumulated.get("rows") or []
            columns = accumulated.get("columns") or []
            data_summary = ""
            if rows and columns:
                preview = rows[:5]
                data_summary = "数据列: " + ", ".join(columns) + "\n"
                for i, row in enumerate(preview):
                    data_summary += (
                        f"第{i+1}行: "
                        + ", ".join(f"{c}={row.get(c)}" for c in columns)
                        + "\n"
                    )
                if len(rows) > 5:
                    data_summary += f"...共 {len(rows)} 行\n"
            elif columns:
                data_summary = f"查询执行成功，列: {', '.join(columns)}，但返回了 0 条数据。\n"

            prompt = "你是 DataPilot Agent 数据分析助手。\n\n"
            if history_lines:
                prompt += "历史对话:\n" + "\n".join(history_lines) + "\n\n"
            prompt += f"用户当前查询了指标 '{metric_name}'，查询结果如下：\n\n"
            prompt += f"{data_summary}\n"
            if not rows:
                prompt += (
                    "查询返回了 0 条数据。请结合用户的问题和历史对话，"
                    "分析可能的原因（如时间范围内无数据、筛选条件过严等），并给出建议。2-3句话即可。\n"
                )
            else:
                prompt += "请用简洁的中文总结查询结果（2-3句话），包含关键数字和趋势。不要重复原始数据。"

            intermediates.append(("answer_generating", {"text": "正在生成分析结论"}))
            async with client.messages.stream(
                model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                async for text in stream.text_stream:
                    answer_text += text
                    intermediates.append(("answer_chunk", {"text": text}))
        except Exception:
            answer_text = str(accumulated.get("message") or "")

        return answer_text, intermediates

    async def _load_seed_query(self, req: TurnRequest) -> Optional[dict]:
        if not req.session_id:
            return None
        try:
            from app.services.persistence import load_recent_messages

            history = load_recent_messages(
                req.session_id, limit=8, user_id=req.user_id, space_id=req.space_id
            )
            for msg in reversed(history or []):
                if msg.get("role") != "assistant":
                    continue
                meta = msg.get("meta") or {}
                if isinstance(meta, str):
                    import json as _json

                    meta = _json.loads(meta)
                rows = meta.get("rows") or []
                cols = meta.get("columns") or []
                if rows and cols:
                    return {
                        "columns": cols,
                        "rows": preview_rows(rows, 100),
                        "rows_count": meta.get("rows_count") or len(rows),
                        "sql": meta.get("sql") or "",
                    }
        except Exception:
            return None
        return None

    def _diagnosis_admission_block(
        self, req: TurnRequest, graph_result: dict, seed_query: dict | None
    ) -> TurnResult | None:
        """Block heavy diagnosis without SUCCESS_WITH_DATA (mode C) or on deny."""
        # Graph already permission-denied
        g_code = str(graph_result.get("error_code") or graph_result.get("terminal_status") or "").upper()
        g_msg = str(graph_result.get("message") or "")
        if g_code == "PERMISSION_DENIED" or ("无权" in g_msg and "权限" in g_msg):
            outcome = QueryOutcome.permission_denied(message=g_msg or "权限不足", trace_id=graph_result.get("trace_id"))
            return _attach_outcome(
                TurnResult(
                    response_type="error",
                    message=outcome.message,
                    answer=outcome.message,
                    trace_id=str(graph_result.get("trace_id") or ""),
                    kernel_route=self.kernel_route,
                    query_outcome=outcome.to_public_dict(),
                )
            )
        if not _seed_has_success_with_data(seed_query):
            msg = (
                "无法启动深度诊断：当前会话没有有效查询数据（需要 SUCCESS_WITH_DATA）。"
                "请先完成一次有结果的数据查询，再请求诊断报告。"
            )
            outcome = QueryOutcome.success_empty(
                message=msg,
                trace_id=graph_result.get("trace_id"),
            )
            return TurnResult(
                response_type="deep_diagnosis",
                message=msg,
                answer=msg,
                terminal_status=QueryOutcomeStatus.SUCCESS_EMPTY.value,
                stop_reason="stop_empty",
                trace_id=str(graph_result.get("trace_id") or ""),
                kernel_route=self.kernel_route,
                rows=[],
                rows_count=0,
                query_outcome=outcome.to_public_dict(),
                artifacts=[],
                task_id=None,
            )
        return None

    async def _run_deep_diagnosis_final(
        self, req: TurnRequest, graph_result: dict
    ) -> TurnResult:
        """JSON path: wait for diagnosis terminal (no mode-A pseudo start)."""
        from app.agents.deep_diagnosis import DiagnosisBudget, run_deep_diagnosis
        from app.agents.link_policy import build_diagnosis_terminal_message
        from app.services.agent_runtime_store import create_task, list_artifacts

        seed_query = await self._load_seed_query(req)
        blocked = self._diagnosis_admission_block(req, graph_result, seed_query)
        if blocked is not None:
            return blocked

        task = create_task(req.session_id, req.user_id, req.space_id, req.question)
        task.update(
            {
                "session_id": req.session_id,
                "user_id": req.user_id,
                "space_id": req.space_id,
                "question": graph_result.get("question") or req.question,
            }
        )
        budget = DiagnosisBudget()
        terminal_message = ""
        terminal_status = None
        stop_reason = None
        async for item in run_deep_diagnosis(
            task,
            req.user_role,
            seed_query=seed_query,
            export_formats=["pdf", "docx", "csv"],
            budget=budget,
        ):
            data = item.get("data") or {}
            if isinstance(data, dict):
                if data.get("message") and item.get("event") in (
                    "diagnosis_stopped",
                    "review_rejected",
                    "orchestration_done",
                    "budget_exhausted",
                ):
                    terminal_message = str(data.get("message") or "")
                    terminal_status = data.get("terminal_status") or terminal_status
                    stop_reason = data.get("stop_reason") or stop_reason

        artifacts = list_artifacts(task["id"], req.session_id, req.user_id, req.space_id)
        has_report = any(
            (a.get("type") == "ReportDocument") for a in (artifacts or []) if isinstance(a, dict)
        )
        review = next(
            (
                a
                for a in reversed(artifacts or [])
                if isinstance(a, dict) and a.get("type") == "ReviewResult"
            ),
            None,
        )
        review_payload = (review or {}).get("payload") or {}
        if not terminal_message:
            terminal_message = build_diagnosis_terminal_message(
                stop_reason=stop_reason or ("stop_ok" if has_report else "stop_empty"),
                outcome_status=terminal_status,
                review_approved=bool(review_payload.get("approved")),
                review_reasons=list(review_payload.get("reasons") or []),
                has_report=has_report,
            )
        if "编排已完成" in terminal_message and (
            not has_report or not review_payload.get("approved")
        ):
            terminal_message = build_diagnosis_terminal_message(
                stop_reason=stop_reason or "stop_rejected",
                outcome_status=terminal_status,
                review_approved=bool(review_payload.get("approved")),
                review_reasons=list(review_payload.get("reasons") or []),
                has_report=has_report,
            )

        # Message persistence is owned by handle_turn / handle_turn_stream
        # via _persist_turn (user × session × space isolation).
        return TurnResult(
            response_type="deep_diagnosis",
            message=terminal_message,
            answer=terminal_message,
            trace_id=str(graph_result.get("trace_id") or ""),
            terminal_status=str(terminal_status or ""),
            stop_reason=str(stop_reason or ""),
            sql=str((seed_query or {}).get("sql") or ""),
            columns=list((seed_query or {}).get("columns") or []),
            rows=list((seed_query or {}).get("rows") or []),
            rows_count=int((seed_query or {}).get("rows_count") or 0),
            artifacts=list(artifacts or []),
            task_id=task["id"],
            kernel_route=self.kernel_route,
            trace=list(graph_result.get("trace") or []),
        )

    async def _stream_deep_diagnosis(
        self, req: TurnRequest, accumulated: dict
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        from app.agents.deep_diagnosis import DiagnosisBudget, run_deep_diagnosis
        from app.agents.link_policy import build_diagnosis_terminal_message
        from app.services.agent_runtime_store import create_task, list_artifacts

        seed_query = await self._load_seed_query(req)
        blocked = self._diagnosis_admission_block(req, accumulated, seed_query)
        if blocked is not None:
            yield "complete", blocked.to_public_dict()
            return

        task = create_task(req.session_id, req.user_id, req.space_id, req.question)
        task.update(
            {
                "session_id": req.session_id,
                "user_id": req.user_id,
                "space_id": req.space_id,
                "question": accumulated.get("question") or req.question,
            }
        )
        yield (
            "task_created",
            {"task_id": task["id"], "mode": "deep_diagnosis"},
        )
        budget = DiagnosisBudget()
        terminal_message = ""
        terminal_status = None
        stop_reason = None
        async for item in run_deep_diagnosis(
            task,
            req.user_role,
            seed_query=seed_query,
            export_formats=["pdf", "docx", "csv"],
            budget=budget,
        ):
            data = item.get("data") or {}
            ev = item.get("event") or "message"
            if ev == "complete":
                ev = "lifecycle_complete"
            if isinstance(data, dict):
                if data.get("message") and item.get("event") in (
                    "diagnosis_stopped",
                    "review_rejected",
                    "orchestration_done",
                    "budget_exhausted",
                ):
                    terminal_message = str(data.get("message") or "")
                    terminal_status = data.get("terminal_status") or terminal_status
                    stop_reason = data.get("stop_reason") or stop_reason
            yield ev, data if isinstance(data, dict) else {}

        artifacts = list_artifacts(task["id"], req.session_id, req.user_id, req.space_id)
        has_report = any(
            (a.get("type") == "ReportDocument") for a in (artifacts or []) if isinstance(a, dict)
        )
        review = next(
            (
                a
                for a in reversed(artifacts or [])
                if isinstance(a, dict) and a.get("type") == "ReviewResult"
            ),
            None,
        )
        review_payload = (review or {}).get("payload") or {}
        if not terminal_message:
            terminal_message = build_diagnosis_terminal_message(
                stop_reason=stop_reason or ("stop_ok" if has_report else "stop_empty"),
                outcome_status=terminal_status,
                review_approved=bool(review_payload.get("approved")),
                review_reasons=list(review_payload.get("reasons") or []),
                has_report=has_report,
            )
        if "编排已完成" in terminal_message and (
            not has_report or not review_payload.get("approved")
        ):
            terminal_message = build_diagnosis_terminal_message(
                stop_reason=stop_reason or "stop_rejected",
                outcome_status=terminal_status,
                review_approved=bool(review_payload.get("approved")),
                review_reasons=list(review_payload.get("reasons") or []),
                has_report=has_report,
            )

        # Persistence happens once at stream completion via _persist_turn.
        tr = TurnResult(
            response_type="deep_diagnosis",
            message=terminal_message,
            answer=terminal_message,
            trace_id=str(accumulated.get("trace_id") or ""),
            terminal_status=str(terminal_status or ""),
            stop_reason=str(stop_reason or ""),
            sql=str((seed_query or {}).get("sql") or ""),
            columns=list((seed_query or {}).get("columns") or []),
            rows=list((seed_query or {}).get("rows") or []),
            rows_count=int((seed_query or {}).get("rows_count") or 0),
            artifacts=list(artifacts or []),
            task_id=task["id"],
            kernel_route=self.kernel_route,
            chart=None,
            candidates=[],
            trace=list(accumulated.get("trace") or []),
        )
        self._persist_turn(req, tr)
        yield "complete", tr.to_public_dict()


def get_analysis_service() -> AnalysisApplicationService:
    """Factory: resolve flags → kernel_route; precheck on each turn."""
    return AnalysisApplicationService(flags=default_flags())
