# V2 Run Event State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist real V2 lifecycle facts before SSE delivery and use database-backed active state and fixed 20-message context.

**Architecture:** A small run store owns run/event rows and broadcasts only committed records. `AnalysisApplicationService` becomes the V2-only entrypoint and records concrete boundaries around existing deterministic work; no workflow engine is introduced.

**Tech Stack:** FastAPI, SQLAlchemy Core, MySQL 8, React/TypeScript, pytest.

---

### Task 1: Run event store and schema

**Files:**
- Create: `config/migrations/009_run_events_and_active_state.sql`
- Create: `backend/app/services/run_store.py`
- Test: `backend/tests/test_run_store.py`

- [ ] **Step 1: Write failing storage tests**

```python
def test_append_event_commits_monotonic_sequence(store):
    run = store.create_run(session_id="s1", user_id=7, space_id="a", question="q")
    first = store.append_event(run["run_id"], kind="run", status="started")
    second = store.append_event(run["run_id"], kind="query", status="completed")
    assert [first["seq"], second["seq"]] == [1, 2]
```

- [ ] **Step 2: Run the test and verify it fails because the store is absent**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_run_store.py -q`

- [ ] **Step 3: Add the migration and minimal scoped store**

```python
event = store.append_event(run_id, kind="query", status="started", agent="query", step="execute")
assert event["seq"] > 0
```

- [ ] **Step 4: Run the store tests and verify they pass**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_run_store.py -q`

### Task 2: MySQL active analysis state

**Files:**
- Modify: `backend/app/agents/analysis_state_repository.py`
- Test: `backend/tests/semantic_query/test_analysis_state_repository.py`

- [ ] **Step 1: Write a failing stale-version test**

```python
assert repo.save(state, expected_version=1)["version"] == 2
assert repo.save(state, expected_version=1)["ok"] is False
```

- [ ] **Step 2: Verify the test fails on the file repository API**

Run: `backend/.venv/bin/python -m pytest backend/tests/semantic_query/test_analysis_state_repository.py -q`

- [ ] **Step 3: Replace the default repository with a scoped MySQL implementation**

```python
UPDATE active_analysis_states SET state_payload=:payload, version=version+1
WHERE session_id=:sid AND user_id=:uid AND space_id=:space AND version=:expected
```

- [ ] **Step 4: Re-run the state tests**

Run: `backend/.venv/bin/python -m pytest backend/tests/semantic_query/test_analysis_state_repository.py -q`

### Task 3: V2 lifecycle event stream and context cap

**Files:**
- Modify: `backend/app/application/analysis_service.py`
- Modify: `backend/app/application/contracts.py`
- Modify: `backend/app/services/persistence.py`
- Test: `backend/tests/semantic_query/test_v2_run_stream.py`

- [ ] **Step 1: Write failing V2 stream tests**

```python
events = [item async for item in service.handle_turn_stream(request)]
assert [data["seq"] for name, data in events if name == "run_event"] == sorted(...)
assert events[-1][1]["run_id"]
```

- [ ] **Step 2: Verify the tests fail because V2 currently waits for a blocking result**

Run: `backend/.venv/bin/python -m pytest backend/tests/semantic_query/test_v2_run_stream.py -q`

- [ ] **Step 3: Record and yield committed V2 boundaries; cap history at 20**

```python
async for event in run_events.stream(run_id, after_seq=0):
    yield "run_event", event
```

- [ ] **Step 4: Re-run V2 stream tests**

Run: `backend/.venv/bin/python -m pytest backend/tests/semantic_query/test_v2_run_stream.py -q`

### Task 4: Replay API and frontend idempotency

**Files:**
- Modify: `backend/app/api/chat.py`
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/App.tsx`
- Test: `backend/tests/test_run_events_api.py`

- [ ] **Step 1: Write the failing replay API test**

```python
response = client.get(f"/api/runs/{run_id}/events?after_seq=1", headers=auth)
assert [event["seq"] for event in response.json()["events"]] == [2]
```

- [ ] **Step 2: Verify the API test fails with 404**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_run_events_api.py -q`

- [ ] **Step 3: Add the scoped replay route and frontend `run_id:seq` reducer**

```ts
if (seen.has(`${event.run_id}:${event.seq}`)) return previous;
```

- [ ] **Step 4: Verify backend and frontend checks**

Run: `backend/.venv/bin/python -m pytest -q && npm run build && npm run lint`

### Task 5: Make V2 the only production route

**Files:**
- Modify: `backend/app/application/analysis_service.py`
- Modify: `backend/app/application/kernel_route.py`
- Modify: `backend/tests/test_v2_only_runtime.py`

- [ ] **Step 1: Write a failing factory test**

```python
assert get_analysis_service().kernel_route == KERNEL_V2
```

- [ ] **Step 2: Verify the test fails when legacy is configured**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_v2_only_runtime.py -q`

- [ ] **Step 3: Remove legacy selection from production factory and mark old graph as deprecated**

```python
def get_analysis_service() -> AnalysisApplicationService:
    return AnalysisApplicationService(kernel_route=KERNEL_V2, flags=default_flags())
```

- [ ] **Step 4: Run the complete suite**

Run: `backend/.venv/bin/python -m pytest -q`
