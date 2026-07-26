# Unseen MySQL Semantic Gate

- status: **NOT_PILOT_READY**
- commit_sha: `c83df41ef1710be62d4a02d8144c71c8a8e09cb2`
- working_tree_clean: `False`
- tests: 52
- failures: 28
- errors: 0
- skipped: 0

## Hard failure signals

- guessed_when_clarification_required: 6
- missing_or_wrong_dimension: 0
- missing_or_wrong_filter: 34
- missing_or_wrong_measure: 246
- sensitive_raw_value_persisted: 0
- wrong_reuse_decision: 0
- wrong_sql_tables: 58
- wrong_subject_table: 58

## Per-family gate tests

- ambiguous: passed=0 failed=3 skipped=0
- billing: passed=0 failed=3 skipped=0
- commerce: passed=0 failed=3 skipped=0
- iot: passed=0 failed=3 skipped=0
- support: passed=0 failed=3 skipped=0
- warehouse: passed=0 failed=3 skipped=0

## Failing tests

- `tests.semantic_gate.test_api_gate::test_real_mysql_service_scores_sql_spec_and_value_not_http_only`
- `tests.semantic_gate.test_api_gate::test_real_mysql_ground_truth_count_across_all_schema_families`
- `tests.semantic_gate.test_followup_gate::test_real_anaphoric_followup_phrases_are_recognized`
- `tests.semantic_gate.test_followup_gate::test_changed_followup_produces_semantic_patch_not_reuse`
- `tests.semantic_gate.test_followup_gate::test_group_topn_followup_changes_dimension_order_and_limit`
- `tests.semantic_gate.test_planner_gate::test_unseen_schema_planner_family_gate[20260726-commerce]`
- `tests.semantic_gate.test_planner_gate::test_unseen_schema_planner_family_gate[20260726-billing]`
- `tests.semantic_gate.test_planner_gate::test_unseen_schema_planner_family_gate[20260726-support]`
- `tests.semantic_gate.test_planner_gate::test_unseen_schema_planner_family_gate[20260726-iot]`
- `tests.semantic_gate.test_planner_gate::test_unseen_schema_planner_family_gate[20260726-warehouse]`
- `tests.semantic_gate.test_planner_gate::test_unseen_schema_planner_family_gate[20260726-ambiguous]`
- `tests.semantic_gate.test_planner_gate::test_unseen_schema_planner_family_gate[731921-commerce]`
- `tests.semantic_gate.test_planner_gate::test_unseen_schema_planner_family_gate[731921-billing]`
- `tests.semantic_gate.test_planner_gate::test_unseen_schema_planner_family_gate[731921-support]`
- `tests.semantic_gate.test_planner_gate::test_unseen_schema_planner_family_gate[731921-iot]`
- `tests.semantic_gate.test_planner_gate::test_unseen_schema_planner_family_gate[731921-warehouse]`
- `tests.semantic_gate.test_planner_gate::test_unseen_schema_planner_family_gate[731921-ambiguous]`
- `tests.semantic_gate.test_planner_gate::test_unseen_schema_planner_family_gate[904177-commerce]`
- `tests.semantic_gate.test_planner_gate::test_unseen_schema_planner_family_gate[904177-billing]`
- `tests.semantic_gate.test_planner_gate::test_unseen_schema_planner_family_gate[904177-support]`
- `tests.semantic_gate.test_planner_gate::test_unseen_schema_planner_family_gate[904177-iot]`
- `tests.semantic_gate.test_planner_gate::test_unseen_schema_planner_family_gate[904177-warehouse]`
- `tests.semantic_gate.test_planner_gate::test_unseen_schema_planner_family_gate[904177-ambiguous]`
- `tests.semantic_gate.test_planner_gate::test_explicit_table_identifier_grounding_is_100_percent[20260726]`
- `tests.semantic_gate.test_planner_gate::test_explicit_table_identifier_grounding_is_100_percent[731921]`
- `tests.semantic_gate.test_planner_gate::test_explicit_table_identifier_grounding_is_100_percent[904177]`
- `tests.semantic_gate.test_profiler_gate::test_profiler_emits_bounded_evidence_status_vocabulary_and_sample_strategy`
- `tests.semantic_gate.test_profiler_gate::test_profiler_records_budget_and_evidence_source`
