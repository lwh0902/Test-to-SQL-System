from .case_matrix import DEFAULT_SEEDS, gate_seeds
from .schema_factory import build_schema_suites


def test_required_families_and_case_volume():
    suites = build_schema_suites(seed=20260726)
    assert {s.family for s in suites} == {
        "commerce",
        "billing",
        "support",
        "iot",
        "warehouse",
        "ambiguous",
    }
    assert sum(len(s.single_turn_cases) for s in suites) >= 120
    assert sum(len(s.followup_chains) for s in suites) >= 40


def test_identifiers_change_but_ground_truth_does_not():
    first = {s.family: s for s in build_schema_suites(DEFAULT_SEEDS[0])}
    second = {s.family: s for s in build_schema_suites(DEFAULT_SEEDS[1])}
    for family in first:
        assert first[family].identifiers != second[family].identifiers
        assert first[family].ground_truth == second[family].ground_truth


def test_gate_does_not_reuse_current_mock_table_names():
    forbidden = {"ecom_orders", "ecom_products", "scan_records", "api_logs"}
    for suite in build_schema_suites():
        assert forbidden.isdisjoint(set(suite.identifiers.values()))


def test_rare_status_trap_is_outside_first_100_logical_rows():
    support = next(s for s in build_schema_suites() if s.family == "support")
    failed = "'failed'"
    values = support.seed_sql.split("VALUES", 2)[-1].splitlines()
    failed_positions = [index for index, row in enumerate(values) if failed in row]
    assert failed_positions
    # When insertion order is not reversed, failure values appear only after row 100.
    if support.seed % 2 == 0:
        assert min(failed_positions) >= 100


def test_opaque_families_have_non_business_identifiers():
    suites = {s.family: s for s in build_schema_suites()}
    for family in ("iot", "ambiguous"):
        assert all(value.startswith("x_") for value in suites[family].identifiers.values())


def test_each_family_contains_a_competing_numeric_time_table_and_varied_order():
    first_tables = set()
    for seed in DEFAULT_SEEDS:
        for suite in build_schema_suites(seed):
            assert suite.physical("decoy") in suite.ddl
            assert suite.physical("decoy_value") in suite.ddl
            assert suite.physical("decoy_time") in suite.ddl
            first_create = suite.ddl.split("CREATE TABLE `", 1)[1].split("`", 1)[0]
            first_tables.add(first_create)
    assert len(first_tables) > 6, "creation order must vary across metamorphic seeds"


def test_ci_can_add_an_unseen_seed(monkeypatch):
    monkeypatch.setenv("DATAPILOT_UNSEEN_SEED", "42424242")
    assert gate_seeds()[-1] == 42424242
    assert len(gate_seeds()) == len(DEFAULT_SEEDS) + 1
