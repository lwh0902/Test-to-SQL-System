# Unseen MySQL Semantic Gate Design

**Status:** design approved in principle; implementation pending written-spec review  
**Date:** 2026-07-26  
**Scope:** MySQL only

## 1. Outcome

DataPilot must not be judged by whether a request returned HTTP 200, produced SQL, or reached a terminal state. It is successful only when it either:

1. produces a semantically correct result from a previously unseen MySQL schema; or
2. explicitly asks for the missing information when the available evidence cannot uniquely support a query.

A confident answer built from the wrong table, measure, filter, dimension, time range, join, or reused state is always a hard failure.

This gate replaces the current assumption that passing intent, routing, Harness, and chain-health tests is sufficient for pilot readiness.

## 2. Meaning of “Connect Any MySQL”

Within the first pilot, “connect any MySQL” means that no database-specific YAML, table-name branch, fixture identifier, or code change is required for these supported operations:

- `COUNT`, `SUM`, `AVG`, `MIN`, and `MAX`;
- explicit and inferred subject-table selection;
- equality, boolean, enum, numeric-range, and time-range filters;
- grouping and Top N;
- conditional counts and rates;
- questions requesting multiple measures;
- safe high-confidence joins;
- follow-up changes to measure, dimension, filter, time, and Top N;
- clarification or refusal when subject, measure, relationship, or time is underdetermined.

It does not mean that an undocumented database can yield every company-specific KPI automatically. Custom concepts such as “active customer”, “valid revenue”, or “high-quality lead” require a discoverable definition or user confirmation. The system must expose this limitation instead of silently inventing a definition.

## 3. Recommended Architecture

### 3.1 Bounded profiling pipeline

The connection bootstrap builds an evidence-bearing `SemanticCatalog` in this order:

1. Read `information_schema`: tables, columns, types, comments, primary keys, foreign keys, and indexes.
2. Classify likely sensitive columns before retrieving values.
3. Collect bounded column profiles: null ratio, approximate distinct ratio, numeric bounds, time bounds, boolean distribution, and low-cardinality Top K values.
4. Collect at most 100 dispersed, masked sample rows per table only when structural and aggregate evidence is insufficient.
5. Infer table roles, field roles, aliases, candidate measures, candidate dimensions, status vocabularies, and relationship confidence.
6. Store the derived profile and evidence metadata; do not persist raw sample rows.

The physical first 100 rows must not be the sole semantic evidence. Where possible, sampling is dispersed by indexed numeric key or time range. If no safe indexed strategy exists, a bounded `LIMIT` sample may be used, but it lowers confidence.

Every profiling statement is read-only and subject to a per-query timeout, table budget, column budget, and total connection-bootstrap budget. Profiling failure yields `DEGRADED` or `BLOCKED`; it must not be hidden.

### 3.2 Evidence-bearing semantic catalog

Each inferred item records:

- the inferred role or alias;
- confidence score;
- evidence sources: metadata, type, key, distribution, Top K, masked pattern, or user confirmation;
- catalog version and schema fingerprint;
- expiry and invalidation reason;
- whether the item may be used automatically or requires confirmation.

User-confirmed semantics outrank automatic inference. Schema changes invalidate affected inferences and active analysis state.

### 3.3 Grounding before SQL

The Planner resolves a question in a fixed order:

1. exact table or column identifier named by the user;
2. confirmed aliases and business labels;
3. catalog-driven candidate ranking;
4. bounded semantic inference from profiles;
5. clarification if the best candidate is below threshold or the score margin is too small.

Measure selection is conditioned on the resolved subject. A global “first candidate measure” fallback is forbidden. `COUNT` anchors to the subject table’s primary key or `COUNT(*)` rather than selecting an unrelated measure table.

The output is a structured `AnalysisSpec`. SQL compilation is allowed only after the Spec passes subject, field-ownership, relationship, filter-value, grain, and ambiguity validation.

### 3.4 Follow-up and reuse

`SupervisorDecision.intent=follow_up` is authoritative for entering Spec patching. The patcher must not independently downgrade it using a smaller keyword regex.

Follow-up operations are structured changes:

- add/replace/remove dimension;
- add/replace/remove filter;
- change time range;
- change measure;
- add Top N ordering;
- add conditional count or derived rate;
- add a second requested measure.

Result reuse is allowed only after a valid patch has been constructed and the canonical new Spec is semantically identical to the previous Spec. Failure to interpret a follow-up produces clarification, never reuse.

## 4. Test-Set Design

### 4.1 Two complementary suites

The gate contains two suites; both are mandatory.

**Deterministic regression suite**

- stable schemas, seeds, questions, and Ground Truth;
- reproducible locally and in CI;
- contains every currently observed failure pattern;
- makes debugging failures straightforward.

**Metamorphic unseen-schema suite**

- generates equivalent databases with deterministic random table and column identifiers;
- varies English, Chinese, abbreviated, opaque, commented, and uncommented names;
- changes column order, table creation order, row insertion order, and non-semantic noise columns;
- runs multiple seeds and proves the answer is invariant under irrelevant schema transformations;
- supports an additional CI seed through `DATAPILOT_UNSEEN_SEED` without changing production code.

Production files are scanned for fixture-specific identifiers. Adding branches for generated table names, dataset IDs, or seed values is a gate failure.

### 4.2 Schema families

At least six unrelated families are required:

1. commerce orders, items, products, refunds, and traffic;
2. subscription billing, plans, invoices, and payments;
3. support tickets, agents, statuses, and resolution events;
4. IoT devices, readings, alarms, and sites;
5. warehouse inventory, movements, products, and locations;
6. deliberately ambiguous ledgers where safe behavior is clarification.

At least two families use Chinese identifiers, two use conventional English identifiers, and two use opaque or abbreviated identifiers whose meaning can only be inferred through profiles and relationships.

None of the current `ecommerce` or `tech_quality` tables is counted toward the unseen-schema pass rate. They remain regression evidence only.

### 4.3 Required capabilities

The suite contains at least 120 single-turn tasks and 40 multi-turn chains. Every schema family covers applicable cases from the following matrix:

- explicit table count;
- business-noun subject resolution;
- sum/average/min/max measure resolution;
- boolean and enum filters;
- time filtering and missing-time clarification;
- grouping and Top N;
- conditional count and rate;
- multiple measures in one question;
- safe join and ambiguous join;
- short and anaphoric follow-up;
- semantically changed follow-up that must not reuse;
- truly identical follow-up that should reuse;
- empty result;
- schema evolution invalidation;
- write, injection, and unsafe profiling rejection;
- sensitive-field masking and non-persistence.

One profiling trap places a rare failure status outside the first 100 physical rows. An implementation that relies only on `SELECT * LIMIT 100` must fail.

### 4.4 Test layers

The same contracts are checked at four layers:

1. **Profiler contract:** MySQL to evidence-bearing `SemanticCatalog`.
2. **Planner contract:** question plus Catalog to `AnalysisSpec` or clarification.
3. **Compiler/executor contract:** Spec to guarded SQL and Ground Truth result.
4. **Public API contract:** authenticated JSON and SSE paths with Session state, Trace, and terminal status.

Component-only success cannot substitute for the public API gate. The API gate provisions real MySQL schemas, profiles them through the production entry point, and performs queries using the production v2 path.

## 5. Strict Scoring

Each answer case has explicit expectations for:

- terminal behavior;
- subject table;
- all SQL tables;
- measures and aggregations;
- filters and values;
- dimensions;
- time range;
- join path and grain;
- ordering and limit;
- result values;
- reuse decision;
- clarification slots when applicable.

`full_correct` is the conjunction of every applicable dimension. HTTP status, fluent text, `SUCCESS_WITH_DATA`, artifact count, or intent accuracy never implies semantic correctness.

These are non-compensable hard failures:

- wrong-table answer returned as success;
- missing required filter, dimension, time range, or requested measure;
- unsafe or low-confidence join executed automatically;
- changed semantics served from previous results;
- guessed answer where clarification is required;
- raw sensitive values stored in Catalog, artifacts, logs, or reports;
- write-capable or unbounded profiling SQL.

## 6. Release Gates

All thresholds apply per schema family as well as overall; a strong family cannot hide a weak family.

- explicit identifier grounding: 100%;
- wrong-table successful answers: 0;
- full semantic correctness: at least 95%;
- Ground Truth value match: at least 98%;
- ambiguity clarification/refusal: 100%;
- missing required information silently guessed: 0;
- follow-up semantic patch correctness: at least 98%;
- changed-follow-up wrong reuse: 0;
- multi-measure complete answer or explicit clarification: 100%;
- safe relationship auto-join correctness: at least 95%;
- unsafe/ambiguous auto-joins: 0;
- write and injection blocking: 100%;
- sensitive raw-value persistence: 0;
- profiler budget and timeout violations: 0;
- JSON/SSE semantic equivalence: 100%;
- all mandatory tests pass in one terminal run against a recorded commit and clean working tree.

Any mandatory test failure keeps the status `NOT_PILOT_READY`. Tests may not be marked optional, xfail, skipped, or excluded from the aggregate gate because the implementation does not yet support them. Infrastructure failures are reported separately and do not become semantic passes.

## 7. DevSpec Integration

Add a new **Recovery Phase 3.5: Unseen MySQL Profiling and Semantic Grounding Gate** immediately after R3 and before R4.

Because R4–R5.5 were built on an R3 foundation that did not satisfy the real semantic gate, their existing implementation may remain, but their pilot-readiness evidence is suspended until R3.5 passes. Intent-routing and Harness reports remain useful component evidence; they are not query-correctness evidence.

R3.5 must specify:

- bounded profiling and sensitive-data policy;
- evidence-bearing Catalog contract;
- catalog-driven subject, measure, dimension, filter, and relationship grounding;
- structured follow-up patching and safe reuse;
- deterministic and metamorphic unseen-schema suites;
- strict scorer and non-compensable failure rules;
- the thresholds in Section 6;
- a single mandatory runner and machine-readable report;
- prohibition on fixture-specific production branches;
- no progression to R4–R6 or business-user testing before the gate is green.

The current R3, R4, and R6 language must be updated so that “four databases”, component tests, intent accuracy, `soft_ok`, or chain-health evidence cannot satisfy this phase.

## 8. Planned Files

The implementation plan will use the existing pytest and eval conventions and is expected to create or modify these focused units:

- `backend/tests/semantic_gate/schema_factory.py`: deterministic and metamorphic MySQL families;
- `backend/tests/semantic_gate/case_matrix.py`: questions and structured expectations;
- `backend/tests/semantic_gate/assertions.py`: strict semantic assertions;
- `backend/tests/semantic_gate/test_profiler_gate.py`: profiling, masking, budgets, and evidence;
- `backend/tests/semantic_gate/test_planner_gate.py`: AnalysisSpec and clarification contracts;
- `backend/tests/semantic_gate/test_followup_gate.py`: state patch and reuse contracts;
- `backend/tests/semantic_gate/test_api_gate.py`: real MySQL public JSON/SSE chain;
- `backend/tests/semantic_gate/test_anti_overfit.py`: renaming invariance and fixture-identifier scan;
- `backend/scripts/run_unseen_mysql_semantic_gate.py`: mandatory runner and JSON/Markdown report;
- `dev_spec.md`: R3.5 and downstream admission changes.

Production changes are intentionally outside this task. The tests must first fail against the current implementation and become the acceptance contract for the model that performs the repair.

## 9. Completion Definition

This test-and-spec task is complete when:

1. the mandatory suite exists and fails for the current semantic defects for the intended reasons;
2. the runner reports all semantic dimensions rather than chain health;
3. `dev_spec.md` makes the suite a non-bypassable prerequisite for pilot readiness;
4. the future repair model can run one documented command to determine pass or fail;
5. no production query-planning code has been changed as part of creating the gate.
