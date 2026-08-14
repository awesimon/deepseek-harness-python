# Implementation Progress

This document records implementation state and verification evidence. Each phase has an owning specification under `docs/specs/`; code changes update this document in the same commit.

## Current milestone

Phase 1 establishes the PyCordis lifecycle kernel required by every backend capability. The kernel is implemented and its focused tests pass.

## Phase status

| Phase | Specification | State | Evidence | Next action |
|---|---|---|---|---|
| 1. PyCordis kernel | [Cordis core](specs/cordis-core.md) | Complete | 15 lifecycle and event tests; `compileall` | Preserve behavior through conformance tests as later runtimes integrate it |
| 2. Backend Agent spine | [Agent Spine](specs/agent-spine.md) | Specified | Normative lifecycle, logging, registry, routing, and failure requirements | Implement immutable values and the Session Log first |
| 3. Dynamic Plugin Manager | Required before implementation | Not started | None | Define manifests, discovery, validation, revisions, activation coordination, and aggregate status |
| 4. Browser bridge | Required before implementation | Not started | None | Define protocol versioning, RPC, event forwarding, client graphs, and reconciliation |

## Phase 1 delivered behavior

- Name-based typed Service keys and isolation Realms.
- Dependency-driven Fiber activation and Provider generation epochs.
- Consumer deactivation before Provider cleanup and reactivation after replacement.
- Failure rollback, explicit retry, and parent-child teardown.
- Single-shot Effects with reverse grouped cleanup and concurrent top-level cleanup.
- Emit, Parallel, Serial, and Waterfall Event modes.

## Verification commands

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m compileall -q src tests
```

## Open architecture decisions

- Python Session format and compatibility with TypeScript Session format version 0.
- Backend plugin worker granularity and restart policy.
- Wire IDL and Python/TypeScript binding generator.
- Multi-page browser activation and reconciliation policy.
