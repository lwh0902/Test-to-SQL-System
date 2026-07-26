"""Deterministic diagnosis agents — COMPONENT / UNIT tests ONLY.

evidence_class=component_only. NOT for production ApplicationService path (R5.5b).
Production must use dispatcher_agent_call → A2A Dispatcher → role Harness.
"""

from __future__ import annotations

from typing import Any, AsyncIterator
from uuid import uuid4

EVIDENCE_CLASS = "component_only"
NOT_FOR_PRODUCTION = True


def _aid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


async def deterministic_agent_call(
    task: dict,
    target: str,
    payload: dict,
    artifact_ids: list,
    suffix: str = "",
) -> AsyncIterator[dict]:
    """Async generator compatible with diagnosis_pipeline.AgentCall."""
    q = payload.get("query") or {}
    rows = list(q.get("rows") or [])
    sql = str(q.get("sql") or "")
    n = int(q.get("rows_count") if q.get("rows_count") is not None else len(rows))
    val = None
    if rows:
        try:
            val = list(rows[0].values())[-1]
        except Exception:
            val = rows[0]

    if target == "insight":
        aid = _aid("ins")
        text_fact = f"查询返回 {n} 行" + (f"，关键值 {val}" if val is not None else "")
        yield {
            "type": "result",
            "value": {
                "artifact_id": aid,
                "payload": {
                    "findings": [
                        {
                            "text": text_fact,
                            "kind": "fact",
                            "evidence_ids": list(artifact_ids[:1] or ["qr_1"]),
                        }
                    ],
                    "hypotheses": [
                        {
                            "text": "波动可能与渠道/地区结构有关（待验证）",
                            "kind": "hypothesis",
                            "evidence_ids": [],
                        }
                    ],
                    "evidence_gaps": [],
                    "summary": text_fact,
                },
            },
        }
        return

    if target == "query":
        # gap fill — return empty additional slice (still SUCCESS path)
        aid = _aid("gap")
        yield {
            "type": "result",
            "value": {
                "artifact_id": aid,
                "payload": {
                    "rows": rows[:1],
                    "rows_count": min(1, n),
                    "columns": list(q.get("columns") or (list(rows[0].keys()) if rows else [])),
                    "sql": sql,
                    "query_outcome": {"status": "SUCCESS_WITH_DATA", "rows_count": min(1, n)},
                },
            },
        }
        return

    if target == "report":
        aid = _aid("rep")
        eids = list(artifact_ids) or ["qr_1"]
        fact = f"关键数值为 {val}" if val is not None else f"共 {n} 行结果"
        yield {
            "type": "result",
            "value": {
                "artifact_id": aid,
                "payload": {
                    "title": "深度诊断报告",
                    "sections": [
                        {
                            "title": "问题定义",
                            "content": str(task.get("question") or payload.get("question") or ""),
                            "evidence_ids": eids[:1],
                        },
                        {
                            "title": "关键发现",
                            "content": fact + "。",
                            "evidence_ids": eids[:1],
                            "claims": [
                                {
                                    "text": fact,
                                    "kind": "fact",
                                    "evidence_ids": eids[:1],
                                }
                            ],
                        },
                        {
                            "title": "业务影响与建议",
                            "content": "建议按地区/渠道继续拆解并对比时间窗。",
                            "evidence_ids": eids[:1],
                        },
                        {
                            "title": "待验证假设与风险",
                            "content": "假设：结构变化导致波动（待验证）。",
                            "evidence_ids": eids[:1],
                        },
                    ],
                    "sql": sql,
                },
            },
        }
        return

    if target == "review":
        aid = _aid("rev")
        report = payload.get("report") or {}
        sections = report.get("sections") or []
        ok = bool(sections)
        # approve only if every key section has evidence_ids
        for s in sections:
            if isinstance(s, dict) and s.get("title") in {"关键发现", "问题定义"}:
                if not (s.get("evidence_ids") or []):
                    ok = False
        yield {
            "type": "result",
            "value": {
                "artifact_id": aid,
                "payload": {
                    "approved": ok,
                    "reasons": [] if ok else ["关键章节缺少证据引用"],
                    "score": 0.9 if ok else 0.2,
                },
            },
        }
        return

    if target == "export":
        yield {
            "type": "result",
            "value": {
                "artifact_id": _aid("exp"),
                "payload": {"exported": False, "reason": "export deferred"},
            },
        }
        return

    yield {"type": "result", "value": {"artifact_id": _aid("unk"), "payload": {}}}
