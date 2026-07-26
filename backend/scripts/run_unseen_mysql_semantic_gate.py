#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
    )
    return proc.stdout.strip()


def _junit(path: Path) -> dict:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    totals = Counter()
    failures = []
    hard = Counter()
    family_counts: dict[str, Counter] = {}
    family_rates: dict[str, list[float]] = {}
    family_names = ("commerce", "billing", "support", "iot", "warehouse", "ambiguous")
    patterns = (
        "wrong_subject_table",
        "wrong_sql_tables",
        "missing_or_wrong_measure",
        "missing_or_wrong_filter",
        "missing_or_wrong_dimension",
        "wrong_reuse_decision",
        "guessed_when_clarification_required",
        "sensitive_raw_value_persisted",
    )
    for suite in suites:
        for key in ("tests", "failures", "errors", "skipped"):
            totals[key] += int(suite.attrib.get(key, 0))
        for case in suite.findall(".//testcase"):
            failure = case.find("failure")
            if failure is None:
                failure = case.find("error")
            skipped = case.find("skipped") is not None
            case_name = f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}".lower()
            family = next((name for name in family_names if name in case_name), "")
            if family:
                counts = family_counts.setdefault(family, Counter())
                counts["tests"] += 1
                if skipped:
                    counts["skipped"] += 1
                elif failure is not None:
                    counts["failed"] += 1
                else:
                    counts["passed"] += 1
            if failure is None:
                continue
            case_id = f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}"
            text = (failure.text or "") + " " + failure.attrib.get("message", "")
            failures.append({"case_id": case_id, "message": text[:1200]})
            rate_match = re.search(r"family=([a-z_]+)\s+seed=\d+\s+full_rate=([0-9.]+)", text)
            if rate_match:
                family_rates.setdefault(rate_match.group(1), []).append(float(rate_match.group(2)))
            for pattern in patterns:
                hard[pattern] += len(re.findall(pattern, text))
    return {
        "totals": dict(totals),
        "failures": failures,
        "hard_failures": dict(hard),
        "families": {
            name: {
                **dict(counts),
                "observed_full_correct_rates": family_rates.get(name, []),
                "minimum_observed_full_correct_rate": min(family_rates[name])
                if family_rates.get(name)
                else None,
            }
            for name, counts in sorted(family_counts.items())
        },
    }


def _markdown(report: dict) -> str:
    totals = report["pytest"]["totals"]
    lines = [
        "# Unseen MySQL Semantic Gate",
        "",
        f"- status: **{report['status']}**",
        f"- commit_sha: `{report['commit_sha']}`",
        f"- working_tree_clean: `{report['working_tree_clean']}`",
        f"- tests: {totals.get('tests', 0)}",
        f"- failures: {totals.get('failures', 0)}",
        f"- errors: {totals.get('errors', 0)}",
        f"- skipped: {totals.get('skipped', 0)}",
        "",
        "## Hard failure signals",
        "",
    ]
    for name, count in sorted(report["pytest"]["hard_failures"].items()):
        lines.append(f"- {name}: {count}")
    lines += ["", "## Per-family gate tests", ""]
    for name, counts in sorted(report["pytest"].get("families", {}).items()):
        lines.append(
            f"- {name}: passed={counts.get('passed', 0)} failed={counts.get('failed', 0)} "
            f"skipped={counts.get('skipped', 0)}"
        )
    lines += ["", "## Failing tests", ""]
    for item in report["pytest"]["failures"][:80]:
        lines.append(f"- `{item['case_id']}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default=str(ROOT / "docs/handoff/unseen-mysql-semantic-gate.json"))
    parser.add_argument("--markdown", default=str(ROOT / "docs/handoff/unseen-mysql-semantic-gate.md"))
    parser.add_argument("--maxfail", type=int, default=0)
    args = parser.parse_args()

    json_path = Path(args.json)
    md_path = Path(args.markdown)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="datapilot-semantic-gate-") as tmp:
        junit = Path(tmp) / "junit.xml"
        cmd = [
            str(BACKEND / ".venv/bin/python"),
            "-m",
            "pytest",
            "-q",
            "tests/semantic_gate",
            f"--junitxml={junit}",
        ]
        if args.maxfail:
            cmd.append(f"--maxfail={args.maxfail}")
        proc = subprocess.run(cmd, cwd=BACKEND, text=True)
        pytest_data = _junit(junit) if junit.exists() else {
            "totals": {"tests": 0, "failures": 0, "errors": 1, "skipped": 0},
            "failures": [{"case_id": "pytest", "message": "junit report missing"}],
            "hard_failures": {},
            "families": {},
        }

    totals = pytest_data["totals"]
    clean = not bool(_git("status", "--porcelain"))
    all_green = (
        proc.returncode == 0
        and totals.get("failures", 0) == 0
        and totals.get("errors", 0) == 0
        and totals.get("skipped", 0) == 0
        and totals.get("tests", 0) >= 1
    )
    status = "READY_FOR_INTERNAL_PILOT" if all_green and clean else "NOT_PILOT_READY"
    report = {
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "commit_sha": _git("rev-parse", "HEAD"),
        "working_tree_clean": clean,
        "command": cmd,
        "exit_code": proc.returncode,
        "seeds": [
            20260726,
            731921,
            904177,
            *(
                [int(os.environ["DATAPILOT_UNSEEN_SEED"])]
                if os.getenv("DATAPILOT_UNSEEN_SEED", "").strip()
                and int(os.environ["DATAPILOT_UNSEEN_SEED"]) not in {20260726, 731921, 904177}
                else []
            ),
        ],
        "thresholds": {
            "per_family_full_correct_rate": 0.95,
            "value_match_rate": 0.98,
            "explicit_identifier_grounding": 1.0,
            "wrong_table_success": 0,
            "ambiguity_handled": 1.0,
            "followup_patch_correct": 0.98,
            "changed_followup_wrong_reuse": 0,
            "sensitive_raw_value_persisted": 0,
        },
        "pytest": pytest_data,
    }
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"status": status, "report": str(json_path), "exit_code": proc.returncode}, ensure_ascii=False))
    return 0 if status == "READY_FOR_INTERNAL_PILOT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
