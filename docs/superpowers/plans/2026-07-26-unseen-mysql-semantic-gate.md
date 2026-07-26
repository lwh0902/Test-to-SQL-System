# Unseen MySQL Semantic Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a mandatory, anti-overfit test gate that proves DataPilot either answers semantically correctly on previously unseen MySQL schemas or explicitly clarifies, then make that gate a prerequisite in `dev_spec.md`.

**Architecture:** Test-owned schema factories create six unrelated MySQL families and deterministic renamed variants. A strict scorer compares behavior, AnalysisSpec, SQL structure, values, reuse, and privacy evidence; a mandatory runner aggregates per-family gates without averaging away hard failures. No production Planner code is changed in this task, so the new semantic tests must initially fail for the observed defects.

**Tech Stack:** Python 3.12, pytest, PyMySQL, sqlglot, FastAPI application service, JSON/Markdown handoff reports.

---

### Task 1: Semantic expectation and strict scorer contracts

**Files:**
- Create: `backend/tests/semantic_gate/__init__.py`
- Create: `backend/tests/semantic_gate/contracts.py`
- Create: `backend/tests/semantic_gate/assertions.py`
- Create: `backend/tests/semantic_gate/test_strict_semantic_scorer.py`

- [ ] **Step 1: Write the failing scorer tests**

Define tests that prove `SUCCESS_WITH_DATA` fails when the subject table is wrong, a required filter is missing, a requested second measure is missing, or changed semantics are reused. Also prove clarification passes only when the expected ambiguity slot is returned.

```python
def test_wrong_table_success_is_hard_failure():
    expected = SemanticExpectation(
        case_id="wrong_table",
        family="contract",
        question="count alpha_orders",
        behavior="answer",
        subject_table="alpha_orders",
        sql_tables={"alpha_orders"},
    )
    observed = SemanticObservation(
        behavior="answer",
        terminal_status="SUCCESS_WITH_DATA",
        sql="SELECT COUNT(*) FROM alpha_items",
        spec={"subject": "alpha_items", "required_tables": ["alpha_items"]},
    )
    score = score_semantics(expected, observed)
    assert not score.full_correct
    assert "wrong_subject_table" in score.hard_failures
```

- [ ] **Step 2: Run the scorer tests and verify RED**

Run: `cd backend && .venv/bin/pytest -q tests/semantic_gate/test_strict_semantic_scorer.py`

Expected: FAIL during collection because `contracts.py` and `assertions.py` do not exist.

- [ ] **Step 3: Implement the test-owned contracts and scorer**

Use dataclasses for `SemanticExpectation`, `SemanticObservation`, and `SemanticScore`. Parse SQL with `sqlglot.parse_one(..., read="mysql")`; collect physical tables, selected aggregates, predicates, grouping, ordering, and limit. `full_correct` is the conjunction of applicable dimensions, while wrong table, unsafe join, wrong reuse, missing measure/filter/dimension/time, guessed ambiguity, and sensitive persistence are hard failures.

```python
@dataclass(frozen=True)
class SemanticExpectation:
    case_id: str
    family: str
    question: str
    behavior: str
    subject_table: str | None = None
    sql_tables: set[str] = field(default_factory=set)
    measures: tuple[MeasureExpectation, ...] = ()
    filters: tuple[FilterExpectation, ...] = ()
    dimensions: tuple[str, ...] = ()
    time_range: tuple[str, str] | None = None
    order_by: tuple[str, ...] = ()
    limit: int | None = None
    expected_values: dict[str, float | int | str] = field(default_factory=dict)
    clarify_slots: tuple[str, ...] = ()
    expect_reuse: bool | None = None
```

- [ ] **Step 4: Run the scorer tests and verify GREEN**

Run: `cd backend && .venv/bin/pytest -q tests/semantic_gate/test_strict_semantic_scorer.py`

Expected: all scorer contract tests pass.

### Task 2: Deterministic and metamorphic schema factories

**Files:**
- Create: `backend/tests/semantic_gate/schema_factory.py`
- Create: `backend/tests/semantic_gate/case_matrix.py`
- Create: `backend/tests/semantic_gate/test_schema_factory.py`

- [ ] **Step 1: Write failing factory tests**

Assert that six families exist, generated identifiers vary by seed, logical Ground Truth remains stable across renames and row-order changes, opaque families do not contain current Mock identifiers, and the rare-status trap places failures outside the first 100 physical rows.

```python
def test_required_families_and_case_volume():
    suites = build_schema_suites(seed=20260726)
    assert {s.family for s in suites} == {
        "commerce", "billing", "support", "iot", "warehouse", "ambiguous"
    }
    assert sum(len(s.single_turn_cases) for s in suites) >= 120
    assert sum(len(s.followup_chains) for s in suites) >= 40
```

- [ ] **Step 2: Run the factory tests and verify RED**

Run: `cd backend && .venv/bin/pytest -q tests/semantic_gate/test_schema_factory.py`

Expected: FAIL because the schema factory is not implemented.

- [ ] **Step 3: Implement schema and case generation**

Each `SchemaSuite` contains MySQL DDL, batched seed rows, a logical-to-physical identifier map, structured cases, expected results, and sensitivity metadata. Generate conventional English, Chinese, abbreviated, and opaque variants with deterministic `random.Random(seed)`. Use actual FK constraints in high-confidence families and deliberately conflicting evidence in the ambiguous family.

Case templates cover explicit count, business subject, aggregation, enum/boolean/time filters, grouping, Top N, conditional rate, multiple measures, join, clarification, empty result, and unsafe request. Paraphrase variants raise the volume without changing Ground Truth.

- [ ] **Step 4: Run the factory tests and verify GREEN**

Run: `cd backend && .venv/bin/pytest -q tests/semantic_gate/test_schema_factory.py`

Expected: all factory tests pass and report at least 120 single-turn cases and 40 chains.

### Task 3: Profiler and privacy gate

**Files:**
- Create: `backend/tests/semantic_gate/mysql_runtime.py`
- Create: `backend/tests/semantic_gate/test_profiler_gate.py`

- [ ] **Step 1: Write failing profiler tests**

Provision generated schemas in real MySQL and call `profile_live_mysql`. Require evidence-bearing profiles, status vocabularies, sensitive classification, no raw sample persistence, bounded query metadata, schema fingerprint invalidation, and detection of a rare value placed after row 100.

```python
def test_rare_status_is_discovered_without_first_100_bias(profiled_support_suite):
    catalog = profiled_support_suite.catalog
    status = find_column(catalog, profiled_support_suite.physical("ticket_status"))
    assert "failed" in status.profile.top_values
    assert status.profile.sample_strategy != "physical_head_only"
```

- [ ] **Step 2: Run profiler tests and verify RED**

Run: `cd backend && .venv/bin/pytest -q tests/semantic_gate/test_profiler_gate.py`

Expected: FAIL because current `SemanticCatalog` does not carry the required profile evidence and bounded-sampling metadata.

- [ ] **Step 3: Add mandatory infrastructure semantics**

The test runtime must not skip when MySQL is unavailable. It raises an explicit `SemanticGateInfrastructureError` so the runner reports `INFRA_BLOCKED`, never PASS. Database names include the seed and worker ID; teardown drops only those exact validated names.

- [ ] **Step 4: Verify profiler failures are intentional**

Run: `cd backend && .venv/bin/pytest -q tests/semantic_gate/test_profiler_gate.py`

Expected current state: assertion failures naming missing profile evidence, sample strategy, sensitivity policy, or status vocabulary—not fixture errors or import errors.

### Task 4: Planner, compiler, follow-up, and anti-overfit gates

**Files:**
- Create: `backend/tests/semantic_gate/test_planner_gate.py`
- Create: `backend/tests/semantic_gate/test_followup_gate.py`
- Create: `backend/tests/semantic_gate/test_anti_overfit.py`

- [ ] **Step 1: Write parametrized Planner contract tests**

For every generated case, call the production Planner against the generated Catalog and normalize its action and Spec into `SemanticObservation`. Assert explicit identifiers at 100%, per-family full correctness at 95% or higher, ambiguity handling at 100%, and zero wrong-table successes.

```python
@pytest.mark.parametrize("suite,case", planner_cases(), ids=case_id)
def test_unseen_schema_planner_contract(suite, case):
    result = plan_question(case.question, suite.catalog)
    observation = observe_plan(result)
    score = score_semantics(case.expectation, observation)
    assert score.full_correct, score.explain()
```

- [ ] **Step 2: Write follow-up and reuse tests**

Construct real `ActiveAnalysisState`, pass Supervisor-authoritative follow-up intent through the production controlled path, and verify dimension/filter/time/Top N/rate/multi-measure patches. A changed canonical Spec must not reuse; an identical Spec must reuse.

- [ ] **Step 3: Write anti-overfit tests**

Run equivalent cases under at least three fixed rename seeds plus `DATAPILOT_UNSEEN_SEED`. Scan `backend/app` Python sources for generated fixture IDs and current gate table identifiers. Assert table creation order, column order, and irrelevant noise columns do not change behavior or Ground Truth.

- [ ] **Step 4: Run and verify meaningful RED**

Run: `cd backend && .venv/bin/pytest -q tests/semantic_gate/test_planner_gate.py tests/semantic_gate/test_followup_gate.py tests/semantic_gate/test_anti_overfit.py`

Expected current state: semantic assertion failures for wrong subject, missing filters/dimensions/derived measures, follow-up downgrade, or wrong reuse. Infrastructure and fixture tests remain green.

### Task 5: Public API gate and mandatory report runner

**Files:**
- Create: `backend/tests/semantic_gate/test_api_gate.py`
- Create: `backend/scripts/run_unseen_mysql_semantic_gate.py`
- Create: `backend/tests/semantic_gate/test_gate_runner.py`

- [ ] **Step 1: Write failing API and runner tests**

Use the authenticated public JSON and SSE paths over provisioned real MySQL. Normalize response, Trace, AnalysisSpec, SQL, values, and reuse decision. The runner must reject skipped/xfail tests, report infra separately, aggregate every semantic dimension per family, and keep `NOT_PILOT_READY` when any non-compensable failure occurs.

```python
def test_wrong_table_cannot_be_averaged_away():
    report = build_gate_report([passing_score(), wrong_table_score()])
    assert report["status"] == "NOT_PILOT_READY"
    assert report["hard_failures"]["wrong_subject_table"] == 1
```

- [ ] **Step 2: Implement the runner**

The command below runs the complete mandatory suite and writes the report only after pytest terminates:

```bash
cd backend
.venv/bin/python scripts/run_unseen_mysql_semantic_gate.py \
  --json ../docs/handoff/unseen-mysql-semantic-gate.json \
  --markdown ../docs/handoff/unseen-mysql-semantic-gate.md
```

The report records commit SHA, working-tree state, MySQL version, seeds, schema fingerprints, case counts, per-family metrics, hard-failure counts, skip/xfail counts, and exact failing case IDs.

- [ ] **Step 3: Verify runner unit tests GREEN**

Run: `cd backend && .venv/bin/pytest -q tests/semantic_gate/test_gate_runner.py`

Expected: runner aggregation and report-shape tests pass.

- [ ] **Step 4: Run the mandatory gate and record intended RED**

Run the command in Step 2.

Expected current state: exit code non-zero and report status `NOT_PILOT_READY`, with semantic failures rather than a false chain-health pass.

### Task 6: DevSpec non-bypassable admission

**Files:**
- Modify: `dev_spec.md`
- Create: `backend/tests/semantic_gate/test_devspec_gate.py`

- [ ] **Step 1: Write failing DevSpec contract tests**

Require the document to contain R3.5, the mandatory runner command, per-family thresholds, zero wrong-table success, profiling/privacy constraints, anti-overfit rules, and explicit suspension of downstream pilot evidence.

```python
def test_devspec_requires_unseen_mysql_gate_before_pilot():
    text = DEV_SPEC.read_text(encoding="utf-8")
    assert "Recovery Phase 3.5" in text
    assert "run_unseen_mysql_semantic_gate.py" in text
    assert "wrong-table successful answers: 0" in text
    assert "R3.5" in extract_r6_prerequisites(text)
```

- [ ] **Step 2: Run and verify DevSpec test RED**

Run: `cd backend && .venv/bin/pytest -q tests/semantic_gate/test_devspec_gate.py`

Expected: FAIL because the current DevSpec has no R3.5 gate.

- [ ] **Step 3: Rewrite DevSpec**

Insert R3.5 after R3, mark downstream readiness evidence suspended, update R4/R5/R5.5/R6 dependencies, add the strict thresholds, prohibit `soft_ok` and intent scores as semantic evidence, and document the single mandatory runner. Do not mark R3.5 complete.

- [ ] **Step 4: Verify DevSpec contract GREEN**

Run: `cd backend && .venv/bin/pytest -q tests/semantic_gate/test_devspec_gate.py`

Expected: all DevSpec contract tests pass.

### Task 7: Final verification and handoff

**Files:**
- Modify only if verification finds defects in the test gate or documentation.

- [ ] **Step 1: Run all test-owned green checks**

Run:

```bash
cd backend
.venv/bin/pytest -q \
  tests/semantic_gate/test_strict_semantic_scorer.py \
  tests/semantic_gate/test_schema_factory.py \
  tests/semantic_gate/test_gate_runner.py \
  tests/semantic_gate/test_devspec_gate.py
```

Expected: all test-infrastructure and DevSpec tests pass.

- [ ] **Step 2: Run the complete gate**

Run: `cd backend && .venv/bin/python scripts/run_unseen_mysql_semantic_gate.py`

Expected current state: `NOT_PILOT_READY`. Planner/profiler/API semantic tests fail for documented production gaps; zero mandatory tests are silently skipped or xfailed.

- [ ] **Step 3: Confirm no production implementation changes**

Run: `git diff --name-only 813a356..HEAD` and `git status --short`.

Expected: this task changes only `backend/tests/semantic_gate/`, the gate runner, plan/spec documentation, and `dev_spec.md`; no files under `backend/app/` are modified by this task.

- [ ] **Step 4: Produce repair-model handoff**

Report the one mandatory command, current failing categories, gate thresholds, and the rule that success may only be claimed when the complete runner exits zero with `READY_FOR_INTERNAL_PILOT`.
