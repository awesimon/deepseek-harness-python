# Python Harness Architecture Specification

English | [中文](architecture.zh.md)

Status: draft implementation specification

## Objective

Build a plugin-first Python backend for DeepSeek Harness while retaining the TypeScript Cordis runtime for browser plugins. Once the runtime foundations are stable, product work should normally consist of adding plugins rather than changing a central agent loop.

## Two runtimes, one logical plugin model

The system has two independent runtime containers:

- **Cordis TS** owns browser services, UI slots, session presentation, client state, and client hot reload.
- **PyCordis** owns agents, session logs, LLM providers, tools, storage, sandboxing, workflows, and backend lifecycle.

A logical plugin has one identity and version. It may contribute to either runtime or both:

| Form | Backend contribution | Client contribution |
|---|---:|---:|
| Backend-only | yes | no |
| Client-only | no | yes |
| Full-stack | yes | yes |

The two contributions have independent fibers and effects. They communicate only through an explicit wire contract. Neither runtime exposes its context or in-process service objects to the other.

## Plugin artifact

The root manifest is the only authority for plugin identity, version, activation policy, permissions, and contribution locations. Language-specific manifests are private build inputs.

```toml
[plugin]
id = "com.example.git"
version = "1.0.0"
runtime_api = "1"

[backend]
entrypoint = "git_plugin:plugin"

[client]
bundle = "dist/client.js"
platform = "web"

[protocol]
schema = "protocol/api.schema.json"

[activation]
backend = "required"
client = "optional"
```

Every table except `[plugin]` is optional. A nested frontend `package.json` may drive the TypeScript build, but it is private and cannot define a second plugin identity or version.

## Activation

The Plugin Manager sits above both runtimes and owns installation and aggregate status:

```text
DISCOVERED -> VALIDATED -> BACKEND_ACTIVE -> CLIENT_PUBLISHED -> ACTIVE
                                |                  |
                                v                  v
                         BACKEND_FAILED      CLIENT_FAILED
                                                   |
                                                   v
                                               DEGRADED
```

For a backend contribution, the manager imports the entrypoint and mounts a PyCordis fiber. For a client contribution, the host publishes a content-hashed bundle row; connected browsers fetch the new revision and mount a Cordis TS fiber.

Required contribution failure rolls back the plugin's other contribution. Optional contribution failure produces `DEGRADED`. Because browsers are separate processes, activation is coordinated rather than transactionally atomic.

## Runtime visibility boundary

Dynamic changes commit at a capability-specific safe point:

- UI slots become visible after the client fiber registers them.
- RPC endpoints become visible after the backend fiber is active.
- tools and prompt sections become visible at the next agent step boundary;
- an in-flight LLM request or tool execution retains the capability snapshot with which it began;
- provider replacement causes dependent fibers to deactivate, clean up, and reactivate.

## Capability structure

Backend capabilities follow three roles:

1. **Service Definition**: Python protocol, request/result values, events, and errors.
2. **Service Provider**: one implementation registered in PyCordis.
3. **Consumer**: a tool, command, API, or another service using the definition.

Providers and consumers depend on definitions, not on one another. A provider swap therefore moves all consumers without changing them.

## Durable agent rule

The session event log is the source of model history. Anything visible to a model must be reconstructable from durable events. Prompt changes, tool schemas, injected context, request configuration, model chunks, assembled messages, calls, and results are logged according to their replay requirements.

## Security

- Backend services are not remotely callable by default.
- A remote method or event requires an explicit wire declaration and JSON-compatible values.
- Credentials remain backend-only; frontend configuration receives an explicit public projection.
- Plugin manifests declare filesystem, subprocess, network, credential, and browser permissions.
- Untrusted backend plugins should eventually run in worker processes. PyCordis unload removes registrations and resources but cannot erase imported Python code from process memory.

## Delivery phases

### Phase 1: PyCordis kernel

- service keys and isolation realms;
- plugin specs and dependency-driven fiber activation;
- reversible effects and failure rollback;
- event modes including waterfall middleware;
- provider replacement and dependent reactivation;
- focused lifecycle tests.

### Phase 2: Backend agent spine

- immutable message and stream vocabulary;
- append-only session log and surface projection;
- LLM adapter registry;
- scoped prompt and tool registries;
- minimal turn/step agent loop;
- keyless replay fixture.

### Phase 3: Dynamic plugin manager

- root plugin manifest and validation;
- Python entrypoint discovery;
- install, enable, disable, update, and aggregate status;
- backend revision workers for reliable code replacement;
- configuration and permission ownership.

### Phase 4: Browser bridge

- versioned protocol schema;
- Python and TypeScript code generation;
- RPC, cancellation, event forwarding, and opaque identity lookup;
- content-hashed client graph and connected-browser reconciliation;
- frontend-only and full-stack sample plugins.

### Phase 5: Product capabilities

- persistence and session querying;
- filesystem, subprocess, sandbox, terminal, and LSP;
- compaction, subagents, jobs, workflow, skills, settings, and credentials;
- Web application migration and compatibility removal.

## Acceptance milestone

The architecture is proven when one full-stack sample plugin can be installed while the application is running, add a backend tool, expose a remote method, register a browser result view, affect the next agent step, and remove every contribution cleanly on disable.
