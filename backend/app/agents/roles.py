"""角色 Harness：统一走 execute(ctx) + 私有 AgentContextPackage。

dev_spec:
- 仅 Query 可使用 LangGraph/DB 工具
- Insight/Report/Review/Export 只消费受控工件与有界 payload
- 私有 working/experience 经 ctx.memory，强制 scrub，禁止 raw rows/凭据/CoT
- LLM 调用注入 package.as_model_bundle()（各自上下文，不共享聊天史）
"""

from __future__ import annotations

import json
import logging

from app.a2a.contracts import A2AMessage
from app.agents.harness import AgentHarness, AgentSkill, HarnessContext
from app.agents.model_adapter import ModelRequest, get_model_adapter, role_model_policy

logger = logging.getLogger(__name__)


def _model(ctx: HarnessContext):
    return ctx.get_model() or get_model_adapter()


class QueryHarness(AgentHarness):
    """R5.5b: v2 Query Kernel only — legacy Agent graph path removed."""

    def __init__(self):
        super().__init__(
            "query",
            AgentSkill(
                "safe-query-v2",
                frozenset({"v2_query_kernel"}),
                "QueryRequest",
                "QueryResult",
                procedure="analysis-spec-query-v2",
                policies=("read_only_db", "scoped_session", "no_legacy_graph"),
            ),
        )

    async def execute(self, ctx: HarnessContext) -> dict:
        ctx.tools.require("v2_query_kernel")
        m = ctx.message
        inputs = ctx.allowed_inputs
        question = inputs.get("question") or m.payload.get("question") or ""
        task_spec = inputs.get("task_spec") or m.payload.get("task_spec") or {}
        if not isinstance(task_spec, dict):
            task_spec = {}

        # Prefer GapCompiler / Supervisor rewritten question + structured hints
        if task_spec.get("question") and str(task_spec.get("question")).strip():
            question = str(task_spec["question"]).strip()
        if task_spec.get("task_type") in ("gap_fill_query", "gap_fill") or m.payload.get("compiled_gap"):
            dims = task_spec.get("dimensions") or task_spec.get("suggested_dimensions") or []
            tables = task_spec.get("tables") or task_spec.get("suggested_tables") or []
            metric = task_spec.get("metric") or ""
            hints = []
            if metric:
                hints.append(f"metric={metric}")
            if dims:
                hints.append("dimensions=" + ",".join(str(d) for d in dims[:6]))
            if tables:
                hints.append("tables=" + ",".join(str(t) for t in tables[:6]))
            if hints and all(h.split("=", 1)[0] not in question for h in hints):
                question = f"{question}\n【task_spec】{'；'.join(hints)}"

        prev = ctx.working_memory
        from app.agents.query_kernel import run_query_kernel

        # optional connection override from payload (tests)
        mysql_connection = m.payload.get("mysql_connection") if isinstance(m.payload, dict) else None
        kr = run_query_kernel(
            question,
            space_id=m.space_id or "",
            task_spec=task_spec,
            user_id=int(m.user_id or 0),
            session_id=m.session_id or "",
            mysql_connection=mysql_connection if isinstance(mysql_connection, dict) else None,
            execute=True,
            preview_limit=100,
        )
        payload = kr.to_query_payload(preview_limit=100)
        outcome = kr.outcome
        art_status = (
            "approved"
            if outcome and outcome.status.value == "SUCCESS_WITH_DATA"
            else ("failed" if outcome and outcome.status.value not in {"SUCCESS_EMPTY", "INVALID_REQUEST"} else "approved")
        )
        aid = ctx.artifacts.save(
            artifact_type="QueryResult",
            status=art_status,
            payload=payload,
        )
        # Private memory: never store rows
        ctx.memory.update_working(
            {
                **prev,
                "last_rows_count": payload["rows_count"],
                "last_sql": (payload.get("sql") or "")[:500],
                "last_columns": list(payload.get("columns") or [])[:30],
                "last_question": str(question)[:300],
                "kernel": "analysis_kernel_v2",
            }
        )
        if payload["rows_count"]:
            ctx.memory.append_experience_note(
                f"v2查询成功 rows={payload['rows_count']} cols={len(payload.get('columns') or [])}",
                meta={"kind": "query_stats", "kernel": "v2"},
            )
        return {"artifact_id": aid, "payload": payload}


class InsightHarness(AgentHarness):
    def __init__(self):
        super().__init__(
            "insight",
            AgentSkill(
                "evidence-insight",
                frozenset(),
                "QueryResult",
                "Insight",
                procedure="insight.evidence_checklist.v1",
                policies=("no_db", "evidence_required"),
            ),
        )

    async def execute(self, ctx: HarnessContext) -> dict:
        from app.agents.procedure import INSIGHT_PROCEDURE, ProcedureRunner

        state = await ProcedureRunner(INSIGHT_PROCEDURE).run(ctx, agent_name="insight")
        result = state.get("result") or {
            "artifact_id": state.get("artifact_id"),
            "payload": {
                "findings": state.get("findings") or [],
                "hypotheses": state.get("hypotheses") or [],
            },
        }
        return result


class ReportHarness(AgentHarness):
    SECTION_TITLES = (
        "问题定义",
        "数据范围与口径",
        "关键发现",
        "归因链路",
        "业务影响与建议",
        "待验证假设与风险",
    )

    def __init__(self):
        super().__init__(
            "report",
            AgentSkill(
                "deep-report",
                frozenset(),
                "Insight",
                "ReportDocument",
                procedure="report.outline_draft_cite.v1",
                policies=("no_db", "evidence_citations"),
            ),
        )

    async def execute(self, ctx: HarnessContext) -> dict:
        from app.agents.procedure import REPORT_PROCEDURE, ProcedureRunner

        state = await ProcedureRunner(REPORT_PROCEDURE).run(ctx, agent_name="report")
        result = state.get("result") or {
            "artifact_id": state.get("artifact_id"),
            "payload": {"sections": state.get("sections") or []},
        }
        return result


class ReviewHarness(AgentHarness):
    """Review Agent = hard policy gate + LLM quality review with private context."""

    REQUIRED_SECTIONS = ReportHarness.SECTION_TITLES
    EVIDENCE_REQUIRED_TITLES = frozenset({"关键发现", "归因链路", "业务影响与建议"})
    CAUSAL_MARKERS = ("导致", "因为", "因此", "所以", "归因于", "caused", "because", "therefore")

    def __init__(self):
        super().__init__(
            "review",
            AgentSkill(
                "evidence-review",
                frozenset(),
                "ReportDocument",
                "ReviewResult",
                procedure="rules-then-llm",
                policies=("hard_gate", "no_db", "no_override_security"),
            ),
        )

    def _review_reasons(self, m: A2AMessage, inputs: dict) -> list[str]:
        reasons: list[str] = []
        query = inputs.get("query") or m.payload.get("query") or {}
        report = inputs.get("report") or m.payload.get("report") or {}
        allowed_ids = {str(x) for x in (m.artifact_ids or [])}

        rows = query.get("rows") or []
        if not rows and not (query.get("rows_count") or 0):
            reasons.append("没有有效查询证据。")

        if not m.artifact_ids:
            reasons.append("缺少证据工件引用。")

        sections = report.get("sections") or []
        titles = [str(s.get("title") or "") for s in sections if isinstance(s, dict)]
        missing = [t for t in self.REQUIRED_SECTIONS if t not in titles]
        if missing:
            reasons.append(f"报告缺少必需章节: {', '.join(missing)}")

        for sec in sections:
            if not isinstance(sec, dict):
                continue
            title = str(sec.get("title") or "")
            eids = [str(x) for x in (sec.get("evidence_ids") or [])]
            if title in self.EVIDENCE_REQUIRED_TITLES and not eids:
                reasons.append(f"章节「{title}」缺少证据引用。")
            unknown = [e for e in eids if e not in allowed_ids]
            if unknown:
                reasons.append(f"章节「{title}」引用未知证据: {', '.join(unknown[:5])}")

            claims = sec.get("claims") or []
            content = str(sec.get("content") or "")
            claim_hit = False
            for claim in claims:
                if not isinstance(claim, dict):
                    continue
                kind = str(claim.get("kind") or "fact").lower()
                c_eids = claim.get("evidence_ids") or []
                text = str(claim.get("text") or "")
                causal = any(mkr in text for mkr in self.CAUSAL_MARKERS)
                if kind == "fact" and causal and not c_eids:
                    reasons.append("无充分证据的因果表述必须标为假设。")
                    claim_hit = True
                    break
            if not claim_hit and title == "归因链路":
                causal = any(mkr in content for mkr in self.CAUSAL_MARKERS)
                hedge = any(h in content for h in ("假设", "可能", "或", "待验证", "hypothesis"))
                if causal and not hedge and not eids:
                    reasons.append("归因链路存在无证据因果表述，须标为假设。")

        blob = json.dumps(report, ensure_ascii=False).lower() if report else ""
        for bad in ("password", "api_key", "secret", "credential"):
            if bad in blob:
                reasons.append(f"报告疑似包含敏感字段: {bad}")
                break

        if m.user_id is None or not m.space_id or not m.session_id:
            reasons.append("作用域不完整，拒绝放行。")

        seen = set()
        out = []
        for r in reasons:
            if r not in seen:
                seen.add(r)
                out.append(r)
        return out

    async def _llm_review(self, ctx: HarnessContext) -> tuple[bool | None, list[str], str | None]:
        policy = role_model_policy("review")
        system = (
            "你是 Review Agent（证据审查员），不是写作者。\n"
            "在规则门禁已通过后，你负责质量审查：\n"
            "1) 报告主张是否被查询/洞察证据支持\n"
            "2) 数字、趋势、对比是否与 allowed_inputs 一致\n"
            "3) 无充分证据的因果是否已标为假设\n"
            "4) 是否夸大、编造或引入证据外事实\n"
            "5) 禁止放行含密码/密钥/凭据的内容\n"
            "可参考 working_memory/experience_memory 中的审查偏好，但不得放宽安全规则。\n"
            "只输出 JSON：{\"approved\":bool,\"reasons\":[str],\"notes\":str}\n"
            "approved=true 时 reasons 必须为 []。\n"
            "不要输出思维链或原始 rows 全文。"
        )
        user = json.dumps(ctx.model_bundle(), ensure_ascii=False, default=str)
        try:
            adapter = _model(ctx)
            result = await adapter.acomplete(
                ModelRequest(
                    system=system,
                    user=user,
                    tier=policy.tier,
                    thinking=policy.thinking,
                    max_tokens=policy.max_tokens,
                    expect_json=True,
                    temperature=0.0,
                )
            )
            if not result.ok or not isinstance(result.json_payload, dict):
                return None, [], (result.error or "llm_review_failed")
            payload = result.json_payload
            approved = bool(payload.get("approved"))
            reasons_raw = payload.get("reasons") or []
            reasons = [str(r) for r in reasons_raw if str(r).strip()]
            if approved:
                reasons = []
            elif not reasons:
                reasons = ["LLM 审查未通过（未给出具体原因）。"]
            return approved, reasons, None
        except Exception as exc:
            logger.exception("Review LLM failed")
            return None, [], str(exc)[:300]

    async def execute(self, ctx: HarnessContext) -> dict:
        m = ctx.message
        rule_reasons = self._review_reasons(m, ctx.allowed_inputs)
        llm_error = None
        llm_reasons: list[str] = []
        review_mode = "rules_only"

        if rule_reasons:
            ok = False
            reasons = rule_reasons
            review_mode = "rules_only"
        else:
            llm_ok, llm_reasons, llm_error = await self._llm_review(ctx)
            if llm_ok is None:
                ok = True
                reasons = []
                review_mode = "rules_fallback"
            elif llm_ok:
                ok = True
                reasons = []
                review_mode = "rules+llm"
            else:
                ok = False
                reasons = llm_reasons
                review_mode = "rules+llm"

        p = {
            "approved": ok,
            "reasons": reasons,
            "evidence_ids": m.artifact_ids,
            "review_mode": review_mode,
            "rule_reasons": rule_reasons,
            "llm_reasons": llm_reasons,
        }
        if llm_error:
            p["llm_error"] = llm_error

        aid = ctx.artifacts.save(
            artifact_type="ReviewResult",
            status="approved" if ok else "rejected",
            payload=p,
        )
        ctx.memory.update_working(
            {
                **ctx.working_memory,
                "approved": ok,
                "reasons": reasons[:10],
                "review_mode": review_mode,
            }
        )
        ctx.memory.append_experience_note(
            f"审查{'通过' if ok else '拒绝'} mode={review_mode}",
            meta={"kind": "review_outcome", "approved": ok},
        )
        return {"artifact_id": aid, "payload": p}
