# Backend Agent Spine Specification

Status: Phase 2 normative specification

## Purpose

The Agent Spine turns durable Session input into model requests, tool executions, and durable output. It is intentionally small: plugins contribute prompts, tools, LLM adapters, policies, and observations through PyCordis Services and Events rather than by modifying the loop.

## Scope

Phase 2 includes:

- immutable Message, Tool, model request, model chunk, and model response values;
- an append-only in-memory Session Event Log with monotonic sequence numbers;
- deterministic projection from Session Events to model history and user-visible transcript;
- hierarchical Agent Scopes and layered Prompt and Tool registries;
- an LLM adapter registry with explicit route resolution;
- a Turn/Step Agent Loop that snapshots model-visible capabilities at each Step;
- effect-owned registrations and Event extension points;
- a keyless replay scenario using a deterministic fake LLM adapter.

Durable file or database storage, compaction, approval, sandbox policy, timeout, remote clients, and provider-specific wire formats remain outside this phase.

## Immutable values

Every public value passed between the Session, LLM, Tool, and Agent modules is an immutable dataclass. JSON fields use recursively JSON-compatible values. Opaque identifiers use distinct value types rather than interchangeable strings.

A Message has a Role, content, and optional Tool Call or Tool Result fields. A ModelRequest contains the complete messages, rendered System Prompt, Tool definitions, route, and Step identity used for one provider call. A ModelResponse contains the committed Assistant Message and zero or more Tool Calls. Streaming ModelChunks are diagnostic and presentation inputs; the committed response is the only Assistant Message used by later model history.

## Session Event Log

`SessionLog.append(event)` assigns a strictly increasing sequence number and stores an immutable envelope. Readers receive snapshots and cannot mutate stored events.

The initial Event set records:

- accepted User input;
- the complete effective model request before the adapter starts;
- every raw model chunk in arrival order;
- the committed Assistant Message and finish reason;
- Tool execution start with exact arguments;
- Tool execution completion with result or structured error.

Anything visible to a model is logged before or when it becomes visible. Model history is derived only from Session Events. The Agent Loop does not maintain a second mutable conversation list.

## Projection

`SessionProjector.model_history()` deterministically produces Messages from accepted User input, committed Assistant Messages, and completed Tool executions. Raw chunks and execution-start events do not enter model history.

`SessionProjector.transcript()` produces ordered user-visible entries without changing the Log. A projection either understands an Event type or fails explicitly; silently ignoring an unknown required Event is forbidden.

## Agent Scope and layered registries

An Agent Scope has opaque identity and an optional parent. Contributions may be global or attached to one exact Scope. Reads merge global contributions, then ancestors from farthest to nearest, then the exact Scope. A nearer contribution with the same name replaces a farther one for that read without deleting it.

Prompt sections have a stable name, order, and render callback. Tool definitions have a stable name, description, JSON Schema parameters, and async-capable handler. Registration returns a disposer and is always owned by a PyCordis Effect.

A Step captures immutable rendered Prompt sections and Tool definitions before logging its ModelRequest. Registry changes after the snapshot affect the next Step, never the in-flight request.

## LLM routing

An LLM route is an explicit provider and model pair. `LLMRegistry.resolve(route)` returns one registered adapter or fails before a model request is logged. No fallback provider or model is selected inside adapter execution.

An adapter accepts one immutable ModelRequest and yields zero or more ModelChunks followed by exactly one ModelResponse. Yielding a chunk after the terminal response, returning no response, or returning more than one response is an adapter protocol error.

## Turn and Step lifecycle

One Turn accepts one or more User Messages and contains one or more Steps. Each Step performs this sequence:

1. Read model history from the Session projection.
2. Snapshot and render Prompt and Tool contributions for the Agent Scope.
3. Build and durably append the complete ModelRequest event.
4. Stream the selected LLM adapter and append each raw chunk.
5. Append the committed Assistant Message.
6. If the response has Tool Calls, resolve and execute each call in response order, logging start and completion, then begin the next Step.
7. If the response has no Tool Calls, finish the Turn with its Assistant Message.

An unknown Tool name and invalid Tool arguments produce logged Tool errors and remain model-visible on the next Step. A Tool handler exception is converted to a structured Tool error; cancellation propagates and does not become a successful result.

The loop enforces a configurable positive maximum Step count. Reaching the limit fails the Turn after preserving every Event already appended.

## Extension events

Phase 2 defines effect-owned hooks at stable timing points:

- `agent/pre-step` may transform the pending ModelRequest through Waterfall delegation before it is logged;
- `agent/post-step` observes the committed response after it is logged;
- `tools/pre-execute` may transform a Tool invocation through Waterfall delegation before execution starts;
- `tools/post-execute` observes the logged Tool outcome.

Waterfall listeners must call `next()` to delegate. Short-circuiting is intentional replacement behavior.

## Failure handling

- Missing LLM routes fail before request logging because no effective provider call exists.
- Adapter protocol failures are logged as Step failures without inventing an Assistant Message.
- Projection rejects unknown required Events.
- Duplicate Prompt, Tool, or LLM registrations in the same layer fail at registration time.
- Disposing a contribution removes it from future snapshots but does not rewrite prior Session Events.

## Acceptance criteria

- Unit tests prove monotonic append, immutable snapshots, deterministic history projection, and rejection of unknown Events.
- Registry tests prove global/ancestor/exact precedence, duplicate rejection, and Effect-owned disposal.
- Adapter tests prove explicit routing and exactly-one-terminal-response enforcement.
- Loop tests prove request-before-provider ordering, raw chunk retention, Tool execution logging, multi-Step continuation, unknown Tool errors, maximum Step failure, and capability snapshot isolation.
- A keyless scenario runs a deterministic fake adapter and Tool through a real PyCordis composition and asserts the resulting Session transcript.
- `docs/progress.md` records the exact verification commands and remaining Phase 2 exclusions.
