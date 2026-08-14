# PyCordis Core Specification

English | [中文](cordis-core.zh.md)

Status: Phase 1 normative specification

## Purpose

PyCordis is the backend plugin lifecycle kernel. It preserves the behavioral ideas of Cordis without reproducing JavaScript implementation techniques.

## Service keys and realms

A `ServiceKey[T]` is a stable, name-based key with a static value type. A registration is addressed by `(ServiceKey, Realm)`. The root context uses one root realm per key. `Context.isolate()` returns a child context that resolves selected keys through fresh opaque realms.

Only one active registration may occupy a key and realm. Duplicate registration fails the providing fiber and rolls back every effect created during its activation.

## Dependency declaration

A plugin declares required service keys in `PluginSpec.requires`. Its fiber remains `PENDING` until every key resolves to an active provider in the fiber's realms.

`Context.require()` rejects undeclared access. Reflection and optional infrastructure code may use the explicit `Context.lookup()` API; ordinary plugins must not use reflection to hide dependencies.

## Fiber lifecycle

```text
PENDING -> LOADING -> ACTIVE
             |          |
             v          v
           FAILED <- UNLOADING -> PENDING
                                      |
                                      v
                                  DISPOSED
```

The dependency epoch is the ordered tuple of provider registration generations. When it changes, an active fiber unloads and may reactivate against the new epoch. A failed fiber retries only after its dependency epoch changes or an explicit retry request.

A service published while its provider is `LOADING` is visible to the provider itself but does not satisfy dependents until the provider reaches `ACTIVE`.

## Effects

Every registration is owned by an effect. An effect setup may return no cleanup, one cleanup, or an iterable of cleanups. Cleanups within one effect execute in reverse order. A fiber unload disposes its top-level effects concurrently and waits for all cleanup attempts.

Effects are single-shot. Activation failure disposes all effects created by the failed attempt before the fiber reaches `FAILED`.

## Events

An `EventKey` fixes an event name and dispatch mode:

| Mode | Semantics |
|---|---|
| `EMIT` | synchronous ordered observers; awaitables are rejected |
| `PARALLEL` | start all listeners and await all results |
| `SERIAL` | await listeners in order and stop at the first bail value |
| `WATERFALL` | compose listeners around an explicit terminal callback |

A serial result bails unless it is `None` or `False`. A waterfall listener receives `next` as its final argument. Not calling it short-circuits downstream listeners and the terminal behavior.

Listeners are effects and disappear with their owning fiber.

## Concurrency

Lifecycle convergence is serialized by the runtime. Plugin code may register services, listeners, effects, or child fibers while convergence is running; those mutations mark the graph dirty and the same convergence pass continues until no state can change.

Phase 1 assumes one asyncio event loop. Thread-safe mutation and cross-process plugins are later concerns.

## Intentional Python choices

- Explicit `require()` replaces JavaScript context Proxy access.
- `ServiceKey[T]` and Python protocols replace TypeScript declaration merging.
- All lifecycle entrypoints are async.
- Imported module eviction is not promised. Unload means resource and registration teardown.
- Config validation is an optional callable on `PluginSpec`; Pydantic integration belongs to the plugin manager phase.
