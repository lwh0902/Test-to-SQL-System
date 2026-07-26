"""Export Agent — render approved ReportDocument / evidence into real files.

Uses execute(ctx): private memory via MemoryAdapter, artifacts via ArtifactAdapter.
dev_spec: only ReviewResult=approved may produce final export.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.agents.export_render import render_export_bytes
from app.agents.harness import AgentHarness, AgentSkill, HarnessContext

__all__ = ["ExportHarness", "render_export_bytes", "EXPORT_DIR", "get_export_path"]

EXPORT_DIR = Path(os.getenv("EXPORT_STORAGE_DIR", "data/exports")).resolve()
DEFAULT_TTL_HOURS = int(os.getenv("EXPORT_TTL_HOURS", "72"))


def get_export_path(file_name: str) -> Path:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    name = Path(file_name).name
    return EXPORT_DIR / name


def _expires_at(hours: int = DEFAULT_TTL_HOURS) -> str:
    ts = datetime.now(timezone.utc) + timedelta(hours=hours)
    return ts.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_format(kind: str, fmt: str | None) -> tuple[str, str]:
    kind = (kind or "report").lower()
    fmt = (fmt or "").lower() or None
    if kind in ("evidence_csv",):
        return "evidence", "csv"
    if kind in ("evidence_xlsx",):
        return "evidence", "xlsx"
    if kind in ("evidence",):
        return "evidence", fmt or "csv"
    if fmt in ("csv", "xlsx"):
        return "evidence", fmt
    return "report", fmt or "pdf"


class ExportHarness(AgentHarness):
    def __init__(self):
        super().__init__(
            "export",
            AgentSkill(
                "secure-export",
                frozenset(),
                "ApprovedReportOrEvidence",
                "ExportFile",
                procedure="render-approved",
                policies=("review_required", "scoped_download"),
            ),
        )

    async def execute(self, ctx: HarnessContext) -> dict:
        m = ctx.message
        inputs = ctx.allowed_inputs
        review = inputs.get("review") or {}
        report = inputs.get("report") or {}
        query = inputs.get("query") or {}

        scope = {
            "task_id": m.task_id,
            "session_id": m.session_id,
            "user_id": m.user_id,
            "space_id": m.space_id,
        }

        if not review.get("approved"):
            p = {
                "exported": False,
                "reason": "Review not approved",
                "evidence_ids": m.artifact_ids,
                **scope,
            }
            aid = ctx.artifacts.save(
                artifact_type="ExportFile",
                status="rejected",
                payload=p,
            )
            ctx.memory.update_working({**ctx.working_memory, "last_export": None, "rejected": True})
            return {"artifact_id": aid, "payload": p}

        kind, fmt = _normalize_format(
            inputs.get("kind") or m.payload.get("kind") or "report",
            inputs.get("format") or m.payload.get("format"),
        )
        content = render_export_bytes(kind=kind, fmt=fmt, report=report, query=query)
        file_name = f"{m.task_id}_{fmt}_{uuid.uuid4().hex[:10]}.{fmt}"

        from app.agents import export as export_mod

        out = Path(export_mod.EXPORT_DIR) / file_name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)

        p = {
            "exported": True,
            "kind": kind,
            "format": fmt,
            "file_name": file_name,
            "byte_size": len(content),
            "content_type": {
                "pdf": "application/pdf",
                "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "csv": "text/csv; charset=utf-8",
                "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            }.get(fmt, "application/octet-stream"),
            "expires_at": _expires_at(),
            "evidence_ids": m.artifact_ids,
            **scope,
        }
        aid = ctx.artifacts.save(
            artifact_type="ExportFile",
            status="approved",
            payload=p,
        )
        p["download_url"] = f"/api/exports/{aid}/download"
        p["artifact_id"] = aid
        try:
            from app.services.agent_runtime_store import update_artifact_payload

            update_artifact_payload(aid, m.session_id, m.user_id, m.space_id, p)
        except Exception:
            pass

        ctx.memory.update_working(
            {
                **ctx.working_memory,
                "last_export": fmt,
                "last_file": file_name,
                "last_bytes": len(content),
            }
        )
        ctx.memory.append_experience_note(
            f"导出 {fmt} 成功 size={len(content)}",
            meta={"kind": "export", "format": fmt},
        )
        return {"artifact_id": aid, "payload": p}
