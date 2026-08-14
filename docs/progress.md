# Implementation Progress

This document records implementation state and verification evidence. Each phase has an owning specification under `docs/specs/`; code changes update this document in the same commit.

## Current milestone

Phase 3 establishes trusted local dynamic plugin management. Backend-only, client-only, and full-stack artifacts can be installed, enabled, disabled, updated, rolled back, and inspected while the process is running.

## Phase status

| Phase | Specification | State | Evidence | Next action |
|---|---|---|---|---|
| 1. PyCordis kernel | [Cordis core](specs/cordis-core.md) | Complete | 15 focused lifecycle and Event tests | Preserve behavior through conformance tests as later runtimes integrate it |
| 2. Backend Agent spine | [Agent Spine](specs/agent-spine.md) | Complete | 14 Agent tests within the 29-test suite; Ruff and strict Pyright | Preserve the Event Log authority when persistent storage is added |
| 3. Dynamic Plugin Manager | [Plugin Manager](specs/plugin-manager.md) | Complete | 11 manifest and Manager tests within the 40-test suite; Ruff and strict Pyright | Replace the trusted in-process BackendHost before admitting third-party code |
| 4. Browser bridge | [Browser Bridge](specs/browser-bridge.md) | Specified | Identity, reconciliation, bundle, RPC, Event, and failure requirements | Define normative JSON Schemas and Python protocol values first |

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

## Phase 3 delivered behavior

- Strict TOML manifests for backend-only, client-only, and full-stack plugins.
- Contained artifact resolution that rejects absolute paths, traversal, and escaping symlinks.
- SHA-256 revisions over manifest, backend, client, and protocol bytes.
- Revision-qualified trusted Python modules mounted as child PyCordis Fibers.
- Immutable client bundle publication without claiming browser activation.
- Serialized install, enable, disable, update, rollback, uninstall, and inventory snapshots.
- Required-contribution rollback and optional-contribution degraded state.

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

## Current limitations

- `InProcessBackendHost` executes trusted local Python code. It removes module lookup entries and disposes Fibers, but cannot guarantee code eviction; third-party plugins require a process-backed Host.
- Installed inventory and Session Events are in memory only.
- Client publication is process-local bytes. No browser receives or activates a bundle until Phase 4 supplies the bridge.
