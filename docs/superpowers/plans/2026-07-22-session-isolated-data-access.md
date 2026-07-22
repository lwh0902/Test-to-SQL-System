# Session-Isolated Data Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver working MySQL connection onboarding, reliable LLM routing fallback, and session-isolated short/long-term context.

**Architecture:** Add transient connection endpoints to the existing connections router. Keep EngineRegistry as the runtime connection boundary. Store a compact memory summary per owned session, use it only during follow-up resolution, and replace undeclared message ordering with stable timestamp ordering.

**Tech Stack:** FastAPI, SQLAlchemy, MySQL, LangGraph, Anthropic-compatible LLM, pytest, React/Vite.

---

### Task 1: Connection onboarding endpoints

**Files:** `backend/tests/test_connections_api.py`, `backend/app/api/connections.py`

- [ ] Add tests asserting `/test-direct` and `/discover-schema` call their service functions and return their results.
- [ ] Run the new tests and confirm the routes are absent/failing before implementation.
- [ ] Import `test_direct_connection` and `discover_schema_direct`, then add rate-limited authenticated POST endpoints.
- [ ] Re-run the endpoint tests.

### Task 2: Session schema and short-term ordering

**Files:** `config/migrations/004_session_memory.sql`, `config/init_system_tables.sql`, `backend/tests/test_session_memory.py`, `backend/app/services/persistence.py`, `backend/app/services/session_service.py`

- [ ] Add failing tests for six-turn message loading and session-owned long-memory lookup.
- [ ] Add the migration/table definitions and change reads to `created_at, id` ordering.
- [ ] Add persistence helpers that only select/update by `session_id`, `user_id`, and `space_id`.
- [ ] Re-run the focused tests.

### Task 3: Long-memory compaction

**Files:** `backend/tests/test_session_memory.py`, `backend/app/services/session_memory_service.py`, `backend/app/services/agent.py`, `backend/app/services/persistence.py`, `backend/app/services/session_service.py`

- [ ] Add failing tests for session isolation and no-summary fallback.
- [ ] Implement a bounded LLM summary service with a safe deterministic fallback, and only persist it after six user turns.
- [ ] Inject the summary exclusively into follow-up resolution and delete it with the session.
- [ ] Re-run focused tests.

### Task 4: Safe route fallback

**Files:** `backend/tests/test_intent_router_fallback.py`, `backend/app/services/agent.py`

- [ ] Add failing tests for invalid/failed LLM classification, a context follow-up, and ambiguous input.
- [ ] Normalize LLM output; use deterministic contextual fallback; introduce a clarification route and responder.
- [ ] Re-run focused tests.

### Task 5: Delivery verification

**Files:** affected backend and frontend files

- [ ] Run the full backend test suite.
- [ ] Run `npm run build` in `frontend`.
- [ ] Inspect the final diff and report the verified deployment prerequisites.
