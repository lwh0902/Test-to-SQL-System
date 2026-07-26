"""Evidence claim validation for Insight/Report (Phase 5)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable


CAUSAL_MARKERS = (
    "导致",
    "因为",
    "因此",
    "所以",
    "归因于",
    "造成",
    "caused",
    "because",
    "therefore",
    "due to",
)


@dataclass
class ClaimValidation:
    ok: bool
    unfounded_causal_count: int = 0
    key_claim_citation_rate: float = 1.0
    problems: list[str] = field(default_factory=list)
    normalized_claims: list[dict] = field(default_factory=list)


def _is_causal(text: str) -> bool:
    t = text or ""
    return any(m in t for m in CAUSAL_MARKERS)


def validate_claims(claims: Iterable[dict] | None) -> ClaimValidation:
    items = [c for c in (claims or []) if isinstance(c, dict)]
    if not items:
        return ClaimValidation(ok=True, key_claim_citation_rate=1.0, normalized_claims=[])

    unfounded = 0
    key_total = 0
    key_cited = 0
    problems: list[str] = []
    normalized: list[dict] = []

    for c in items:
        text = str(c.get("text") or "").strip()
        kind = str(c.get("kind") or "fact").lower()
        eids = [str(x) for x in (c.get("evidence_ids") or []) if str(x).strip()]
        causal = _is_causal(text)
        item = {**c, "text": text, "kind": kind, "evidence_ids": eids}

        # key factual claims must be cited
        if kind == "fact" and text and not text.startswith("未形成"):
            key_total += 1
            if eids:
                key_cited += 1
            else:
                problems.append(f"fact_missing_citation:{text[:40]}")

        if causal and kind == "fact" and not eids:
            unfounded += 1
            # auto-downgrade suggestion
            item["kind"] = "hypothesis"
            item["auto_downgraded"] = True
            problems.append(f"unfounded_causal_downgraded:{text[:40]}")
        elif causal and kind == "fact" and eids:
            # allowed if cited
            pass

        normalized.append(item)

    rate = (key_cited / key_total) if key_total else 1.0
    # unfounded_causal_count = original violations (fact+causal+no evidence)
    ok = unfounded == 0 and rate >= 1.0 and not any(
        p.startswith("fact_missing_citation") for p in problems
    )
    return ClaimValidation(
        ok=ok,
        unfounded_causal_count=unfounded,
        key_claim_citation_rate=rate,
        problems=problems,
        normalized_claims=normalized,
    )


def validate_report_sections(sections: list[dict] | None, allowed_ids: set[str] | None = None) -> ClaimValidation:
    """Aggregate claims from report sections + section-level evidence_ids."""
    allowed = allowed_ids or set()
    claims: list[dict] = []
    problems: list[str] = []
    required = {"关键发现", "归因链路", "业务影响与建议"}
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title") or "")
        eids = [str(x) for x in (sec.get("evidence_ids") or [])]
        if title in required:
            if not eids:
                problems.append(f"section_missing_evidence:{title}")
            elif allowed and any(e not in allowed for e in eids):
                problems.append(f"section_unknown_evidence:{title}")
            # treat section content as a key claim unit
            claims.append(
                {
                    "text": str(sec.get("content") or title)[:200],
                    "kind": "fact",
                    "evidence_ids": eids,
                }
            )
        for c in sec.get("claims") or []:
            if isinstance(c, dict):
                claims.append(c)
        # causal content without hedge in 归因链路
        if title == "归因链路":
            content = str(sec.get("content") or "")
            hedge = any(h in content for h in ("假设", "可能", "待验证", "hypothesis", "或"))
            if _is_causal(content) and not hedge and not eids:
                claims.append(
                    {
                        "text": content[:120],
                        "kind": "fact",
                        "evidence_ids": [],
                    }
                )

    base = validate_claims(claims)
    # If we auto-downgraded, recount unfounded as 0 for gate when normalized is clean
    remaining = sum(
        1
        for c in base.normalized_claims
        if _is_causal(str(c.get("text") or ""))
        and str(c.get("kind")).lower() == "fact"
        and not (c.get("evidence_ids") or [])
    )
    # section missing evidence hurts citation rate
    if any(p.startswith("section_missing_evidence") for p in problems):
        base.key_claim_citation_rate = min(base.key_claim_citation_rate, 0.0)
        base.ok = False
    base.unfounded_causal_count = remaining
    base.problems = list(base.problems) + problems
    base.ok = base.ok and remaining == 0 and base.key_claim_citation_rate >= 1.0
    return base


def extract_claims_from_insight(payload: dict | None) -> list[dict]:
    p = payload or {}
    out = []
    for f in p.get("findings") or []:
        if isinstance(f, dict):
            out.append(f)
    for h in p.get("hypotheses") or []:
        if isinstance(h, dict):
            out.append({**h, "kind": h.get("kind") or "hypothesis"})
    return out
