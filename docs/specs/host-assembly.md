# Host Assembly Specification

Status: Phase 5 normative specification

## Purpose

The Host Assembly turns the lifecycle kernel, Agent Spine, Dynamic Plugin Manager, and Browser Bridge into one runnable application. It owns process startup and shutdown while runtime behavior remains in plugins.

## Scope

Phase 5 includes a PyCordis Browser Bridge plugin, typed Host configuration, plugin catalog discovery and activation, an aiohttp listener, optional browser runtime delivery, a command-line entrypoint, deterministic teardown, and a keyless real-browser lifecycle scenario.

The Host does not add Agent behavior, plugin-specific RPC methods, or hidden lifecycle state. It obtains the Agent Spine, Plugin Manager, client artifact registry, and Browser Bridge from PyCordis Services after their provider Fibers become active.

## Core composition

One `HarnessHost` owns one `Cordis` runtime. Startup mounts the Agent Spine, Plugin Manager, and Browser Bridge provider plugins. The Browser Bridge provider declares the client artifact registry as a Service dependency and provides the Bridge, RPC registry, and Event registry as Services for backend plugins.

The Host rejects a core provider that does not reach `ACTIVE`. It never constructs a second Manager or Bridge outside the provider plugins.

## Configuration

`HarnessHostConfig` contains a non-empty Session ID, bind host, port, zero or more plugin catalog directories, and an optional browser runtime Bundle path. Paths resolve before the listener starts. The port accepts `0` for an operating-system-assigned test port.

Each plugin catalog directory contains immediate plugin directories with root `plugin.toml` files. Startup discovers candidates in stable order, rejects every discovery diagnostic, installs each unique Plugin ID, and enables it. A required contribution failure aborts startup. An optional contribution failure may produce `DEGRADED` without aborting the Host.

## HTTP application

The Host exposes the Browser Bridge artifact and WebSocket routes and a keyless `/health` endpoint. When a browser runtime Bundle is configured, `/` returns the fixed bootstrap document and `/browser.js` serves that exact file without directory traversal or fallback. The bootstrap creates one Cordis TS root Context and one `BridgeConnection` after its WebSocket opens.

The included listener binds before startup returns. `base_url` reports the effective address, including an assigned port. Host configuration is trusted local input; Internet-facing policy remains outside this phase.

## Lifecycle

`start()` is single-shot. A second call fails, including after shutdown. Any startup failure closes the partially created listener, disables every installed plugin that reached a serving state, and closes PyCordis before returning the original failure.

`close()` is idempotent and joins concurrent callers. It stops accepting HTTP/WebSocket traffic, disables installed plugins in stable reverse order, and then closes PyCordis. Client publications, backend Effects, Bridge registrations, Agent Services, and child Fibers are absent when it returns. Cleanup attempts continue after an individual failure and report an exception group.

The asynchronous context manager calls `start()` on entry and `close()` on exit. The command-line entrypoint waits for process termination signals and then uses the same close path.

## Browser lifecycle evidence

The keyless browser scenario builds the browser runtime, starts a Host on a loopback ephemeral port, and enables one full-stack plugin. Chromium loads the bootstrap document, imports the content-addressed client Bundle, mounts a real Cordis TS Fiber, invokes an Effect-owned Python RPC method, exchanges Events, and renders observable revision state.

The scenario updates backend and client bytes, observes old client Effect cleanup before the replacement becomes active, rejects the stale Revision, and disables the plugin. The page observes removal, and the Host closes with no backend Fiber or client publication left.

## Failure handling

- Missing catalogs, invalid browser runtime paths, discovery diagnostics, duplicate Plugin IDs, and required activation failures reject startup before it returns a URL.
- A listener bind failure closes the PyCordis composition and installed plugin contributions.
- Browser runtime delivery serves one configured regular file and never resolves request-controlled paths.
- WebSocket and client activation failures retain the Browser Bridge diagnostics defined by Phase 4.
- Shutdown aggregates cleanup failures after attempting every owned cleanup.

## Acceptance criteria

- Unit tests prove core Service composition, stable catalog activation, assigned-port reporting, startup rollback, idempotent close, and browser-runtime path validation.
- The CLI and `python -m harness` share one parser and lifecycle implementation.
- Backend plugins register Bridge RPC and Events through the official PyCordis Service keys.
- A real Chromium scenario proves browser Bundle delivery, Cordis TS activation, RPC, bidirectional Events, dual-contribution update, old Effect cleanup, stale rejection, disable, and Host teardown.
- Ruff, strict Pyright, Python tests, TypeScript type checking/tests/build, source and Wheel builds, and isolated Wheel imports pass.
- `docs/progress.md` records Phase 5 evidence and the next concrete milestone.

## Exclusions

Persistent inventory and Sessions, process isolation, remote package installation, dependency resolution, signatures, authentication, authorization, TLS termination, client activation aggregation across pages, and plugin authoring templates remain separate capabilities. The browser runtime Bundle is built from `frontend/`; packaging it into a distributable application is not part of this phase.
