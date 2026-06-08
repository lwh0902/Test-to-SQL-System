# Data Map Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize the database understanding path so users can see database identity, inspect data maps visually, query tables, and get meaningful no-data diagnosis.

**Architecture:** Reuse the existing LangGraph agent and `working_memory` frame instead of adding a new task-frame store. Make table queries carry a structured table target, use `SYSTEM_SCHEMAS` as preset fallback, render `data_map` through a reusable React component, and verify no-data cases with fixed diagnostic SQL instead of rewriting generated SQL.

**Tech Stack:** FastAPI, SQLAlchemy, LangGraph, pytest, React, TypeScript, Ant Design, Vite.

---

### Task 1: Fix Safe Sampling

**Files:**
- Modify: `backend/app/services/data_map_service.py`
- Test: `backend/tests/test_safe_sample.py`

- [x] Move `engine_registry` to module scope so tests and callers can patch it consistently.
- [x] Reject unsafe dynamic table identifiers before composing sample SQL.
- [x] Run `backend/.venv/bin/python -m pytest backend/tests/test_safe_sample.py`.

### Task 2: Preserve Table Query Context

**Files:**
- Modify: `backend/app/services/agent.py`
- Modify: `backend/app/services/persistence.py`
- Test: `backend/tests/test_table_query_handler.py`
- Test: `backend/tests/test_working_memory_frame.py`

- [x] Add `AgentState.table_target`.
- [x] Resolve table targets from current-space schema aliases.
- [x] Load `SYSTEM_SCHEMAS` for preset spaces when `analysis_spaces.db_schema` is empty.
- [x] Persist table query frames with `last_target_type = "table"` and `last_target = table_name`.
- [x] Add regression tests for preset schema fallback and table-target memory.

### Task 3: Stabilize No-Data Diagnosis

**Files:**
- Modify: `backend/app/services/agent.py`
- Test: `backend/tests/test_diagnosis_handler.py`
- Test: `backend/tests/test_table_query_handler.py`

- [x] Stop rewriting generated SQL with regex to diagnose no-data results.
- [x] Diagnose only known tables from the current space schema.
- [x] Use fixed checks: table exists, total rows, latest timestamp, and sample existence.
- [x] Make `query_executor` support `table_query` without `QueryIntent`.
- [x] Add regression tests for table-query execution with and without rows.

### Task 4: Render Data Maps as Shared UI

**Files:**
- Create: `frontend/src/components/DataMapBlock.tsx`
- Modify: `frontend/src/components/ChatMessage.tsx`
- Modify: `frontend/src/App.tsx`

- [x] Extract data map cards into a reusable `DataMapBlock`.
- [x] Render database identity, recommended questions, and table cards in the shared component.
- [x] Use compact mode inside chat messages.
- [x] Wire data-map recommended questions to `handleQuestionClick`.

### Task 5: Restore Build Health

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/SpaceCreateModal.tsx`
- Modify: `frontend/src/components/TracePanel.tsx`

- [x] Return session lists from `loadSessions`.
- [x] Use `session.id` consistently.
- [x] Fill required `ChatResponse` fields for error messages.
- [x] Replace invalid `Typography.Divider` with `Divider`.
- [x] Fix `unknown` JSX rendering in trace output.

### Verification

- [x] Backend: `backend/.venv/bin/python -m pytest backend/tests` → 99 passed.
- [x] Frontend: `npm run build` in `frontend` → TypeScript and Vite build passed.

### Remaining Follow-Ups

- [ ] Make table profiling asynchronous with task status and loading UI.
- [ ] Add table relationship inference as P2, clearly labeled as inferred relationships.
- [ ] Add a dedicated database identity intent so "现在接入的数据库是什么" can answer without always rendering the full data map.
