# Productization Roadmap and Durable Session Specification

English | [中文](productization-roadmap.zh.md)

Status: Phase 12 normative specification

## Productization order

The foundation phases establish lifecycle and wire semantics. Productization adds user-facing durability and operational capabilities in dependency order:

1. Durable Session events and read-only Session projection.
2. Persistent plugin inventory and restart reconciliation.
3. API/JSON-RPC/ACP service assembly.
4. Credentials, settings, identity, approval, and authenticated control.
5. Filesystem, subprocess, shell, terminal, sandbox, compaction, subagent, and workflow capabilities.
6. Remote distribution, signatures, dependency resolution, and isolated backend execution.

Each item remains a plugin capability over the existing PyCordis and Browser Bridge foundations.

## Phase 12 scope

Phase 12 adds an optional SQLite-backed Session Store. `SessionLog` remains the authoritative append-only API and projection input. When configured, one event is committed to SQLite before it becomes visible in the in-memory snapshot; a failed write therefore cannot leave a partially durable event.

The store uses one process-local SQLite database with a monotonic schema version and one event table keyed by `(session_id, sequence)`. Event payloads are strict JSON with explicit event tags. Unknown tags, malformed payloads, non-finite numbers, sequence gaps, duplicate sequences, and session identity mismatches fail startup or append loudly.

Host configuration accepts `--session-db PATH`. The parent directory is created when needed. Without the option, behavior remains process-local memory. Startup loads the selected Session before accepting invocations; existing history participates in the next Agent request exactly as if the process had not stopped.

The Host exposes `GET /api/v1/sessions/{session_id}` for the active Session only. The response contains the ordered event envelopes and deterministic transcript projection. It is a local read API, not a remote multi-user Session service; authentication, pagination, redaction policy, and write operations remain later phases.

## Failure and lifecycle

- SQLite open, schema, decode, or integrity failures abort Host startup before the listener binds.
- A failed append aborts the Agent Step and does not add the event to the in-memory log.
- Concurrent appends serialize through the Session Store connection and retain monotonic sequences.
- Session cleanup closes the database through the owning PyCordis Effect after invocation cancellation and before runtime teardown completes.
- The format version is `0` with no compatibility promise; schema changes require an explicit monotonic schema version.

## Acceptance

- A fresh database persists all supported Session Event variants and reloads them with equal projections.
- Restarting a Host with the same `session_id` resumes model history and exposes the previous transcript.
- Corrupt JSON, unknown event tags, sequence gaps, and conflicting appends fail without partial memory state.
- The Session HTTP route returns ordered immutable observations and rejects another Session ID.
- Existing in-memory tests and all foundation checks remain green.

## Exclusions

Persistent Plugin Inventory, multi-Session routing, migration tooling, compaction, retention, encryption, access control, remote streaming, and distributed locking are outside Phase 12.
