# Harness Foundation Completion Specification

This reference defines the final foundation milestone for the Python Harness. It closes the Browser Bridge implementation and establishes the repository layout used by subsequent plugin development.

## Scope

The milestone includes the direct Python package layout, normative Browser Bridge JSON Schema, Python frame validation, backend/client Event forwarding, an HTTP/WebSocket adapter, a Cordis TS client adapter, and a keyless full-stack lifecycle scenario.

The existing [Cordis Core](cordis-core.md), [Agent Spine](agent-spine.md), [Plugin Manager](plugin-manager.md), and [Browser Bridge](browser-bridge.md) specifications remain authoritative for their subsystems. This document defines the integration required to consider the runtime foundation complete.

## Repository layout

The import package lives at `harness/` in the repository root. Setuptools discovers only `harness` and its subpackages from that root. Tests, Pyright, Ruff, bytecode compilation, editable installs, source distributions, and wheels use the same location without `PYTHONPATH` overrides.

The distribution remains `deepseek-harness-python`; the supported import root remains `harness`. No `src/` package tree or `deepseek_harness` compatibility package exists.

The browser runtime adapter lives under `frontend/`. It is harness infrastructure, not a second plugin identity. Individual logical plugins may independently contain an optional `frontend/` build directory while their root `plugin.toml` remains the identity authority.

## Protocol authority

Version 1 Browser Bridge frames are defined by a bundled JSON Schema under `harness/protocol/`. Every frame contains `protocol` and `type`, rejects unknown fields, and uses JSON-compatible values only. Python decoding validates before constructing immutable protocol values; Python encoding validates the emitted object. TypeScript protocol types and tests are mechanically checked against the same field definitions and shared fixtures.

Supported frames cover hello, full-graph reconciliation, per-plugin results, operation completion, RPC calls/results/cancellation, and explicitly named Events. Unsupported versions terminate the logical connection. Invalid frames fail without changing page or plugin state.

## Client publication and reconciliation

Client publication retains exact bundle bytes, SHA-256 digest, optional plugin protocol Schema bytes, and activation policy for one Plugin ID and Revision. Publishing or removing a client revision notifies connected transports, which issue a new full-graph reconciliation operation.

The HTTP adapter serves only the current exact Revision with immutable cache headers and its digest. It serves an optional plugin protocol Schema under the same Revision. Missing, stopped, or stale revisions return not found.

The WebSocket adapter accepts one hello frame before other traffic, owns connection replacement for duplicate Page IDs, and serializes outbound frames per connection. It routes reconciliation results, RPC, cancellation, and Events through the transport-independent `BrowserBridge`. Disconnect removes page state and cancels owned calls.

## Event forwarding

Backend plugins register inbound Event handlers by Plugin ID, Revision, and Event name. Registrations return idempotent disposers and are intended to be owned by PyCordis Effects. Client Events reach only a matching active page Revision.

The Plugin Manager supplies each backend activation with an isolated `PLUGIN_RUNTIME_IDENTITY` Service. Bridge registrations use that Manager-owned Plugin ID and Revision; identity is not a plugin configuration field.

Backend emission targets all matching active pages or one Page ID. Page sinks are connection-owned and removed on disconnect. Event forwarding does not reflect arbitrary PyCordis or Cordis TS event names and is not durable.

## Cordis TS client adapter

The TypeScript adapter owns one Cordis TS child Fiber for each active Plugin ID and Revision. Reconciliation preserves matching revisions, unloads removed or changed revisions before loading replacements, verifies bundle SHA-256 before import, and reports every changed plugin plus operation completion.

Client modules export a Cordis plugin or `createPlugin(api)`. The factory receives a `ClientPluginApi` bound to the reconciliation entry's Plugin ID and Revision; its RPC and Event methods always carry that identity. A failed hash, import, export, or Fiber activation reports `failed` and removes attempt-owned resources. Unload disposes the Fiber before deleting the active revision.

## Full-stack lifecycle

The keyless scenario installs and enables one full-stack plugin, establishes a page connection, activates its client Fiber, invokes an Effect-owned backend RPC method, forwards an Event, updates both contributions, rejects the stale Revision, and disables the plugin. Completion requires both PyCordis and Cordis TS contributions to disappear after disable.

## Failure handling

- Schema errors, unsupported versions, unknown frame types, and identity mismatches fail before state mutation.
- Bundle or plugin protocol Schema requests never redirect from a stale Revision to the current Revision.
- Replaced WebSocket connections cannot remove or mutate the replacement connection.
- RPC cancellation and disconnect suppress successful completion and return or emit a structured cancellation result.
- Client activation failure is page-local and preserves the diagnostic associated with the exact operation and Revision.

## Acceptance criteria

- The repository imports, type-checks, tests, builds, and installs `harness/` directly from the root.
- Python Schema tests accept every supported frame and reject unknown fields, versions, discriminants, and invalid success/error combinations.
- Event tests prove same-Plugin/Revision authorization, targeted and broadcast delivery, ordering, disposal, and disconnect cleanup.
- HTTP/WebSocket tests prove exact artifact delivery, automatic reconciliation, frame routing, connection replacement, and cancellation.
- TypeScript tests mount, preserve, replace, and unload a real Cordis plugin while proving Effect cleanup and hash rejection.
- The keyless full-stack scenario proves enable, reconciliation, RPC, Event, update, stale rejection, and disable across both runtimes.
- `docs/progress.md` contains no unfinished Browser Bridge implementation item.

## Exclusions

Internet-facing authentication, authorization policy, TLS termination, package download, signatures, dependency installation, persistent inventory, durable Session storage, and process isolation for untrusted backend plugins remain deployment or product capabilities. The included HTTP/WebSocket adapter is an application building block and must be placed behind the host application's security policy before remote exposure.
