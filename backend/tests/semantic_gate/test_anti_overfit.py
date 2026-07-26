from __future__ import annotations

from pathlib import Path

from .case_matrix import DEFAULT_SEEDS, gate_seeds
from .schema_factory import build_schema_suites


ROOT = Path(__file__).resolve().parents[2]


def test_generated_identifiers_are_not_hardcoded_in_production():
    generated = {
        identifier
        for seed in gate_seeds()
        for suite in build_schema_suites(seed)
        for identifier in suite.identifiers.values()
    }
    offenders = []
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        hits = sorted(value for value in generated if value in text)
        if hits:
            offenders.append((str(path.relative_to(ROOT)), hits[:5]))
    assert not offenders, f"fixture-specific production branches: {offenders[:10]}"


def test_metamorphic_seeds_preserve_case_shape_and_ground_truth():
    baseline = {s.family: s for s in build_schema_suites(DEFAULT_SEEDS[0])}
    for seed in DEFAULT_SEEDS[1:]:
        candidate = {s.family: s for s in build_schema_suites(seed)}
        for family, suite in baseline.items():
            other = candidate[family]
            assert suite.ground_truth == other.ground_truth
            assert len(suite.single_turn_cases) == len(other.single_turn_cases)
            assert len(suite.followup_chains) == len(other.followup_chains)
            assert suite.identifiers != other.identifiers
