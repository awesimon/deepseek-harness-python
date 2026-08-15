# Plugin Authoring SDK Specification

Status: Phase 6 normative specification

## Purpose

The Plugin Authoring SDK gives backend-only, client-only, and full-stack plugin authors stable APIs over PyCordis and Cordis TS without exposing Manager or Browser Bridge bookkeeping. Plugins remain ordinary lifecycle-owned contributions, so an author can add behavior without implementing revision addressing, registration disposal, or wire frame construction.

## Scope

Phase 6 includes a Python backend author API, a TypeScript client author API, revision-bound RPC and Event helpers, immutable protocol descriptors, focused in-memory test harnesses, and executable examples of all three contribution forms.

The SDK is a thin authoring layer over the existing PyCordis `PluginSpec`, Cordis TS plugin, `PLUGIN_RUNTIME_IDENTITY`, and Browser Bridge Services. It does not create a second plugin runtime, lifecycle, registry, or identity source.

## Identity authority

The root `plugin.toml` `[plugin]` table remains the only authority for Plugin ID, semantic version, and runtime API. The Plugin Manager computes the content Revision and injects `PLUGIN_RUNTIME_IDENTITY` into the backend Fiber; browser reconciliation injects the same Plugin ID and Revision into `ClientPluginApi`.

No production SDK constructor, decorator, factory, protocol descriptor, or configuration field accepts a Plugin ID or Revision. SDK contexts expose both values as read-only observations for diagnostics and application data only. Authors cannot publish, register, call, emit, or listen under a different identity through the SDK.

The SDK may accept a diagnostic plugin name because `PluginSpec.name` identifies a Fiber in errors; that name has no package, authorization, or wire meaning and defaults to `plugin-backend`.

## Python backend author API

The `harness.sdk` module exports `define_backend_plugin`, `define_bridge_backend_plugin`, `BackendPluginContext`, `BridgeBackendPluginContext`, `BackendPluginChannel`, `RpcMethod`, `ClientEvent`, `ServerEvent`, `rpc_method`, `client_event`, and `server_event`.

```python
plugin = define_backend_plugin(setup, requires=(MY_SERVICE,))
plugin = define_bridge_backend_plugin(setup, requires=(MY_SERVICE,))
```

Both factories return `PluginSpec[None]`, add `PLUGIN_RUNTIME_IDENTITY` to the declared dependencies, reject duplicate author dependencies, and pass a read-only context to `setup`. `define_bridge_backend_plugin` additionally declares `BROWSER_BRIDGE`, `BRIDGE_RPC_REGISTRY`, and `BRIDGE_EVENT_REGISTRY` and supplies a revision-bound `BackendPluginChannel`. `define_backend_plugin` does not depend on Browser Bridge Services, so a backend-only plugin remains usable in a Manager composition without a browser transport.

`BackendPluginContext` exposes `cordis: Context`, `plugin_id`, and `revision`. Authors resolve their explicitly declared application Services and create custom Effects through `cordis`; the SDK does not add reflective service lookup or implicit dependencies.

`BridgeBackendPluginContext` extends that API with `channel`. `BackendPluginChannel.register_rpc(method, handler)` registers a method for the injected identity, `on_client_event(event, handler)` registers a client-to-backend Event handler, and `emit_client_event(event, payload, *, page_id=None)` sends a backend-to-client Event and returns the delivery count. Registration methods are asynchronous because they establish PyCordis Effects, and authors do not receive or manage the underlying registry disposers.

Backend RPC handlers receive an immutable JSON-compatible argument mapping and return a JSON-compatible value, directly or through an awaitable. Client Event handlers receive the source Page ID and a JSON-compatible payload. The SDK validates outbound values with the Agent JSON value rules before delivery and does not coerce unsupported Python objects.

`setup` may be synchronous or asynchronous and may return the cleanup forms accepted by `PluginSpec`. The returned `PluginSpec` is the backend entrypoint referenced by `plugin.toml`; plugin code does not call the Manager to install or enable itself.

## TypeScript client author API

The `@deepseek-harness/browser-bridge-client` package exports `defineClientPlugin`, `ClientPluginContext`, `RpcMethod`, `ClientEvent`, `ServerEvent`, `rpcMethod`, `clientEvent`, and `serverEvent` in addition to the lower-level Bridge client API.

```ts
export const createPlugin = defineClientPlugin(async (ctx) => {
  const value = await ctx.call(describe, { verbose: false })
  ctx.on(changed, (payload) => render(payload))
  return () => removeRenderedState(value)
})
```

`defineClientPlugin(setup)` returns the `createPlugin(api)` factory consumed by the Browser Bridge adapter. The resulting Cordis TS plugin creates a `ClientPluginContext` from the adapter-supplied `ClientPluginApi` and its active Cordis Context. It fails activation when invoked without a revision-bound API.

`ClientPluginContext` exposes the active Cordis Context plus read-only `pluginId` and `revision`. `call(method, args, signal?)` invokes the matching backend RPC, `emit(event, payload)` sends a client Event, and `on(event, handler)` registers a backend Event listener whose disposer belongs to the client Fiber. `effect(setup)` delegates custom cleanup ownership to the same Cordis Fiber. Setup and its returned cleanup follow Cordis TS plugin lifecycle semantics.

The client API never exposes raw page, operation, or call identifiers. Authors may pass an `AbortSignal` for RPC cancellation but cannot address another plugin or Revision.

## Full-stack protocol helpers

`RpcMethod[Arguments, Result]`, `ClientEvent[Payload]`, and `ServerEvent[Payload]` are immutable descriptors with a non-empty wire name and a direction. Python constructs them through `rpc_method`, `client_event`, and `server_event`; TypeScript uses `rpcMethod`, `clientEvent`, and `serverEvent`. Backend channels accept RPC methods and client-origin Events, while client contexts accept RPC methods and both Event directions according to the operation being performed. Static typing rejects a descriptor used in the wrong direction.

Descriptors carry no Plugin ID, Revision, registry, connection, or mutable handler state. Every operation combines the descriptor name with the identity already bound to its SDK context. Duplicate names in one direction fail during activation rather than replacing an existing registration.

The optional `[protocol].schema` artifact in `plugin.toml` remains the cross-runtime data specification and contributes to the Manager Revision. Phase 6 descriptors provide direction-safe names and JSON-compatible generic types; they do not generate language bindings or perform plugin-specific JSON Schema validation. Full-stack examples keep Python descriptors, TypeScript descriptors, and the Schema names aligned and exercise them through shared fixtures.

## Version and compatibility policy

`plugin.runtime_api` selects the Host authoring and loading API major. The Phase 6 SDK supports `runtime_api = "1"`; an unsupported value fails manifest validation before importing backend code or publishing a client Bundle. The Python distribution and TypeScript package use semantic package versions, and templates pin compatible SDK ranges rather than copying SDK implementation code.

Additive SDK APIs and optional protocol fields may ship under the same runtime API. Removing an API, changing lifecycle ownership, changing identity authorization, or changing existing wire meaning requires a new runtime API or Browser Bridge protocol version as applicable. Unsupported old formats fail explicitly; the SDK does not guess, downgrade, or install compatibility aliases.

A plugin's semantic version is author-controlled metadata, while its Revision is a Manager-controlled content digest. Changing backend, client, manifest, or declared protocol bytes changes the Revision regardless of package or SDK version.

## Effect and lifecycle ownership

Every SDK registration is created with the active backend or client Fiber and is disposed by that Fiber. Backend RPC and Event registrations disappear before an old backend Revision can serve after disable or update. Client Event listeners and custom Effects disappear before the imported module is released.

Setup failure disposes every Effect established by that activation attempt. Cleanup runs in the owning Cordis runtime's established reverse order, continues according to that runtime's cleanup policy, and remains visible through Fiber diagnostics. SDK helpers never retain a hidden global registry or keep an author callback alive after Fiber disposal.

Application callbacks may capture their SDK context only for the active lifecycle. Calls made after disposal fail through the underlying inactive context, stale Revision, or disposed connection behavior; the SDK does not reconnect or retarget them.

## Test support

`harness.sdk.testing` provides `BackendPluginHarness` and `FullStackPluginHarness`. The backend harness mounts one author entrypoint with a synthetic Manager-owned identity, declared test Services, and deterministic disposal. The full-stack harness adds in-memory Bridge Services, invokes registered RPC, sends client Events, captures emitted server Events, and can assert that no registration remains after disposal.

The TypeScript package exposes test-only `createClientPluginHarness`, which mounts the real Cordis plugin with a fake revision-bound `ClientPluginApi`, records RPC and Event traffic, dispatches backend Events, and disposes the Fiber. Test harnesses may accept explicit fixture identity because they stand in for Manager and reconciliation infrastructure; production author APIs do not.

Test support exercises the same public factories and lifecycle paths used by the Host. It does not import private registries, mutate a plugin's returned `PluginSpec`, or claim browser transport coverage. Real HTTP, WebSocket, and Chromium behavior remains covered by the Host full-stack scenario.

## Failure handling

- Empty descriptor names, duplicate dependencies, duplicate registrations, missing injected identity, and unavailable required Services fail activation without leaving registrations.
- Non-JSON RPC arguments, results, and Event payloads fail at the earliest SDK-controlled send or return point; values are never stringified as a fallback.
- Backend handler exceptions use the Browser Bridge structured RPC error path, and cancellation never becomes a successful result.
- A missing or stale backend Revision rejects client RPC and Events through existing Bridge authorization.
- Client setup, listener, or cleanup failures remain Cordis Fiber diagnostics and do not cause the SDK to mount an untracked replacement.
- Test harness teardown reports cleanup failures and still attempts to dispose every owned runtime object.

## Acceptance criteria

- A backend-only example uses `define_backend_plugin`, resolves one declared PyCordis Service, and removes its Effects on disable without depending on Browser Bridge Services.
- A client-only example uses `defineClientPlugin`, mounts in a real Cordis TS Context, and removes Event listeners and custom Effects on unload.
- A full-stack example uses descriptors and both SDK contexts for RPC and bidirectional Events without passing Plugin ID or Revision in plugin code.
- Tests prove Manager-injected identity is used for every backend registration and browser reconciliation identity is used for every client operation.
- Tests reject identity or Revision parameters on production factories and prove stale operations cannot be retargeted through a descriptor.
- Python and TypeScript test harnesses exercise setup success, setup rollback, cancellation, handler failure, JSON rejection, and idempotent disposal.
- Type checking proves RPC argument/result and Event direction types across representative examples.
- Existing raw `PluginSpec` and `createPlugin(api)` entrypoints continue to work because the SDK compiles to those established runtime forms.
- README and progress documentation identify the SDK as the supported author path and retain the trusted local-code limitation.

## Exclusions

Project templates and a scaffolding command are specified separately. Protocol code generation, plugin-specific runtime Schema validation, UI components, dependency installation, remote distribution, signatures, process isolation, permissions, persistent state, state migration between Revisions, and multi-page activation aggregation are outside Phase 6.
