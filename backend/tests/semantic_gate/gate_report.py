from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable

from .contracts import SemanticScore


HARD_ZERO = {
    "wrong_subject_table",
    "wrong_sql_tables",
    "wrong_reuse_decision",
    "guessed_when_clarification_required",
    "sensitive_raw_value_persisted",
}


def build_gate_report(
    scores: Iterable[SemanticScore | dict[str, Any]],
    *,
    test_exit_code: int = 0,
    skipped: int = 0,
    xfailed: int = 0,
    infrastructure_error: str = "",
    working_tree_clean: bool = True,
) -> dict[str, Any]:
    normalized = [s.to_dict() if isinstance(s, SemanticScore) else dict(s) for s in scores]
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    hard = Counter()
    for score in normalized:
        by_family[str(score.get("family") or "unknown")].append(score)
        hard.update(score.get("hard_failures") or [])

    families = {}
    for family, items in sorted(by_family.items()):
        total = len(items)
        full = sum(bool(item.get("full_correct")) for item in items)
        dimensions: dict[str, list[bool]] = defaultdict(list)
        for item in items:
            for name, value in (item.get("dimensions") or {}).items():
                dimensions[name].append(bool(value))
        families[family] = {
            "total": total,
            "full_correct": full,
            "full_correct_rate": full / total if total else 0.0,
            "dimension_rates": {
                name: sum(values) / len(values) if values else 0.0
                for name, values in sorted(dimensions.items())
            },
        }

    semantic_pass = bool(normalized) and all(
        item["full_correct_rate"] >= 0.95 for item in families.values()
    )
    zero_hard_pass = all(hard.get(name, 0) == 0 for name in HARD_ZERO)
    execution_pass = test_exit_code == 0 and skipped == 0 and xfailed == 0
    if infrastructure_error:
        status = "INFRA_BLOCKED"
    elif semantic_pass and zero_hard_pass and execution_pass and working_tree_clean:
        status = "READY_FOR_INTERNAL_PILOT"
    else:
        status = "NOT_PILOT_READY"
    return {
        "status": status,
        "semantic_pass": semantic_pass,
        "zero_hard_failure_pass": zero_hard_pass,
        "test_exit_code": test_exit_code,
        "skipped": skipped,
        "xfailed": xfailed,
        "working_tree_clean": working_tree_clean,
        "infrastructure_error": infrastructure_error,
        "families": families,
        "hard_failures": dict(sorted(hard.items())),
        "failed_case_ids": [
            str(item.get("case_id")) for item in normalized if not item.get("full_correct")
        ],
    }

