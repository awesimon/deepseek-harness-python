# DeepSeek Harness TypeScript Source Notes

English | [中文](deepseek-harness-ts.zh.md)

Reference commit: `47f943859bef60e4160492346772ded9b24f765a`

Purpose: record the TypeScript mechanisms already inspected for the Python rewrite. Consult this file before reopening the original source.

## Cordis context and services

- `vendor/cordis/src/context.ts`: a context is a Proxy-backed service repository. Child contexts inherit metadata. `isolate(name, label?)` replaces the service-name-to-realm label for descendants. `intercept()` layers service-specific plugin configuration.
- `vendor/cordis/src/reflect.ts`: service implementations are stored by isolation label. A plugin property read is constrained by its declared injection chain. `provide()` is an effect, rejects duplicate providers in one realm, and notifies dependent fibers when availability changes.
- Python decision: use explicit `ServiceKey`, `require`, and `lookup` APIs instead of Proxy behavior. Preserve realm-based addressing and duplicate rejection.

## Fiber lifecycle

- `vendor/cordis/src/fiber.ts`: every `ctx.plugin()` creates a fiber with `PENDING`, `LOADING`, `ACTIVE`, `FAILED`, `UNLOADING`, and `DISPOSED` states.
- A fiber builds an activation epoch from the UIDs of the service providers satisfying `inject`. Missing dependencies produce the inactive epoch. Provider identity changes unload and then reload the dependent fiber.
- A service provided during plugin construction is not generally available to dependents until the provider fiber becomes active. The provider retains self-access during teardown until dependency cleanup completes.
- `effect()` makes setup reentrancy-safe, collects one or several disposers, and makes disposal single-shot. Cleanups inside one effect reverse registration order; a fiber unload starts top-level effect disposal concurrently and awaits settlement.
- Python decision: preserve dependency epochs, activation failure rollback, grouped reverse cleanup, and concurrent top-level disposal. Initial implementation targets one asyncio event loop.

## Events

- `vendor/cordis/src/events.ts`: listeners belong to the registering fiber. Dispatch supports synchronous `emit`, awaited concurrent `parallel`, awaited ordered bail `serial`, synchronous bail, and around-middleware `waterfall`.
- Waterfall listeners receive `next` last. Returning without calling it short-circuits the remaining listeners and built-in terminal operation.
- Dispatch may carry a receiver whose context filter selects admitted listeners. DSH agent scope builds routing carriers on top of this facility.
- Python decision: Phase 1 implements `emit`, `parallel`, `serial`, and `waterfall`. Scope-filtered event carriers arrive with agent scope.

## DSH scope versus Cordis isolation

- `packages/core/scope/src/index.ts`: DSH scope uses opaque object identity and a parent relation. A listener registered in an ancestor scope receives descendant events; events do not flow downward.
- `packages/core/scope/src/store.ts`: registries have a global layer plus lazily created exact-scope layers. Reads merge global, far ancestors, then the nearest scope so nearest named entries win.
- This is separate from Cordis service isolation. Isolation selects a service implementation; DSH scope selects registry contributions and event listeners for an agent.
- Python decision: Phase 1 implements service isolation only. Agent scope and layered registries belong to Phase 2.

## Agent spine

- `docs/architecture.md` and `packages/core/agent-loop/src/agent.ts`: a turn contains zero or more steps. A step contains one model request and its tool executions.
- The loop claims inbox input, assembles prompt sections and tool schemas, runs `agent/pre-step`, appends entered user messages, derives model history from the session log, streams the request, logs raw chunks and the assembled assistant message, executes tool calls, and repeats if more model work is owed.
- `followup` targets the next turn and wakes the driver. `steer` targets the next step and wakes it. `inject` targets the next step without waking it.
- Capability interception belongs to agent/tool/LLM events rather than edits to the loop.

## Session log

- `packages/core/session`: an append-only session event log is authoritative. Model history is a projection of its surface, not a separately mutated message list.
- Model-visible input must be reconstructable from the log. Raw assistant chunks are retained for replay/UI fidelity; assembled messages drive history.
- Surface events can append or replace an inclusive range, allowing compaction without rewriting the raw log.
- Python decision: preserve append-only authority and surface projection in Phase 2. Do not add model-visible context without a durable event.

## Tool and LLM pipelines

- `packages/core/tools`: tool execution passes through `tools/pre-execute`, monotonic guards, `tools/execute`, `tools/post-execute`, definition finalization, and final result observation. Approval, sandbox policy, timeout, and rewriting remain plugins.
- `packages/llm/llm`: adapters register provider routes. Streaming exposes raw chunks with one terminal finish. Operational provider failure becomes a terminal stream result; plugin/consumer failures may throw.
- The agent loop freezes and logs the effective request header, including rendered prompt and tool schemas.

## Browser plugin runtime

- `.agents/notes/implemented/architecture/2026-07-23-client-plugin-loading-model.md`: host and browser run separate Cordis trees with the same Loader governance.
- Client plugin packages declare `dsh.client` and export `./client`. The host scans mounted Loader entries, resolves built `client.js`, hashes it, serves it, and emits a browser boot graph.
- The browser module system loads independent bundles into a lazy module table. Cordis Loader owns activation, dependency waiting, disposal, and refresh. HMR invalidates the old module, disposes the old fiber, removes owned styles, imports the new revision, and mounts a new fiber.
- A client-only package still has an empty host `apply()` so one host composition row owns its roster presence. A dual-face package has real root and `./client` behavior. A backend-only package has no client declaration.
- Python decision: retain Cordis TS. A root plugin manifest replaces npm metadata as cross-language identity; nested frontend package metadata is build-only.

## Profiles and composition

- `packages/boot/app-boot` and `packages/bundle/base`: a profile composes ordered bundle patch layers, then profile, home, and command overlays. Rows are mounted concurrently; service dependencies, not row order, determine activation.
- User patch changes can recompose the tree. Failed candidates leave the last good tree active.
- Python decision: plugin composition and transactional config replacement arrive after the lifecycle kernel.

## Questions not yet resolved

- Exact production install/update transaction across multiple connected browser clients.
- Python session format and whether the first milestone reads TypeScript v0 logs.
- Worker-process isolation granularity for third-party backend plugins.
- Wire IDL choice and TypeScript/Python code-generation toolchain.
