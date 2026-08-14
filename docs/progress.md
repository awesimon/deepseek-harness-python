# Implementation Progress

This document records implementation state and verification evidence. Each phase has an owning specification under `docs/specs/`; code changes update this document in the same commit.

## Current milestone

Phase 2 establishes the durable Agent Spine on PyCordis. Session projection, scoped Prompt and Tool contributions, explicit LLM routing, and the multi-Step Tool loop are implemented and verified without API credentials.

## Phase status

| Phase | Specification | State | Evidence | Next action |
|---|---|---|---|---|
| 1. PyCordis kernel | [Cordis core](specs/cordis-core.md) | Complete | 15 focused lifecycle and Event tests | Preserve behavior through conformance tests as later runtimes integrate it |
| 2. Backend Agent spine | [Agent Spine](specs/agent-spine.md) | Complete | 14 Agent tests within the 29-test suite; Ruff and strict Pyright | Preserve the Event Log authority when persistent storage is added |
| 3. Dynamic Plugin Manager | [Plugin Manager](specs/plugin-manager.md) | Specified | Manifest, revision, lifecycle, rollback, and trusted-host requirements | Implement manifest validation and immutable revision building first |
| 4. Browser bridge | Required before implementation | Not started | None | Define protocol versioning, RPC, event forwarding, client graphs, and reconciliation |

## Phase 1 delivered behavior

- Name-based typed Service keys and isolation Realms.
- Dependency-driven Fiber activation and Provider generation epochs.
- Consumer deactivation before Provider cleanup and reactivation after replacement.
- Failure rollback, explicit retry, and parent-child teardown.
- Single-shot Effects with reverse grouped cleanup and concurrent top-level cleanup.
- Emit, Parallel, Serial, and Waterfall Event modes.

## Phase 2 delivered behavior

- Immutable JSON-compatible Messages, Tool Calls, model requests, chunks, and responses.
- Monotonic append-only Session Events with deterministic model-history and transcript projections.
- Hierarchical Agent Scopes with global, ancestor, and exact-layer precedence.
- Effect-owned Prompt, Tool, and LLM registrations.
- JSON Schema Tool argument validation through `jsonschema`.
- Explicit provider/model LLM routing with exactly-one-terminal-response enforcement.
- Multi-Step Turn execution with durable request, chunk, response, Tool start, Tool outcome, and failure Events.
- Step capability snapshots that remain stable while plugin registrations change.
- Agent and Tool Waterfall/Parallel extension Events.

## Verification commands

```sh
uv run python -m unittest discover -s tests -v
uv run ruff check src tests
uv run pyright
uv run python -m compileall -q src tests
```

## Open architecture decisions

- Durable Python Session storage format and compatibility with TypeScript Session format version 0.
- Backend plugin worker granularity and restart policy.
- Wire IDL and Python/TypeScript binding generator.
- Multi-page browser activation and reconciliation policy.
