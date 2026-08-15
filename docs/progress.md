# Implementation Progress

This document records implementation state and verification evidence. Each phase has an owning specification under `docs/specs/`; code changes update this document in the same commit.

## Current milestone

Phase 6 defines the supported plugin author APIs over PyCordis and Cordis TS. Phase 7 templates depend on that API; Phase 8 multi-page readiness can proceed independently after its state model is implemented.

## Phase status

| Phase | Specification | State | Evidence | Follow-up scope |
|---|---|---|---|---|
| Foundation. Python package layout | [Package Layout](specs/package-layout.md) | Complete | Direct `harness` import, editable install, wheel install, and rejection of `deepseek_harness` | Keep distribution metadata separate from the import namespace |
| 1. PyCordis kernel | [Cordis Core](specs/cordis-core.md) | Complete | 15 focused lifecycle and Event tests | Preserve lifecycle behavior through conformance tests |
| 2. Backend Agent Spine | [Agent Spine](specs/agent-spine.md) | Complete | 14 Agent tests and strict static checks | Add durable Session storage as a separate capability |
| 3. Dynamic Plugin Manager | [Plugin Manager](specs/plugin-manager.md) | Complete | 11 manifest and Manager tests plus the full-stack lifecycle | Replace the trusted in-process Host before admitting third-party code |
| 4. Browser Bridge | [Browser Bridge](specs/browser-bridge.md) | Complete | 12 Python protocol/Bridge/transport tests, 7 TypeScript tests, and the full-stack lifecycle | Add multi-page activation aggregation only under its own specification |
| 5. Host Assembly | [Host Assembly](specs/host-assembly.md) | Complete | 5 Host tests and a real Chromium update/disable scenario over HTTP/WebSocket | Specify the plugin SDK and authoring templates |
| 6. Plugin Authoring SDK | [Plugin SDK](specs/plugin-sdk.md) | Specified | Normative Python, TypeScript, protocol-helper, and test-harness requirements | Implement the SDK before templates consume it |
| 7. Plugin Templates | [Plugin Templates](specs/plugin-templates.md) | Specified | Normative layouts, CLI, deterministic generation, and downstream checks | Implement after Phase 6 public APIs stabilize |
| 8. Multi-Page Activation | [Multi-Page Activation](specs/multi-page-activation.md) | Specified | Normative membership, quorum, readiness, diagnostics, and Chromium requirements | Implement independently of authoring tooling |

## Delivered foundation

- Name-based typed PyCordis Services, isolation Realms, dependency-driven Fiber activation, Provider replacement, recursive teardown, and reversible Effects.
- Emit, Parallel, Serial, and Waterfall Event modes with Effect-owned listeners.
- Immutable Agent values, append-only Session Events, deterministic projections, scoped Prompt and Tool registries, explicit LLM routing, and multi-Step Tool execution.
- Strict root plugin manifests, contained artifact resolution, content-addressed Revision identity, and backend-only, client-only, or full-stack contribution forms.
- Serialized runtime install, enable, disable, update, rollback, uninstall, and immutable inventory snapshots.
- Isolated `PLUGIN_RUNTIME_IDENTITY` Services for exact backend Bridge registration without user-configured identity.
- Normative Browser Bridge JSON Schema and shared Python/TypeScript fixtures.
- Exact bundle and plugin protocol Schema delivery, automatic full-graph reconciliation, page-local state, stale-result rejection, cancellable RPC, and ordered explicit Events.
- aiohttp HTTP/WebSocket transport with duplicate Page ID replacement and connection-owned cleanup.
- Browser fetch, SHA-256 verification, dynamic module import, real Cordis TS Fiber mounting, replacement, failure rollback, and unload cleanup.
- Keyless lifecycle coverage for enable, page activation, backend RPC, bidirectional Events, dual-contribution update, stale-call rejection, and disable.
- Runnable Host composition with catalog activation, assigned ports, browser bootstrap delivery, command-line entrypoints, startup rollback, and deterministic shutdown.
- Per-page reconciliation backpressure that coalesces publication changes until the active operation completes.
- Real Chromium evidence that the assembled Host activates, updates, rejects stale calls, disables, and tears down a full-stack plugin.

## Verification commands

```sh
uv lock --check
uv run python -m unittest discover -s tests -v
uv run ruff check harness tests
uv run pyright
uv run python -m compileall -q harness tests
uv build

pnpm --dir frontend run typecheck
pnpm --dir frontend run test
pnpm --dir frontend run build
```

Current automated count: 60 Python tests and 7 TypeScript tests.

## Next milestone

Implement Phase 6 Plugin Authoring SDK and Phase 8 Multi-Page Activation as independent work streams. Begin Phase 7 only after the SDK exports and test harnesses pass their acceptance criteria.

## Intentional exclusions

- `InProcessBackendHost` executes trusted local Python code. It removes runtime registrations and module lookup entries but cannot guarantee code eviction or isolate untrusted code.
- Installed inventory and Session Events are in memory only.
- Authentication, authorization policy, TLS termination, remote package download, signatures, dependency installation, and registry distribution are not part of the runtime foundation.
- The Host accepts trusted local paths and provides no authentication, authorization, TLS termination, or Internet-facing deployment policy.

Each future implementation phase must add a specification under `docs/specs/` and update this progress document in the same change.
