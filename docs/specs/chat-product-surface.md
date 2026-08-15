# Browser Chat and DeepSeek-Compatible API Specification

English | [中文](chat-product-surface.zh.md)

Status: Phase 13 normative specification

## Scope

The Host serves a browser chat surface from the existing Browser Bridge bootstrap page. The page keeps the TypeScript Cordis bridge connection and adds a Session-aware chat presentation over the Host's public Chat Completions API.

The Host exposes `POST /chat/completions` and `POST /v1/chat/completions` with the DeepSeek/OpenAI Chat Completions request and response conventions. The browser sends `model`, `messages`, and `stream`; the Host maps the latest non-empty user message to the active Session Agent invocation and returns the configured exact model.

## Behavior

- The page receives the active `session_id` and configured model through escaped bootstrap metadata.
- On load and refresh, the page reads `GET /api/v1/sessions/{session_id}` and renders user, assistant, tool, and step-error transcript entries.
- A submitted message is appended optimistically, sent as a Chat Completions request, and followed by the committed Assistant response.
- Non-streaming requests return a `chat.completion` object with one terminal choice.
- Streaming requests return valid `chat.completion.chunk` SSE records followed by `data: [DONE]`; the current Agent response is emitted as one content chunk and one terminal chunk.
- Route, provider, and protocol failures retain structured HTTP status and error codes.
- The page works on desktop and narrow mobile viewports, preserves keyboard submission, and exposes accessible labels for the composer and actions.

## Acceptance

- Browser build, TypeScript checks, and Chat API tests pass.
- Host tests prove both Chat Completions paths, JSON output, SSE output, and unavailable-route errors.
- A real DeepSeek-compatible endpoint completes one browser-originated question without exposing the API key to the browser.

## Exclusions

Token-by-token forwarding from the internal Agent loop, stateless multi-conversation routing, request-supplied system prompt or tool definitions, usage accounting, authentication, and remote deployment policy remain later work.
