# Plugin Control Plane and Local Distribution Specification

English | [中文](plugin-control-plane.zh.md)

Status: Phase 10 normative specification

## Purpose

The Plugin Control Plane turns the Dynamic Plugin Manager into an operable local-development service. It exposes inventory and lifecycle commands through a loopback HTTP API, supplies a CLI over that API, watches trusted catalogs for hot updates, and makes the TypeScript browser SDK resolvable outside the Harness workspace.

The Manager remains the authority for plugin identity, revision construction, contribution activation, rollback, and aggregate state. The control plane validates operator intent, serializes it with filesystem intent, and returns Manager-derived snapshots; it does not implement a second lifecycle.

## Scope

Phase 10 includes an opt-in versioned control API, inventory and readiness snapshots, install, enable, disable, update, rollback, uninstall, optimistic mutation preconditions, operation diagnostics, a non-retrying CLI client, configurable catalog watching with debounce, and a locally distributable browser SDK tarball used by generated client projects.

The phase operates on trusted local plugin roots already accepted by the [Dynamic Plugin Manager](plugin-manager.md). It does not download plugin code or dependencies. Backend code remains trusted and in-process under the existing limitation.

## Trust and exposure

The control API has no authentication and is therefore available only when the effective Host listener is loopback-only. Enabling it on a wildcard, public, or mixed loopback/non-loopback address fails Host configuration before binding. The adapter does not trust forwarded-address headers, enable CORS, or derive authorization from `Host` or proxy headers.

Requests that carry a browser `Origin` header must use the control listener's exact origin. Requests without `Origin` are admitted for CLI and local automation. Mutation requests require `application/json`; form and text bodies are rejected so an unrelated web page cannot submit a simple cross-origin form mutation.

Plugin roots supplied by HTTP or filesystem discovery must resolve to immediate children of configured trusted catalog directories. Symlinks and normalized paths may not escape those catalogs. Loopback placement limits exposure but is not a security boundary against another process running as the same user.

## Control resources and snapshots

`GET` responses are immutable JSON snapshots captured from one serialized control-plane observation. Collection results are ordered by Plugin ID. Each plugin entry includes Plugin ID, semantic version, current Revision, retained previous Revision, source root, desired enablement, aggregate state, process-local contribution state, published client Revision, complete client activation readiness, current diagnostic, and an opaque monotonic `mutationVersion`.

Client readiness preserves the fields defined by the [Multi-Page Client Activation Aggregation specification](multi-page-activation.md), including quorum, aggregate state, page counts, current page diagnostics, target Revision, and operation identities. A control snapshot never collapses `unobserved`, `reconciling`, `degraded`, or `failed` into a generic enabled flag.

The collection also reports an opaque `inventoryVersion` and watcher status. Watcher status includes whether watching is enabled, the configured catalogs and policies, the number of pending roots, the currently dispatched root when present, and the most recent structured watcher diagnostic. Inventory and mutation versions are process-local concurrency tokens, not durable identifiers or plugin Revisions.

Every diagnostic contains a stable code, concise message, affected Plugin ID or path when known, operation identity when available, and current or candidate Revision when available. Python exception classes and tracebacks are logs, not protocol fields.

## HTTP API

The API prefix is `/api/control/v1`. Unknown fields and unsupported media types fail before any Manager operation.

| Method | Path | Operation |
|---|---|---|
| `GET` | `/plugins` | Return ordered inventory and watcher status. |
| `GET` | `/plugins/{pluginId}` | Return one current plugin snapshot. |
| `POST` | `/plugins/install` | Install one validated catalog root as disabled. |
| `POST` | `/plugins/{pluginId}/enable` | Enable the current Revision. |
| `POST` | `/plugins/{pluginId}/disable` | Disable all current contributions. |
| `POST` | `/plugins/{pluginId}/update` | Build and apply a candidate from the installed root. |
| `POST` | `/plugins/{pluginId}/rollback` | Activate the retained previous Revision. |
| `POST` | `/plugins/{pluginId}/uninstall` | Remove one disabled inventory record. |

Install accepts a catalog-contained `pluginRoot` and requires `expectedAbsent: true`. Update reads only the already installed root; a request cannot redirect an installed Plugin ID to another directory. Rollback requires a `targetRevision` equal to the snapshot's retained previous Revision. Uninstall retains the Manager rule that the plugin must already be disabled.

Each accepted mutation returns an Operation ID, an outcome of `succeeded` or `failed`, and the post-operation snapshot captured before the serialized slot is released. Uninstall returns a tombstone containing the removed Plugin ID, Revision, and final mutation version. A contribution activation or cleanup failure is an accepted operation with `failed` outcome and Manager diagnostics, while malformed, stale, or unauthorized intent is a rejected request.

HTTP status distinguishes transport and command admission: successful observations and accepted operations use `2xx`; invalid input uses `400`; unknown inventory uses `404`; stale or state-conflicting intent uses `409`; unsupported content uses `415`; closing service uses `503`; and an unexpected adapter failure uses `500`. Error responses use one stable JSON envelope and never include a traceback.

## Mutation preconditions

Every mutation except install requires both `expectedRevision` and `expectedMutationVersion` from a prior snapshot. The control plane compares both values inside the serialized operation immediately before invoking the Manager. A mismatch returns `409` with the current snapshot and does not import code, publish a bundle, disable a contribution, or increment a version.

The mutation version changes after every accepted operation that changes inventory, desired enablement, current or previous Revision, or a serving contribution. Browser membership and readiness reports do not change it. An exact idempotent enable or disable that produces no Manager change succeeds without incrementing it.

Revision comparison prevents a stale client from targeting different code; the monotonic mutation version also detects disable/enable and rollback/update cycles that return to the same content digest. The API never guesses a new precondition and never retries a conflict automatically.

## Serialized operations

One Host owns one FIFO mutation coordinator shared by HTTP commands, startup catalog actions, and filesystem watcher actions. It submits at most one mutation to the Manager at a time, including operations for different Plugin IDs, matching the Manager's process-wide serialization. Read responses observe either the state before or after a mutation and never a partially updated record.

Each queued item carries its source, Operation ID, target root or Plugin ID, and captured preconditions. Preconditions are rechecked at dispatch, not only when queued. A watcher item that becomes stale is discarded and rescheduled from a fresh filesystem and Manager snapshot; an HTTP item returns a conflict and is never retargeted.

Disconnecting an HTTP client does not cancel a mutation after Manager dispatch because contribution teardown or activation cannot be abandoned safely. The operation completes, diagnostics remain observable through inventory, and a retry still requires a fresh snapshot. A request cancelled before dispatch is removed without invoking the Manager.

Plugin callbacks cannot call the coordinator recursively for their own record. Such an attempt fails explicitly instead of waiting on the active operation.

## Filesystem watcher

Watching is opt-in Host configuration. Each enabled watcher declares catalog roots, a positive debounce duration, a create policy of `ignore`, `install_disabled`, or `install_enabled`, and a delete policy of `ignore`, `disable`, or `uninstall`. The Host rejects watched catalogs outside its trusted catalog set and timing or policy values it does not support.

The watcher observes root `plugin.toml` files and every manifest-declared backend, client, and protocol artifact. Events are coalesced per plugin root. After the debounce interval, one rescan builds one immutable candidate and submits intent through the mutation coordinator. A change that arrives during build or activation schedules exactly one later rescan of the latest filesystem state.

For an installed root, a distinct valid candidate invokes update and preserves the record's desired enablement. The same Revision is a no-op. An invalid or incomplete candidate leaves the current Revision serving, records a watcher diagnostic, and waits for a later filesystem event or explicit API command rather than polling in a failure loop.

Create policy controls whether a newly valid immediate child is ignored, installed disabled, or installed and enabled. Delete policy controls whether a root that remains absent for the full debounce interval is ignored, disabled, or disabled and uninstalled. Automatic uninstall proceeds only when disable cleanup has no diagnostic; otherwise the disabled record remains observable. A delete followed by recreation is evaluated from fresh state and never reuses a stale candidate.

Watcher and HTTP operations use identical validation, preconditions, rollback, publication, Browser Bridge reconciliation, and snapshot paths. The watcher does not invoke a package manager, rebuild frontend source, infer undeclared artifacts, or edit plugin files.

## CLI

`deepseek-harness-python plugin` is an HTTP client for the control API and provides `list`, `show`, `install`, `enable`, `disable`, `update`, `rollback`, and `uninstall`. It accepts an explicit control URL, defaults only to a documented loopback URL, and never imports or constructs a Plugin Manager.

Before a mutation, the CLI reads the relevant snapshot and sends its exact preconditions unless the caller supplies an explicit Revision and mutation version. It does not retry `409`; it prints the current conflict snapshot so a person or script can decide again. Rollback additionally sends the selected retained Revision.

Successful commands write one stable JSON document to standard output. Rejected requests, transport failures, and accepted operations with `failed` outcome return nonzero and write a concise diagnostic to standard error; when the server supplied a snapshot, the JSON snapshot remains available on standard output. Human-oriented formatting may be added later without changing the JSON mode.

## Local TypeScript SDK distribution

The browser authoring package remains a TypeScript Cordis runtime dependency and is distributed for local development as a versioned npm-compatible tarball. The frontend library build emits its public runtime and type exports, then a packaging step creates the tarball and records its package version and SHA-256 digest. Python wheel and source distributions include that exact artifact as package data; a stale or missing artifact fails the distribution build.

`deepseek-harness-plugin sdk export` copies the bundled tarball to an explicit destination without downloading or installing anything. Export is idempotent when an existing file has the same digest and rejects a different existing file unless the caller selects another destination.

Client-only and full-stack scaffolds vendor the tarball under `frontend/vendor/` and declare the browser SDK with a relative `file:` dependency. Their lockfile records the local tarball and its dependency graph, so `pnpm install --frozen-lockfile` no longer depends on a workspace symlink or a nonexistent public browser SDK version. Backend-only scaffolds contain no browser artifact.

The bundled package version must match the SDK compatibility constant used by the scaffolder. Generated projects remain deterministic for one Harness distribution. Public npm dependencies such as Cordis, TypeScript, the bundler, and the test runner still require a configured registry or populated package-manager store; Phase 10 does not claim offline dependency installation or publish the browser SDK to a remote registry.

## Startup and shutdown

Host startup validates control and watcher configuration, activates initial catalog plugins, binds the loopback application, and then starts filesystem observation. A startup failure closes the adapter and watcher through the existing Host rollback path. Initial catalog activation and later control operations use the same mutation coordinator.

Shutdown first marks the control plane closing and rejects new mutations, then stops accepting watcher events and cancels queued operations that have not reached Manager dispatch. It waits for the one dispatched mutation to settle before the Host closes browser connections, disables plugins, and closes PyCordis according to the [Host Assembly specification](host-assembly.md). Concurrent close calls join the same shutdown.

Queued cancellation and watcher shutdown have bounded, configured timing and structured diagnostics. The control plane never reports shutdown complete while a Manager mutation it dispatched is still running, and it never starts a deferred hot update after plugin teardown begins.

## Failure handling

- Invalid JSON, unknown fields, unsafe roots, unsupported actions, and invalid preconditions fail without Manager mutation.
- Candidate validation failure leaves the current installed or active Revision unchanged and reports the candidate path and stable error code.
- Activation and cleanup failures retain Manager state and diagnostics; the control adapter does not fabricate success or reactivate an old Revision.
- Stale HTTP intent returns the current snapshot, while stale watcher intent triggers one fresh rescan and cannot overwrite a newer API decision.
- Filesystem overflow or watcher backend failure marks watching failed and leaves HTTP control available; recovery requires explicit Host restart in this phase.
- SDK asset digest or version mismatch fails export, scaffold generation, or distribution build before writing a partial project or package.
- Unexpected response serialization failure is logged with its Operation ID and returns a generic error without leaking local paths beyond fields already authorized for this local API.
- Shutdown continues after individual queue, watcher, adapter, or plugin cleanup failures and reports them through the Host's aggregate cleanup error.

## Acceptance criteria

- API tests prove loopback-only startup, exact-origin browser requests, content-type enforcement, stable JSON errors, and rejection of non-loopback control exposure.
- Inventory tests prove deterministic ordering, complete Manager and browser readiness fields, watcher status, immutable observations, and stable diagnostic codes.
- Lifecycle tests exercise install, enable, disable, update, rollback, and uninstall through the real Host API for backend-only, client-only, and full-stack plugins.
- Concurrency tests prove FIFO Manager dispatch, atomic response snapshots, stale Revision and mutation-version conflicts, no automatic HTTP retry, and stale watcher rescan.
- Cancellation and shutdown tests prove pre-dispatch cancellation, post-dispatch completion after client disconnect, closing-service rejection, queue draining, and no mutation after teardown starts.
- Watcher tests prove debounce coalescing, in-flight rescan, same-Revision no-op, valid hot update, invalid-candidate preservation, create policies, delete policies, and cleanup-failure retention.
- A keyless Chromium scenario changes a full-stack plugin's backend and built client artifacts, observes one new Revision through the API, reconciles connected pages, and proves old backend and Cordis TS Effects are absent.
- CLI tests run against a listening Host, cover every command, preserve structured output, return nonzero for failed outcomes, and surface conflicts without retry.
- An isolated wheel installation exports the browser SDK, generates client-only and full-stack projects, installs their locked frontend dependencies without workspace links, and passes type checking, tests, builds, validation, and Host activation.
- Ruff, strict Pyright, Python tests, TypeScript checks/tests/build, Python distribution builds, wheel smoke tests, and documentation checks pass; `docs/progress.md` records the evidence when Phase 10 implementation lands.

## Exclusions

Remote plugin registries, plugin search or download, remote browser SDK publication, dependency installation, lockfile updates, signatures, provenance, trust policy, untrusted-code sandboxing, process isolation, production authentication, authorization, TLS termination, proxy deployment, and multi-user control are outside Phase 10.

The watcher does not compile source, migrate plugin state, preserve durable operation history, synchronize multiple Hosts, or guarantee recovery after process restart. Persistent inventory, durable audit logs, background job APIs, dependency graphs between logical plugins, and automatic rollback based on browser readiness require separate specifications.
