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

## Capability order

[Implementation progress](../progress.md) owns completion state and verification evidence. The phase order records dependencies between the specifications:

1. [PyCordis kernel](cordis-core.md) establishes Services, Fibers, Effects, and Events.
2. [Backend Agent Spine](agent-spine.md) establishes durable model-facing state and extension registries.
3. [Dynamic Plugin Manager](plugin-manager.md) owns root manifests, Revisions, and both contribution lifecycles.
4. [Browser Bridge](browser-bridge.md) projects client Revisions into Cordis TS and carries explicit RPC and Events.
5. [Host Assembly](host-assembly.md) composes providers, catalogs, HTTP/WebSocket transport, and process teardown.
6. [Plugin Authoring SDK](plugin-sdk.md) provides the supported author APIs over both lifecycle runtimes.
7. [Plugin Templates and Scaffolding](plugin-templates.md) depends on the SDK and generates all three contribution forms.
8. [Multi-Page Client Activation](multi-page-activation.md) derives deployment readiness from Manager and Bridge state independently of authoring tooling.
9. [Agent Runtime Assembly](agent-runtime-assembly.md) connects the Agent Spine to a DeepSeek-compatible provider and exposes serialized, cancellable Turn invocation.
10. [Plugin Control Plane and Local Distribution](plugin-control-plane.md) adds loopback lifecycle operations, catalog watching, and a locally distributable browser SDK.

Product capabilities such as persistence, filesystem, subprocess, sandbox, terminal, LSP, compaction, subagents, workflow, skills, settings, and credentials each require their own later specification and remain ordinary plugins over these foundations. Remote distribution, trust, and backend isolation also remain later phases.

## Acceptance milestone

The authoring path is proven when a generated full-stack plugin builds without network-dependent runtime behavior, activates through the assembled Host, exchanges typed RPC and Events, updates both Revisions, and removes every contribution cleanly on disable.
