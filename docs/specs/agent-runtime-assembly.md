# Agent Runtime Assembly Specification

English | [中文](agent-runtime-assembly.zh.md)

Status: Phase 9 normative specification

## Purpose

The Agent Runtime Assembly connects the [Agent Spine](agent-spine.md) to a DeepSeek-compatible HTTP provider and exposes one runnable Turn entry path through the Host and command line. It preserves plugin ownership of prompts, tools, policies, and LLM routes; provider wiring and invocation transport do not add behavior to the Agent Loop.

## Scope

Phase 9 includes an effect-owned DeepSeek-compatible LLM adapter, validated provider and Turn configuration, explicit default-route resolution, one Session-scoped invocation service, a non-streaming HTTP invocation API, explicit cancellation, an HTTP client command, keyless provider conformance tests, and an optional real-API scenario.

The adapter consumes provider streaming responses so raw chunks remain available to the Session log. The Host API waits for the terminal Turn result and returns one JSON response; forwarding model chunks to HTTP clients is outside this phase.

## Runtime composition

The Host mounts an Agent Runtime provider after the Agent Spine becomes active. The provider requires the Agent Loop, LLM Registry, and Session Log Services, creates one stable root Agent Scope for Host invocations, registers configured LLM adapters through PyCordis Effects, and publishes the invocation service and resolved default route as Services.

The Host never constructs a second Agent Loop, Session Log, or LLM Registry. Backend plugins continue to contribute prompts, tools, middleware, and additional exact LLM routes through the Phase 2 Services. Unloading an adapter removes its route from future resolution without changing recorded requests or completed Turns.

## Configuration and credentials

`HarnessHostConfig` accepts an optional `DeepSeekHTTPConfig` and a positive default maximum Step count. The provider configuration contains a non-empty route provider name, model name, HTTP base URL, API key, positive connect timeout, and positive total request timeout. The base URL must use HTTP or HTTPS and must not contain user information, query parameters, or a fragment; the adapter appends the fixed `/chat/completions` path after removing trailing slashes.

Programmatic callers pass the API key in memory. The server CLI resolves it from a named environment variable, defaulting to `DEEPSEEK_API_KEY`; it does not accept a plaintext key argument. Missing credentials fail startup when the provider is configured. Configuration representations, logs, diagnostics, Session Events, Host responses, and exception messages never include the key or Authorization header.

Provider configuration is optional so the Host can still serve browser-only and keyless compositions. Without a configured default route, the invocation endpoint remains present but rejects an omitted route as unavailable. An explicitly requested route may still succeed when a backend plugin registered it.

## Route resolution

`DeepSeekHTTPConfig` registers exactly one `LLMRoute(provider, model)` and selects it as the Host default. Duplicate registration fails Host startup. An invocation may omit its route and use that resolved default, or provide both provider and model to request one exact registered route. Providing only one field is invalid.

The invocation service validates the effective route before accepting User input into the Agent Loop. A route that is unavailable at admission fails without a `UserInputAccepted` or `ModelRequestRecorded` Event. Every admitted invocation passes a complete `LLMRoute` to the Loop, and the effective route remains part of each recorded `ModelRequest`; adapters never select a fallback provider or model during execution. If an adapter unloads or middleware selects an unavailable route after admission, the affected Step records a failure while preserving earlier Turn Events.

## DeepSeek-compatible HTTP adapter

For each `ModelRequest`, the adapter sends one authenticated JSON `POST` with `stream: true`. A non-empty rendered System Prompt becomes the first system message, followed by the request history in order. Assistant Tool Calls and Tool results retain their call IDs. Each `ModelToolDefinition` becomes an OpenAI-compatible function Tool with its exact name, description, and parameters. The request model is the route model; transport configuration cannot replace it.

The adapter accepts `text/event-stream` data records, ignores comment and keepalive lines, decodes every non-terminal `data` JSON value, and emits a `ModelChunk` containing the raw decoded value before interpreting it. `[DONE]` ends transport input but does not replace the required terminal choice. Content deltas are concatenated in arrival order. Fragmented Tool Calls are assembled by choice index and tool index; IDs and function names must remain consistent, and concatenated argument text must decode to one JSON object before the terminal response is produced.

One successful stream yields exactly one `ModelResponse` with assembled content, Tool Calls, and the provider finish reason. Missing choices, inconsistent Tool Call fragments, invalid argument JSON, premature end of stream, output after terminal completion, or multiple terminal choices are provider protocol failures and never invent an Assistant Message.

Operational HTTP, network, timeout, and provider protocol failures produce one terminal `ModelProviderFailure` containing a stable code, retryable flag, optional HTTP status, and credential-free message. Phase 9 extends adapter output so exactly one `ModelResponse` or `ModelProviderFailure` terminates a stream. The Agent Loop records a provider failure as `StepFailed` and the invocation fails without an `AssistantMessageCommitted` Event. Adapter implementation defects may still raise; the Loop records them through its existing adapter-error path.

Non-success HTTP responses read only a bounded response body for diagnostics. Retry-After and status may inform the failure metadata, but the adapter performs no automatic retry: retry policy belongs to a plugin because repeating a model request changes cost and timing.

## Session and invocation lifecycle

One Host owns one process-lifetime Session and one invocation service. Turns for that Session execute in FIFO order under one serialization lock so model history cannot interleave across concurrent HTTP requests. A queued invocation does not append User input until it acquires execution ownership. A completed Turn remains in the Session history used by the next Turn.

Before the provider starts, the Agent Loop appends accepted User input and the complete effective model request to the authoritative append-only Session Log. Raw chunks, terminal output, Tool activity, cancellation, and failure retain the Phase 2 ordering. Phase 9 preserves this append-before-provider and reconstructability obligation, but the current Session Log remains process-local memory; crash recovery, restart persistence, and a stable on-disk Session format are not claimed by this phase.

Each request carries a client-generated opaque Invocation ID. The service rejects an ID that is already queued or active. Cancellation removes a queued invocation without creating Session Events, or cancels the active Turn task. Active model or Tool cancellation propagates through the Loop, records the existing structured cancellation outcome, closes the provider response, and never becomes success. A settled Invocation ID may be reused because Phase 9 keeps no durable invocation registry.

## Host API

`POST /api/v1/agent/invocations/{invocation_id}` accepts a JSON object with one non-empty `input` string and an optional route object containing both `provider` and `model`. It waits for the serialized Turn and returns `200` with the Invocation ID, Session ID, Turn ID, Step count, and terminal Assistant Message. The endpoint does not return raw provider chunks, credentials, internal exceptions, or unrelated Session history.

`DELETE /api/v1/agent/invocations/{invocation_id}` requests cancellation of one queued or active invocation. It returns an accepted cancellation result when the ID is live and a not-found result when it is unknown or already settled. Cancellation is idempotent at the service level even when concurrent DELETE requests race.

Malformed input and incomplete routes return a structured `400` response; duplicate live IDs return `409`; an unavailable route or unconfigured default returns `503`; provider HTTP and protocol failures return `502`; provider timeouts return `504`; maximum-Step exhaustion and cancellation return stable non-success responses. Every error body contains `code` and `message`, and may contain retryable provider metadata without exposing secrets.

The API inherits the Host's trusted-deployment stance. Phase 9 adds no authentication, authorization, tenant separation, rate limit, or Internet-facing policy.

## CLI

The server CLI accepts explicit provider activation options for provider name, model, base URL, credential environment-variable name, connect timeout, total timeout, and maximum Steps. Omitting provider activation leaves the Host keyless and does not inspect the credential environment variable.

`deepseek-harness-python invoke --url URL [--provider PROVIDER --model MODEL] TEXT` calls an already running Host through the same HTTP API, writes only the terminal Assistant content to standard output on success, and writes one structured diagnostic to standard error on failure. Provider and model overrides must appear together. The command generates a fresh Invocation ID; on interrupt it sends one best-effort DELETE before exiting with the interrupt status. It never reads or transmits an API key itself.

## Host shutdown and failure handling

Host shutdown first stops accepting new invocations, cancels queued and active work, waits for provider response cleanup and Tool cancellation, and then continues the Phase 5 plugin and PyCordis teardown. Concurrent shutdown and DELETE share the same idempotent cancellation path. No invocation task, HTTP response, adapter registration, or credential-bearing client session remains when `close()` returns.

Self-contained invalid configuration, duplicate default routes, and missing configured credentials fail before the listener starts. Provider failures affect the invocation and its Session Events but do not stop the Host or unload healthy plugins. Failure messages use stable codes for decisions; remote exception text and response bodies are diagnostic text only.

## Acceptance criteria

- Configuration tests prove URL, timeout, credential, maximum-Step, and paired route validation plus secret-safe representations and diagnostics.
- Adapter tests use a local fake HTTP server to prove request mapping, raw chunk order, fragmented content and Tool Call assembly, exactly one terminal result, bounded error handling, timeout, cancellation cleanup, and absence of credential leakage.
- Agent tests prove operational failures become terminal provider failures and logged Step failures without an invented Assistant Message.
- Invocation tests prove default and explicit route resolution before User input, FIFO Session serialization, completed-history reuse, queued and active cancellation, duplicate live-ID rejection, and shutdown joining all work.
- Host tests exercise the real HTTP routes with a fake adapter and prove structured success and failure responses without a DeepSeek key.
- CLI tests prove terminal output, route override validation, nonzero failure status, and best-effort interrupt cancellation against the real Host API.
- An optional real-API test uses `DEEPSEEK_API_KEY`, self-skips when absent, performs one bounded no-Tool Turn, and never records or prints the credential.
- Ruff, strict Pyright, Python tests, source and Wheel builds, isolated Wheel import, and the documentation checks pass; `docs/progress.md` records the exact evidence.

## Exclusions

Disk-backed Sessions, crash recovery, Session format compatibility with TypeScript, compaction, multi-Session routing, resumable or server-streamed HTTP responses, automatic provider retries, token accounting, rate limiting, credential storage, OAuth, approval, sandbox policy, remote package installation, worker-process isolation, and provider families beyond the DeepSeek-compatible Chat Completions protocol remain separate phases.
