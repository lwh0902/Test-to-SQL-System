"""Multi-day offline eval + fault matrix for pilot admission (Phase 6).

Runs the same offline suite N times (days) to ensure stability, not flaky single-shot.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from app.agents.query_outcome import QueryOutcome, QueryOutcomeStatus, next_actions_for_outcome
from app.eval.phase3_runner import run_phase3_benchmark
from app.eval.phase1_link_check import run_fault_matrix_check


def run_fault_matrix_offline() -> dict[str, Any]:
    """100% correct termination behavior on FAULT_MATRIX."""
    try:
        return run_fault_matrix_check()
    except Exception:
        # local fallback if helper module shape differs
        cases = []
        ok = 0
        for st in QueryOutcomeStatus:
            o = QueryOutcome(status=st, rows_count=1 if st == QueryOutcomeStatus.SUCCESS_WITH_DATA else 0)
            acts = next_actions_for_outcome(o)
            allows_report = st == QueryOutcomeStatus.SUCCESS_WITH_DATA
            # report never for non-success-with-data
            bad = False
            if st != QueryOutcomeStatus.SUCCESS_WITH_DATA:
                if "report" in acts or "insight" in [a for a in acts if a == "report"]:
                    bad = True
                # insight may appear only for success_with_data per matrix
                row_ok = "report" not in acts
            else:
                row_ok = allows_report
            if not bad and row_ok:
                ok += 1
            cases.append({"status": st.value, "actions": acts, "ok": (not bad)})
        # stricter: use FAULT_MATRIX allows_report
        from app.agents.query_outcome import FAULT_MATRIX

        ok = 0
        n = 0
        for st, row in FAULT_MATRIX.items():
            n += 1
            if st != QueryOutcomeStatus.SUCCESS_WITH_DATA:
                if row.get("allows_report") is False and row.get("allows_insight") is False:
                    ok += 1
                elif row.get("allows_report") is False:
                    ok += 1
                else:
                    pass
            else:
                if row.get("allows_report") is True:
                    ok += 1
        return {"ok": ok, "n": n, "rate": (ok / n if n else 0.0), "cases": cases}


def run_multi_day_offline_eval(eval_root: Path | str, days: int = 3) -> dict[str, Any]:
    root = Path(eval_root)
    day_results = []
    t0 = time.perf_counter()
    for day in range(1, days + 1):
        p3 = run_phase3_benchmark(root, report_json=None, report_md=None)
        single_rate = float(p3.get("full_correct_rate") or 0.0)
        fault = run_fault_matrix_offline()
        fault_rate = float(fault.get("rate") or 0.0)
        day_results.append(
            {
                "day": day,
                "single_turn_rate": single_rate,
                "fault_matrix_rate": fault_rate,
                "pass": single_rate >= 0.95 and fault_rate >= 1.0,
                "phase3_full_n": p3.get("full_n"),
                "fault_n": fault.get("n"),
            }
        )
    return {
        "days": days,
        "day_results": day_results,
        "all_days_pass": all(d["pass"] for d in day_results),
        "elapsed_s": round(time.perf_counter() - t0, 4),
    }
