# Multi-Page Client Activation Specification

Status: Phase 8 normative specification

## Purpose

Multi-page client activation aggregation derives one revision-qualified client readiness result from Browser Bridge state across connected pages and reports it through the Plugin Manager. It makes browser activation observable without transferring plugin lifecycle authority away from the Manager or page-local Fiber authority away from the Bridge.

## Scope

Phase 8 includes an official PyCordis aggregation provider, validated Host quorum configuration, deterministic page membership, revision-qualified aggregate snapshots, required and optional readiness rules, structured diagnostics, update and disable handling, and keyless multi-page browser evidence.

The phase aggregates the page-local states defined by the [Browser Bridge specification](browser-bridge.md). It does not change Bundle evaluation, Cordis TS Fiber ownership, package-private RPC, Event routing, or the root plugin manifest.

## Authority

The Plugin Manager remains authoritative for installation, desired enablement, current Revision, contribution policy, publication, and the plugin record. The Browser Bridge remains authoritative for live page connections, accepted connection generations, reconciliation operations, and each page's reported Plugin ID and Revision state.

The aggregation provider owns only the derived `ClientActivationSnapshot`. It cannot enable, disable, update, publish, reconcile, or dispose a plugin. It submits a snapshot to a Manager-owned reporting API; the Manager accepts it only when Plugin ID, desired enablement, and Revision still match the current record.

Backend-only plugins have client state `not_applicable`. Publication success and browser activation remain separate facts: a published client contribution may be `unobserved`, `reconciling`, `active`, `degraded`, or `failed`.

## Configuration

`HarnessHostConfig` contains a validated client activation aggregation configuration with a default quorum and optional Plugin ID overrides. A quorum is either `all_connected` or `any_connected`; omitted configuration defaults to `all_connected`. Overrides must name an installed plugin with a client contribution; duplicate, unknown, backend-only, or unsupported entries fail Host startup.

`all_connected` requires every eligible page to report the current Revision active. `any_connected` requires at least one eligible page to report the current Revision active. An empty eligible set never satisfies either quorum.

Quorum is a deployment choice rather than plugin package identity, so it does not add fields to `plugin.toml`. The root manifest's `activation.client` value retains a different purpose: `required` makes quorum satisfaction part of plugin readiness, while `optional` allows the other contribution to remain ready when browser activation fails.

## Page membership

A page becomes eligible after the Bridge accepts its `hello` for the current logical connection. Every eligible page participates in every currently published client plugin because Phase 8 has no route, tenant, capability, or plugin-interest selector.

Membership is keyed by opaque Page ID and connection generation. Replacing a connection with the same Page ID removes the old generation and admits the accepted replacement as one serialized membership change; the two generations are never counted together. Frames from the replaced generation cannot change membership or aggregate state.

The accepted `hello` inventory initializes page state only for exact currently published Revisions. A matching loaded Revision counts as active after Bridge validation. Missing, unpublished, or stale inventory entries do not count as success and enter normal reconciliation.

Disconnect removes the page from the eligible set immediately and cancels its outstanding calls as defined by the Bridge. Reconnect creates a new generation and is evaluated from its new inventory. The aggregator does not retain a disconnected page as a quorum member or assume its Cordis TS Fibers survived.

## Aggregate model

Each immutable `ClientActivationSnapshot` contains Plugin ID, current Revision when published, client activation policy, quorum, state, eligible-page count, active-page count, pending-page count, failed-page count, and current page diagnostics. Counts and diagnostics are derived from one serialized Bridge snapshot so callers never observe totals assembled from different connection generations.

For a published Revision, `loading`, `waiting`, and `unloading` toward that Revision are pending. Exact-Revision `active` is successful. A terminal `failed`, an operation result that leaves the desired Revision absent, or an operation-level failure for an unresolved desired entry is failed. State for another Revision is pending until the current operation settles and never counts as active or failed for the target solely because it is stale.

The aggregate state is derived in this order:

1. With no eligible pages, state is `unobserved`.
2. When quorum is satisfied and no eligible page failed, state is `active`.
3. When quorum is satisfied and at least one eligible page failed, state is `degraded`.
4. When quorum is not satisfied and at least one eligible page is pending, state is `reconciling`.
5. When a non-empty eligible set has settled without satisfying quorum, state is `failed`.

An installed client contribution without a current publication is `not_published`. After withdrawal, it is `draining` while any eligible page still reports the withdrawn Revision active or unloading, then becomes `not_published`. A plugin without a client contribution is always `not_applicable`.

## Plugin Manager status

Client aggregation is one input to the Manager's aggregate plugin state; desired enablement and backend activation remain independent inputs. Host startup and `enable()` complete after process-local contributions start and publication succeeds. They do not wait for a browser connection, because the HTTP listener must become available before a page can reconcile.

For a required client contribution, `unobserved` or `reconciling` makes the enabled plugin `WAITING`, `active` makes it `ACTIVE`, `degraded` makes it `DEGRADED`, and `failed` makes it `FAILED`. A browser activation failure does not automatically withdraw publication or stop a successful backend contribution; membership changes or a later successful reconciliation can recover the aggregate state for the same Revision.

For an optional client contribution, `unobserved`, `reconciling`, or `active` does not prevent the plugin from becoming `ACTIVE` when every process-local required contribution is ready; this is vacuously true for a client-only plugin. `degraded` or `failed` makes the plugin `DEGRADED`; it never rolls back or fails a successful required backend contribution.

Process-local required activation failure retains the Phase 3 rollback behavior and takes precedence over client aggregation. A post-publication page failure is readiness state, not a new activation attempt, so its recoverable `FAILED` state does not re-run Phase 3 contribution rollback. While a required client is waiting, an optional backend failure remains visible in contribution diagnostics but the aggregate state stays `WAITING`; after client quorum settles, the optional failure produces `DEGRADED`. Desired disablement takes precedence over every readiness result and produces `DISABLING` or `DISABLED`.

`/health` remains a process-liveness endpoint and does not block Host startup on browser readiness. Manager snapshots are the authoritative readiness and diagnostic API for callers that need to wait for a plugin to become usable in browsers.

## Status and diagnostics

Every failed page entry retains a structured diagnostic containing a stable error code, Plugin ID, target Revision, Page ID, connection generation, Operation ID when available, page state, and a concise message. Pending entries expose their target Revision and operation identity without fabricating an error.

The plugin record may retain only the most recent stale, replaced, disconnected, withdrawn, or previous-Revision client diagnostic as non-current context. It does not contribute to current counts or readiness. A healthy exact-Revision report clears that page's current failure. Revision replacement clears current aggregate diagnostics before evaluating the new target.

Snapshot ordering is deterministic by Page ID. Browser exception text is diagnostic data, not a protocol decision key; aggregation branches only on validated states and identities.

## Update and disable

Update starts a new aggregation generation after the Manager accepts and publishes the candidate Revision. No page state or result from the previous Revision can satisfy the new generation. A page replacing the old Revision is pending until it reports the candidate active or failed, and the aggregate may move through `unobserved`, `reconciling`, `active`, `degraded`, or `failed` as membership changes.

Browser failure of a published candidate follows the required or optional status rules above and does not silently reactivate the previous Revision. An explicit later update or retry owns recovery to another Revision.

Disable marks the plugin non-serving and withdraws publication before client unload completes. The Manager state proceeds to `DISABLED` without waiting indefinitely for browser acknowledgements; exact-revision Bundle, RPC, and Event authorization is already unavailable. The client snapshot reports `draining` until every still-connected page reports the withdrawn contribution absent or disconnects, then reports `not_published`.

Host shutdown may close WebSocket traffic before disabling plugins as defined by the [Host Assembly specification](host-assembly.md). Connection removal therefore settles membership without requiring unload acknowledgements from pages that the Host has disconnected.

## Failure handling

- Invalid or stale frames remain Bridge failures and cannot mutate an aggregate snapshot.
- A per-plugin activation failure affects only that Plugin ID, Revision, and page generation; another page or plugin continues reconciling.
- Operation-level failure marks every unresolved desired entry in that operation failed with the same operation identity, while already validated results remain unchanged.
- Duplicate Page ID replacement is atomic for aggregation and rejects late results from the replaced generation.
- Disconnect removes quorum membership but may preserve the most recent client diagnostic as non-current context.
- Snapshot delivery to the Manager is revision-checked; a racing update or disable rejects the stale report and triggers recomputation from current authorities.
- An internal aggregation failure must produce an explicit provider or Host diagnostic and must not report a fabricated `active` state.

## Acceptance criteria

- Pure aggregation tests cover empty membership, `all_connected`, `any_connected`, pending work, partial failure, total failure, recovery, and deterministic diagnostics.
- Membership tests prove accepted `hello`, exact-Revision inventory, disconnect, reconnect, duplicate Page ID replacement, and rejection of results from an old connection generation.
- Manager tests prove backend-only `not_applicable`, required `WAITING`/`ACTIVE`/`DEGRADED`/`FAILED` transitions, optional failure degradation, process-local failure precedence, and recovery without republishing the same Revision.
- Update tests prove that no old Revision or Operation result contributes to the candidate aggregate and that a candidate browser failure never silently restores the previous Revision.
- Disable tests prove immediate serving revocation, observable `draining`, eventual `not_published`, and completion when remaining pages disconnect.
- Host configuration tests reject invalid quorum names and invalid Plugin ID overrides before serving traffic.
- A keyless Chromium scenario connects at least two pages, demonstrates divergent page outcomes under both quorum modes, recovers after membership or reconciliation changes, updates to a new Revision, and disables with no current page or backend registration left.
- Ruff, strict Pyright, Python tests, TypeScript type checking/tests/build, and documentation checks pass; `docs/progress.md` records the implementation evidence when Phase 8 code lands.

## Exclusions

Page selection by route, tenant, user, browser capability, or plugin-declared interest is outside Phase 8. Cross-process or cross-Host quorum, durable page history, metrics retention, background-tab leases, offline activation, authentication, authorization, and automatic rollback to an old Revision remain separate capabilities.

Plugin SDKs and backend-only, client-only, or full-stack authoring templates are owned by their own specification. Phase 8 consumes the same root manifest and client protocol without coupling aggregation to template generation.
