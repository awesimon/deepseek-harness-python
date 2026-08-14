# Browser Bridge Specification

Status: Phase 4 normative specification

## Purpose

The Browser Bridge delivers Plugin Manager client revisions to browser pages, reconciles each page's Cordis TS Fibers with Host intent, and carries explicitly registered JSON RPC and Events between a plugin's backend and client contributions.

## Scope

Phase 4 includes a transport-independent Host state machine, versioned JSON frames, exact bundle retrieval, page-local reconciliation, stale-result rejection, request cancellation, package-private RPC, a TypeScript client runtime adapter, and in-memory conformance tests shared by Python and TypeScript fixtures.

Authentication, Internet-facing deployment, binary streaming, offline cache persistence, browser extension packaging, and multi-page quorum remain outside this phase. A WebSocket/HTTP adapter may expose the core protocol, but transport code does not own plugin state.

## Authority and identities

The Plugin Manager remains the process-wide authority for installed plugins and published client revisions. The Bridge projects that state to pages; it cannot enable, disable, or update a plugin by itself.

`PageId`, `OperationId`, `RpcCallId`, Plugin ID, and Revision are opaque fields. Every load, unload, result, RPC, Event, and cancellation frame carries enough identity to reject a message from an old page connection, operation, or revision.

## Protocol version

Every frame is a JSON object containing `protocol: "1"` and a discriminant `type`. Unknown protocol versions close the logical connection with an explicit incompatibility error. Unknown frame types under a supported protocol fail that frame and do not mutate page state.

The normative JSON Schemas live under `protocol/`. Python and TypeScript values are generated or mechanically checked against those schemas; handwritten copies cannot drift independently.

## Connection and reconciliation

A page opens a logical connection with `hello`, carrying its Page ID and currently loaded `pluginId -> revision` map. The Host responds with one `reconcile` command containing the complete desired client graph and a new Operation ID.

Each desired entry contains Plugin ID, Revision, bundle URL, SHA-256 digest, optional protocol schema URL, and activation policy. Entries absent from the desired graph must unload. Entries with a different Revision unload before the target loads. Matching entries remain mounted.

The page applies one reconciliation operation serially and reports one result per changed plugin plus a final operation result. A newer reconcile supersedes an older operation. Results for a superseded Operation ID are retained only as diagnostics and never change current page state.

## Bundle retrieval and evaluation

The Host serves bundle bytes only for an exact currently published Plugin ID and Revision. Responses include immutable cache headers and the declared SHA-256 digest. Missing or unpublished revisions return not found rather than redirecting to current.

The TypeScript adapter imports one content-addressed module, resolves its client plugin export, and mounts it under a Cordis TS Context. The resulting Fiber owns listeners, Slots, styles, timers, RPC handlers, and other Effects. Unload disposes the Fiber before removing the module revision from the adapter's active table.

Browser code execution is trusted in this phase. User approval and generated-code guards are separate product policies layered before publication or reconciliation.

## Page-local state

For each page and plugin, the Host records `absent`, `loading`, `active`, `waiting`, `failed`, or `unloading`, plus the exact Revision and latest diagnostic. `active` means that page reported an established Cordis TS Fiber. It does not infer another page's state.

Disconnect removes ephemeral page state and cancels its outstanding RPC calls. Reconnect starts from a new `hello` inventory and full reconciliation; the Host does not assume the old connection's Fibers survived.

## Package-private RPC

Backend plugins register methods in `BridgeRpcRegistry` under their own Plugin ID and active Revision. Browser code calls only its same Plugin ID and Revision. Calls contain JSON-compatible arguments, an opaque Call ID, and an optional cancellation token.

The Host rejects missing, stopped, or stale revisions before invoking a handler. Success and structured failure responses carry the same Call ID. Cancellation is best effort: it marks the call cancelled, cancels an active async task, and suppresses a later success response.

RPC handlers are Effect-owned. Disabling or updating a backend removes handlers before the old client revision is told to unload, so stale pages cannot reach new or unrelated backend code.

## Events

`BridgeEventRegistry` exposes explicitly named JSON Events. Backend emission targets all active pages for the same Plugin ID and Revision, or one Page ID. Client emission targets the same active backend revision. Event forwarding never reflects arbitrary PyCordis or Cordis TS Event names.

Events are ordered per logical connection. They are not durable; model-visible consequences must be written to the Session Log by the owning backend plugin before reaching a later model request.

## Failure handling

- Invalid frames, schema failures, unknown versions, and identity mismatches fail without partial state mutation.
- Bundle hash mismatch fails client activation and reports a diagnostic.
- Client import or Cordis activation failure disposes attempt-owned Effects and reports `failed`.
- RPC handler exceptions become structured errors; cancellation and disconnect do not become success.
- Host disable or update supersedes outstanding load operations and rejects subsequent stale results.
- Required client failure affects aggregate Plugin Manager status; optional client failure leaves the plugin degraded.

## Acceptance criteria

- Schema tests validate every frame and reject unknown fields, versions, and discriminants.
- Host tests prove initial reconciliation, no-op matching revisions, unload-before-load updates, disconnect cleanup, and stale Operation rejection.
- Bundle tests prove exact revision retrieval, immutable digest headers, and not-found behavior after disable.
- RPC tests prove same-plugin/revision authorization, structured errors, cancellation, handler disposal, and stale-call rejection.
- TypeScript tests mount and unload a real Cordis TS test plugin and prove Effect cleanup.
- A full-stack keyless scenario enables one plugin, reconciles a simulated page, calls its backend method, updates both revisions, rejects the old call, and removes both Fibers on disable.
- `docs/progress.md` distinguishes transport-independent completion from any production WebSocket/HTTP adapter still pending.
