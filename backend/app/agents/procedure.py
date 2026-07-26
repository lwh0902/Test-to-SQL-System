"""Executable role procedures (thinking paradigm without exposing CoT).

Insight: observe → extract → verify → label → commit
Report:  outline → draft → cite_check → revise → commit
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from app.agents.context_policy import compact_digest
from app.agents.model_adapter import ModelRequest, get_model_adapter, role_model_policy
from app.agents.progress import default_step_summary, emit_agent_progress, emit_artifact_produced

logger = logging.getLogger(__name__)

StepFn = Callable[[Any, dict], Awaitable[dict]]


@dataclass(frozen=True)
class ProcedureStep:
    name: str
    summary: str
    fn: StepFn


@dataclass(frozen=True)
class Procedure:
    id: str
    name: str
    steps: tuple[ProcedureStep, ...]


class ProcedureRunner:
    def __init__(self, procedure: Procedure):
        self.procedure = procedure

    async def run(self, ctx: Any, *, agent_name: str, initial_state: Optional[dict] = None) -> dict:
        state: dict[str, Any] = dict(initial_state or {})
        state.setdefault("agent", agent_name)
        state.setdefault("procedure", self.procedure.id)
        msg = getattr(ctx, "message", None)
        scope = {}
        if msg is not None:
            scope = {
                "task_id": msg.task_id,
                "session_id": msg.session_id,
                "user_id": msg.user_id,
                "space_id": msg.space_id,
            }

        for step in self.procedure.steps:
            # skip revise if not needed
            if step.name == "revise" and not state.get("needs_revise"):
                continue
            emit_agent_progress(
                {
                    **scope,
                    "agent": agent_name,
                    "step": step.name,
                    "status": "running",
                    "summary": step.summary or default_step_summary(agent_name, step.name, "running"),
                    "procedure": self.procedure.id,
                }
            )
            # also mirror to harness safe events if present
            events = getattr(ctx, "events", None)
            if events is not None and hasattr(events, "emit"):
                try:
                    events.emit(
                        "agent_progress",
                        {
                            "agent": agent_name,
                            "step": step.name,
                            "status": "running",
                            "summary": step.summary,
                        },
                    )
                except Exception:
                    pass
            try:
                result = await step.fn(ctx, state)
                if isinstance(result, dict):
                    state.update(result)
                emit_agent_progress(
                    {
                        **scope,
                        "agent": agent_name,
                        "step": step.name,
                        "status": "completed",
                        "summary": default_step_summary(agent_name, step.name, "completed"),
                        "procedure": self.procedure.id,
                        "counts": state.get("counts") or {},
                    }
                )
            except Exception as exc:
                emit_agent_progress(
                    {
                        **scope,
                        "agent": agent_name,
                        "step": step.name,
                        "status": "failed",
                        "summary": default_step_summary(agent_name, step.name, "failed"),
                        "procedure": self.procedure.id,
                        "error_code": type(exc).__name__,
                    }
                )
                raise
        return state


def _model(ctx):
    getter = getattr(ctx, "get_model", None)
    if callable(getter):
        m = getter()
        if m is not None:
            return m
    return get_model_adapter()


# ----- Insight steps -----

async def _insight_observe(ctx, state: dict) -> dict:
    inputs = getattr(ctx, "allowed_inputs", {}) or {}
    q = inputs.get("query") or {}
    working = getattr(ctx, "working_memory", {}) or {}
    return {
        "query": q,
        "evidence_ids": list(getattr(getattr(ctx, "package", None), "artifact_ids", None) or ctx.message.artifact_ids or []),
        "prior_digest": list(working.get("findings_digest") or [])[:8],
        "counts": {
            "rows_count": int(q.get("rows_count") or len(q.get("rows") or []) or 0),
            "columns": len(q.get("columns") or []),
        },
    }


async def _insight_extract(ctx, state: dict) -> dict:
    q = state.get("query") or {}
    evidence_ids = state.get("evidence_ids") or []
    candidates = []
    evidence_gaps: list[str] = []
    if q.get("rows") or q.get("rows_count"):
        policy = role_model_policy("insight")
        system = (
            "你是 Insight Agent 的 EXTRACT 步骤。只提取候选发现，不要写完整报告。\n"
            "输出 JSON：{\"candidates\":[{\"text\":str,\"kind\":\"fact|hypothesis\",\"evidence_ids\":[str]}],"
            "\"evidence_gaps\":[str]}。\n"
            "若证据不足以支撑完整归因（缺维度/缺表/缺对比期），必须在 evidence_gaps 列出可执行的补查缺口。\n"
            "禁止输出思维链；禁止输出原始 rows 数组；不要编造不存在的数字。"
        )
        user = json.dumps(
            {
                "phase": "extract",
                "bundle": ctx.model_bundle() if hasattr(ctx, "model_bundle") else {},
            },
            ensure_ascii=False,
            default=str,
        )
        try:
            result = await _model(ctx).acomplete(
                ModelRequest(
                    system=system,
                    user=user,
                    tier=policy.tier,
                    thinking=policy.thinking,
                    max_tokens=policy.max_tokens,
                    expect_json=True,
                    temperature=0.2,
                )
            )
            if result.ok and isinstance(result.json_payload, dict):
                for c in result.json_payload.get("candidates") or result.json_payload.get("findings") or []:
                    if isinstance(c, dict) and c.get("text"):
                        candidates.append(
                            {
                                "text": str(c.get("text") or "")[:500],
                                "kind": c.get("kind") or "fact",
                                "evidence_ids": c.get("evidence_ids") or evidence_ids,
                            }
                        )
                raw_gaps = result.json_payload.get("evidence_gaps") or []
                if isinstance(raw_gaps, list):
                    evidence_gaps = [str(g)[:200] for g in raw_gaps if str(g).strip()]
        except Exception:
            logger.exception("insight extract failed")
    if not candidates:
        n = (q.get("rows_count") or len(q.get("rows") or []) or 0)
        candidates = [
            {
                "text": f"查询返回 {n} 条结果。" if n else "没有可用于诊断的查询结果。",
                "kind": "fact",
                "evidence_ids": evidence_ids,
            }
        ]
        if not n and not evidence_gaps:
            evidence_gaps = ["缺少有效查询结果，需补充指标或时间范围查询"]
    return {
        "candidates": candidates,
        "evidence_gaps": evidence_gaps,
        "counts": {
            **(state.get("counts") or {}),
            "candidates": len(candidates),
            "evidence_gaps": len(evidence_gaps),
        },
    }


def _text_supported_by_preview(text: str, q: dict) -> bool:
    """Heuristic verify: numbers in claim should appear in preview if any numbers present."""
    import re

    nums = re.findall(r"\d+(?:\.\d+)?%?", text or "")
    if not nums:
        return True
    blob = json.dumps(q.get("rows") or [], ensure_ascii=False, default=str)
    blob += " " + str(q.get("rows_count") or "")
    # if claim has a striking number absent from data → weak
    missing = [n for n in nums if n not in blob]
    # allow small set of missing if most match
    if not missing:
        return True
    if len(missing) >= max(1, len(nums) // 2):
        return False
    return True


async def _insight_verify(ctx, state: dict) -> dict:
    q = state.get("query") or {}
    verified = []
    for c in state.get("candidates") or []:
        ok = _text_supported_by_preview(str(c.get("text") or ""), q)
        item = dict(c)
        item["verify_ok"] = ok
        if not ok and item.get("kind") == "fact":
            item["kind"] = "hypothesis"
            item["verify_note"] = "数值未能在查询预览中直接核对，降级为假设"
        verified.append(item)
    return {
        "verified": verified,
        "counts": {
            **(state.get("counts") or {}),
            "verified": len(verified),
            "downgraded": sum(1 for v in verified if v.get("verify_note")),
        },
    }


async def _insight_label(ctx, state: dict) -> dict:
    findings = []
    hypotheses = []
    evidence_ids = state.get("evidence_ids") or []
    evidence_gaps = list(state.get("evidence_gaps") or [])
    for v in state.get("verified") or []:
        item = {
            "text": str(v.get("text") or ""),
            "evidence_ids": v.get("evidence_ids") or evidence_ids,
            "kind": v.get("kind") or "fact",
        }
        if item["kind"] == "hypothesis":
            hypotheses.append({"text": item["text"], "evidence_ids": item["evidence_ids"]})
            findings.append(item)
        else:
            findings.append(item)
    if not findings:
        findings = [
            {
                "text": "未形成有效发现。",
                "evidence_ids": evidence_ids,
                "kind": "fact",
            }
        ]
    # Deterministic gap hints when LLM omitted them
    if not evidence_gaps:
        q = state.get("query") or {}
        cols = [str(c).lower() for c in (q.get("columns") or [])]
        n = int(q.get("rows_count") or len(q.get("rows") or []) or 0)
        if n == 0:
            evidence_gaps.append("查询结果为空，需放宽时间或过滤条件后重查")
        else:
            # single-series without dimension columns → suggest breakdown
            dim_hints = ("channel", "渠道", "category", "分类", "device", "source", "brand", "region")
            if not any(any(h in c for h in dim_hints) for c in cols):
                evidence_gaps.append("缺少维度拆解（渠道/分类/设备等），建议补充 breakdown 查询")
            if n == 1 and not any("date" in c or "time" in c or "日" in c for c in cols):
                evidence_gaps.append("缺少时间趋势对比，建议补充趋势查询")
    # de-dupe
    seen = set()
    uniq = []
    for g in evidence_gaps:
        g = str(g).strip()
        if g and g not in seen:
            seen.add(g)
            uniq.append(g[:200])
    evidence_gaps = uniq[:12]

    return {
        "findings": findings,
        "hypotheses": hypotheses,
        "evidence_gaps": evidence_gaps,
        "counts": {
            **(state.get("counts") or {}),
            "findings": len(findings),
            "hypotheses": len(hypotheses),
            "evidence_gaps": len(evidence_gaps),
        },
    }


async def _insight_commit(ctx, state: dict) -> dict:
    q = state.get("query") or {}
    p = {
        "findings": state.get("findings") or [],
        "hypotheses": state.get("hypotheses") or [],
        "evidence_gaps": list(state.get("evidence_gaps") or []),
    }
    aid = ctx.artifacts.save(
        artifact_type="Insight",
        status="approved" if (q.get("rows") or q.get("rows_count")) else "rejected",
        payload=p,
    )
    digest = compact_digest(p.get("findings") or [])
    ctx.memory.update_working(
        {
            **(ctx.working_memory or {}),
            "last_findings_count": len(p.get("findings") or []),
            "findings_digest": digest,
            "last_procedure": INSIGHT_PROCEDURE.id,
        }
    )
    if digest:
        ctx.memory.append_experience_note(
            f"洞察要点: {digest[0]}",
            meta={"kind": "insight_digest"},
        )
    emit_artifact_produced(
        {
            "agent": "insight",
            "artifact_type": "Insight",
            "artifact_id": aid,
            "status": "completed",
            "summary": "洞察工件已生成",
            "task_id": ctx.message.task_id,
            "session_id": ctx.message.session_id,
            "user_id": ctx.message.user_id,
            "space_id": ctx.message.space_id,
        }
    )
    return {"artifact_id": aid, "payload": p, "result": {"artifact_id": aid, "payload": p}}


INSIGHT_PROCEDURE = Procedure(
    id="insight.evidence_checklist.v1",
    name="insight",
    steps=(
        ProcedureStep("observe", "整理查询结果与私有上下文", _insight_observe),
        ProcedureStep("extract", "提取候选发现", _insight_extract),
        ProcedureStep("verify", "核对发现是否被证据支持", _insight_verify),
        ProcedureStep("label", "标记事实与假设", _insight_label),
        ProcedureStep("commit", "写入洞察工件", _insight_commit),
    ),
)


# ----- Report steps -----

_SECTION_TITLES = (
    "问题定义",
    "数据范围与口径",
    "关键发现",
    "归因链路",
    "业务影响与建议",
    "待验证假设与风险",
)


async def _report_outline(ctx, state: dict) -> dict:
    inputs = getattr(ctx, "allowed_inputs", {}) or {}
    question = inputs.get("question") or ""
    revision = inputs.get("revision_notes") or inputs.get("review_reasons") or []
    outline = [{"title": t, "bullets": []} for t in _SECTION_TITLES]
    if inputs.get("force_deterministic"):
        return {
            "outline": outline,
            "revision_notes": list(revision),
            "counts": {"outline_sections": len(outline)},
        }
    policy = role_model_policy("report")
    system = (
        "你是 Report Agent 的 OUTLINE 步骤。只输出六段大纲要点，不要写长文。\n"
        "标题必须是：问题定义；数据范围与口径；关键发现；归因链路；业务影响与建议；待验证假设与风险。\n"
        "输出 JSON：{\"outline\":[{\"title\":str,\"bullets\":[str]}]}"
    )
    user = json.dumps(
        {
            "phase": "outline",
            "question": question,
            "revision_notes": revision,
            "bundle": ctx.model_bundle() if hasattr(ctx, "model_bundle") else {},
        },
        ensure_ascii=False,
        default=str,
    )
    try:
        result = await _model(ctx).acomplete(
            ModelRequest(
                system=system,
                user=user,
                tier=policy.tier,
                thinking=policy.thinking,
                max_tokens=min(policy.max_tokens, 1024),
                expect_json=True,
                temperature=0.2,
            )
        )
        if result.ok and isinstance(result.json_payload, dict):
            raw = result.json_payload.get("outline") or []
            by_t = {str(x.get("title")): x for x in raw if isinstance(x, dict)}
            outline = []
            for t in _SECTION_TITLES:
                src = by_t.get(t) or {"title": t, "bullets": []}
                outline.append(
                    {
                        "title": t,
                        "bullets": [str(b)[:200] for b in (src.get("bullets") or [])[:5]],
                    }
                )
    except Exception:
        logger.exception("report outline failed")
    return {"outline": outline, "revision_notes": list(revision), "counts": {"outline_sections": len(outline)}}


def _deterministic_query_facts(query: dict) -> tuple[list[str], int]:
    """Render bounded, literal facts from query previews without inference."""
    queries = query.get("evidence_queries") if isinstance(query, dict) else None
    if not isinstance(queries, list) or not queries:
        queries = [query]
    ranked_facts: list[tuple[int, str]] = []
    total_rows = 0
    for item in queries[:6]:
        if not isinstance(item, dict):
            continue
        rows = [r for r in (item.get("rows") or [])[:20] if isinstance(r, dict)]
        if not rows:
            continue
        total_rows += int(item.get("rows_count") or len(rows))
        sql = str(item.get("sql") or "")
        table_match = re.search(r"\bFROM\s+`?([\w\u4e00-\u9fff]+)`?", sql, re.I)
        table = table_match.group(1) if table_match else "查询结果"
        if len(rows) == 1:
            pairs = [f"{k}={v}" for k, v in rows[0].items()]
            ranked_facts.append((int(item.get("rows_count") or len(rows)), f"{table}: " + "，".join(pairs)))
            continue
        metric_key = "value" if "value" in rows[0] else next(
            (k for k, v in rows[0].items() if isinstance(v, (int, float))), ""
        )
        dimension_keys = [k for k in rows[0] if k != metric_key]
        if metric_key and dimension_keys:
            ordered = sorted(
                rows,
                key=lambda row: float(row.get(metric_key) or 0)
                if isinstance(row.get(metric_key), (int, float))
                else 0,
                reverse=True,
            )
            rendered = []
            for row in ordered[:8]:
                dim = "/".join(str(row.get(k)) for k in dimension_keys)
                rendered.append(f"{dim}={row.get(metric_key)}")
            ranked_facts.append(
                (
                    int(item.get("rows_count") or len(rows)),
                    f"{table} 按 {'/'.join(dimension_keys)} 分布: " + "，".join(rendered),
                )
            )
        else:
            ranked_facts.append(
                (int(item.get("rows_count") or len(rows)), f"{table}: 返回 {len(rows)} 条分组结果")
            )
    ranked_facts.sort(key=lambda item: -item[0])
    return [fact for _rank, fact in ranked_facts], total_rows


async def _report_draft(ctx, state: dict) -> dict:
    inputs = getattr(ctx, "allowed_inputs", {}) or {}
    q = inputs.get("query") or {}
    i = inputs.get("insight") or {}
    e = list(getattr(getattr(ctx, "package", None), "artifact_ids", None) or ctx.message.artifact_ids or [])
    question = inputs.get("question") or ""
    outline = state.get("outline") or []
    revision = state.get("revision_notes") or []
    force_deterministic = bool(inputs.get("force_deterministic"))

    # Safe fallback is generated from literal query previews only. Insight
    # hypotheses are intentionally not promoted into key findings.
    evidence_facts, evidence_rows = _deterministic_query_facts(q)
    generation_mode = "deterministic_evidence_fallback"
    sections = [
        {"title": "问题定义", "content": question, "evidence_ids": e},
        {
            "title": "数据范围与口径",
            "content": f"本次基于 {len(evidence_facts)} 组成功查询证据、{evidence_rows} 条结果预览。",
            "evidence_ids": e,
        },
        {
            "title": "关键发现",
            "content": "\n".join(evidence_facts) or "暂无可验证的数据事实。",
            "evidence_ids": e,
        },
        {"title": "归因链路", "content": "现有查询只支持相关性描述，不能据此确认因果关系。", "evidence_ids": e},
        {"title": "业务影响与建议", "content": "优先处理查询中数量最高的异常分组，并补充可关联键后再验证根因。", "evidence_ids": e},
        {"title": "待验证假设与风险", "content": "Insight 中的假设不作为事实结论；结论受当前查询范围限制。", "evidence_ids": e},
    ]

    policy = role_model_policy("report")
    system = (
        "你是 Report Agent 的 DRAFT 步骤。按大纲撰写 canonical 六段报告。\n"
        "每章 content 为字符串，并带 evidence_ids。\n"
        "无充分证据的因果写入「待验证假设与风险」或标明假设。\n"
        "若有 revision_notes 必须针对性修改。\n"
        "禁止输出思维链与原始 rows。\n"
        "输出 JSON：{\"sections\":[{\"title\":str,\"content\":str,\"evidence_ids\":[str]}]}"
    )
    user = json.dumps(
        {
            "phase": "draft",
            "outline": outline,
            "revision_notes": revision,
            "bundle": ctx.model_bundle() if hasattr(ctx, "model_bundle") else {},
        },
        ensure_ascii=False,
        default=str,
    )
    if not force_deterministic:
        try:
            result = await _model(ctx).acomplete(
                ModelRequest(
                    system=system,
                    user=user,
                    tier=policy.tier,
                    thinking=policy.thinking,
                    max_tokens=policy.max_tokens,
                    expect_json=True,
                    temperature=0.3,
                )
            )
            if result.ok and isinstance(result.json_payload, dict):
                raw = result.json_payload.get("sections") or []
                by_t = {str(s.get("title")): s for s in raw if isinstance(s, dict)}
                fixed = []
                for t in _SECTION_TITLES:
                    src = by_t.get(t) or next((s for s in sections if s["title"] == t), {"title": t, "content": "", "evidence_ids": e})
                    fixed.append(
                        {
                            "title": t,
                            "content": str(src.get("content") or ""),
                            "evidence_ids": src.get("evidence_ids") or e,
                        }
                    )
                sections = fixed
                generation_mode = "llm"
        except Exception:
            logger.exception("report draft failed")

    return {
        "sections": sections,
        "generation_mode": generation_mode,
        "counts": {**(state.get("counts") or {}), "sections": len(sections)},
    }


async def _report_cite_check(ctx, state: dict) -> dict:
    e = list(getattr(getattr(ctx, "package", None), "artifact_ids", None) or ctx.message.artifact_ids or [])
    sections = list(state.get("sections") or [])
    problems = []
    required_evidence = {"关键发现", "归因链路", "业务影响与建议"}
    titles = {str(s.get("title")) for s in sections if isinstance(s, dict)}
    for t in _SECTION_TITLES:
        if t not in titles:
            problems.append(f"缺少章节:{t}")
    fixed = []
    for s in sections:
        if not isinstance(s, dict):
            continue
        title = str(s.get("title") or "")
        eids = list(s.get("evidence_ids") or [])
        if title in required_evidence and not eids:
            problems.append(f"章节「{title}」缺少证据引用")
            eids = list(e)
        fixed.append({**s, "evidence_ids": eids or e})
    # ensure all six
    by_t = {str(s.get("title")): s for s in fixed}
    sections = [by_t.get(t) or {"title": t, "content": "", "evidence_ids": e} for t in _SECTION_TITLES]
    needs_revise = bool(problems) or bool(state.get("revision_notes"))
    return {
        "sections": sections,
        "cite_problems": problems,
        "needs_revise": needs_revise and not state.get("revised_once"),
        "counts": {
            **(state.get("counts") or {}),
            "cite_problems": len(problems),
        },
    }


async def _report_revise(ctx, state: dict) -> dict:
    if state.get("revised_once"):
        return {}
    problems = list(state.get("cite_problems") or [])
    revision = list(state.get("revision_notes") or [])
    notes = revision + problems
    if not notes:
        return {"needs_revise": False, "revised_once": True}

    e = list(getattr(getattr(ctx, "package", None), "artifact_ids", None) or ctx.message.artifact_ids or [])
    sections = state.get("sections") or []
    policy = role_model_policy("report")
    system = (
        "你是 Report Agent 的 REVISE 步骤。根据 revision_notes 修订六段报告。\n"
        "保持六段标题不变；补齐 evidence_ids；修正审查指出的问题。\n"
        "禁止输出思维链。输出 JSON：{\"sections\":[{\"title\":str,\"content\":str,\"evidence_ids\":[str]}]}"
    )
    user = json.dumps(
        {
            "phase": "revise",
            "revision_notes": notes,
            "sections": sections,
            "evidence_ids": e,
        },
        ensure_ascii=False,
        default=str,
    )
    try:
        result = await _model(ctx).acomplete(
            ModelRequest(
                system=system,
                user=user,
                tier=policy.tier,
                thinking=policy.thinking,
                max_tokens=policy.max_tokens,
                expect_json=True,
                temperature=0.2,
            )
        )
        if result.ok and isinstance(result.json_payload, dict):
            raw = result.json_payload.get("sections") or []
            by_t = {str(s.get("title")): s for s in raw if isinstance(s, dict)}
            sections = []
            for t in _SECTION_TITLES:
                src = by_t.get(t) or {"title": t, "content": "", "evidence_ids": e}
                sections.append(
                    {
                        "title": t,
                        "content": str(src.get("content") or ""),
                        "evidence_ids": src.get("evidence_ids") or e,
                    }
                )
    except Exception:
        logger.exception("report revise failed")
    return {"sections": sections, "needs_revise": False, "revised_once": True}


async def _report_commit(ctx, state: dict) -> dict:
    sections = state.get("sections") or []
    p = {
        "sections": sections,
        "generation_mode": state.get("generation_mode") or "unknown",
    }
    aid = ctx.artifacts.save(artifact_type="ReportDocument", status="draft", payload=p)
    outline = [s.get("title") for s in sections if isinstance(s, dict)]
    ctx.memory.update_working(
        {
            **(ctx.working_memory or {}),
            "last_sections": len(sections),
            "outline": outline,
            "last_procedure": REPORT_PROCEDURE.id,
        }
    )
    ctx.memory.append_experience_note(
        "已生成六段诊断报告",
        meta={"kind": "report_outline", "sections": len(outline)},
    )
    emit_artifact_produced(
        {
            "agent": "report",
            "artifact_type": "ReportDocument",
            "artifact_id": aid,
            "status": "completed",
            "summary": "报告工件已生成",
            "task_id": ctx.message.task_id,
            "session_id": ctx.message.session_id,
            "user_id": ctx.message.user_id,
            "space_id": ctx.message.space_id,
        }
    )
    return {"artifact_id": aid, "payload": p, "result": {"artifact_id": aid, "payload": p}}


REPORT_PROCEDURE = Procedure(
    id="report.outline_draft_cite.v1",
    name="report",
    steps=(
        ProcedureStep("outline", "生成报告大纲", _report_outline),
        ProcedureStep("draft", "起草六段正文", _report_draft),
        ProcedureStep("cite_check", "检查证据引用", _report_cite_check),
        ProcedureStep("revise", "按审查意见修订", _report_revise),
        ProcedureStep("commit", "写入报告工件", _report_commit),
    ),
)


_REGISTRY: dict[str, Procedure] = {
    INSIGHT_PROCEDURE.id: INSIGHT_PROCEDURE,
    REPORT_PROCEDURE.id: REPORT_PROCEDURE,
    "evidence-findings": INSIGHT_PROCEDURE,  # legacy skill procedure label
    "six-section-report": REPORT_PROCEDURE,
    "insight": INSIGHT_PROCEDURE,
    "report": REPORT_PROCEDURE,
}


def get_procedure(procedure_id: str) -> Procedure:
    if procedure_id not in _REGISTRY:
        raise KeyError(f"unknown procedure: {procedure_id}")
    return _REGISTRY[procedure_id]
