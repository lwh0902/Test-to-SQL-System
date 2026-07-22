# Session-Isolated Data Access and Memory Design

## Goal

Make the MySQL connection wizard usable end-to-end, keep conversational state reliable, and keep all retained memory isolated to one owned analysis session.

## Data access

The connection API accepts a transient credential payload only for connection testing and schema discovery. It never persists the payload during those calls. A persisted connection remains encrypted at rest and a user-created space selects that connection through `EngineRegistry`. Query execution stays read-only, table/column allow-listed, EXPLAIN checked, and time limited.

## Routing

The LLM remains the primary classifier. Its output is normalized to the allowed route set. If it fails, is empty, or returns an unknown route, the router uses deterministic context signals: explicit chat/help phrases, the session task frame, and follow-up wording. Ambiguous input produces `clarification`, rather than silently querying data.

## Memory

Short-term context is the six most recent conversation turns (twelve messages) plus a structured task frame. It is ordered by `created_at, id`; no undeclared sequence column is required.

Long-term memory is a compact LLM-generated summary stored by `(session_id, user_id, space_id)`. It is never read outside the same owned session. It contains only task continuity, approved business terms, and pending questions; it excludes raw rows, SQL parameters, credentials, and sensitive fields. The latest summary is included only in follow-up resolution. Deleting a session deletes its summary.

## Schema compatibility

The migration adds `session_memories`. Application reads use `created_at, id`, so databases created from the current bootstrap schema and deployed databases remain usable without an undeclared `seq` column.

## Verification

Unit tests cover transient connection endpoints, route fallback, session-scoped memory persistence/retrieval, and message ordering. The backend test suite and frontend production build are run before delivery.
