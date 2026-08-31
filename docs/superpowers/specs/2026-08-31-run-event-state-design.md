# V2 Run Event and Database State Design

## Goal

Make the V2 analysis path the only production path, persist a small replayable run-event timeline before SSE delivery, and move active analysis state from local JSON files to MySQL.

## Scope

- One user analysis request creates one `analysis_runs` record.
- Each public lifecycle fact is appended to `run_events` with a monotonic sequence number.
- SSE is a transport projection of committed events; reconnects read events after a supplied sequence.
- `ActiveAnalysisState` is stored under `(session_id, user_id, space_id)` with optimistic versions.
- Model-facing short-term context is exactly the latest 20 chat messages.
- `analysis_kernel_v2` is the only runtime route. Legacy LangGraph source remains unused and deprecated until 2026-10-31, then is removed in a follow-up deletion change.

## Deliberately out of scope

- No generic workflow engine, message broker, or DAG scheduler.
- No raw chain-of-thought, prompts, credentials, or full result rows in event payloads.
- No automatic cross-session memory retrieval.

## Data model

`analysis_runs` is scoped by session, user, and space. It owns status, `next_seq`, and terminal metadata. `run_events` has primary key `(run_id, seq)` and stores only safe UI facts: kind, status, agent, step, artifact reference, JSON payload, and timestamp.

Event allocation locks the parent run row, increments `next_seq`, inserts the event, and commits before publishing it to local SSE subscribers. Multiple server instances can therefore replay the authoritative database timeline even though only the creating process receives the immediate queue notification.

`active_analysis_states` stores the serialized existing `ActiveAnalysisState`, its catalog identity and expiry, and a version. Updates use `WHERE version = expected_version`; a conflicting request reloads the state and returns a safe conflict rather than overwriting a newer follow-up.

## V2 execution flow

1. Stream creates a run and emits committed `run.started`.
2. The service records actual start/completion/failure facts around supervisor, semantic parsing, SQL compile, query execution, and diagnosis procedure.
3. The stream forwards committed facts in sequence order and sends a single terminal `complete` payload containing `run_id`.
4. The frontend stores events by `run_id:seq`; duplicate delivery is ignored and reconnect replay fills gaps.

## Context policy

The shared context loader returns the latest 20 messages scoped by session, user, and space. V2 supervisor and answer generation use this loader. The existing semantic active state is injected separately as structured state, not by serializing historical raw query rows.

## Failure policy

Run/event persistence failures end the stream with a safe error; they are not silently ignored. A failed lifecycle operation records its failure event when the database remains available. The terminal SSE guard continues to enforce exactly one `complete` event.

## Tests

- run event sequences are monotonic and replayable;
- duplicate frontend event application is idempotent;
- V2 stream emits persisted lifecycle facts before terminal completion;
- database active state rejects a stale version;
- V2 context loader caps history at 20 messages;
- legacy route is no longer selectable in the production factory.
