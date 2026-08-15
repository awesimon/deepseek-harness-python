# Agent Runtime Assembly 规范

[English](agent-runtime-assembly.md) | 中文

状态：第九阶段规范

## 用途

Agent Runtime Assembly 将 [Agent Spine](agent-spine.zh.md) 连接到 DeepSeek-Compatible HTTP Provider，并通过 Host 和命令行提供一个可运行的 Turn 入口。Prompt、Tool、Policy 和 LLM Route 仍由 Plugin 所有；Provider Wiring 和 Invocation Transport 不向 Agent Loop 添加行为。

## 范围

第九阶段包括归 Effect 所有的 DeepSeek-Compatible LLM Adapter、经过校验的 Provider 和 Turn 配置、显式 Default Route Resolution、一个 Session-Scoped Invocation Service、非流式 HTTP Invocation API、显式 Cancellation、HTTP Client Command、无密钥 Provider Conformance Test，以及可选的真实 API 场景。

Adapter 消费 Provider Streaming Response，使 Raw Chunk 继续进入 Session Log。Host API 等待终止 Turn Result 并返回一个 JSON Response；将模型 Chunk 转发给 HTTP Client 不属于本阶段。

## Runtime Composition

Agent Spine 进入 Active 后，Host 挂载 Agent Runtime Provider。该 Provider 依赖 Agent Loop、LLM Registry 和 Session Log Service，为 Host Invocation 创建一个稳定的 Root Agent Scope，通过 PyCordis Effect 注册配置的 LLM Adapter，并将 Invocation Service 和解析后的 Default Route 发布为 Service。

Host 不构造第二个 Agent Loop、Session Log 或 LLM Registry。Backend Plugin 继续通过第二阶段 Service 提供 Prompt、Tool、Middleware 和其他精确 LLM Route。卸载 Adapter 会从后续 Resolution 中移除对应 Route，但不会更改已记录的 Request 或已完成的 Turn。

## 配置和凭据

`HarnessHostConfig` 接受可选的 `DeepSeekHTTPConfig` 和正数 Default Maximum Step Count。Provider Config 包含非空的 Route Provider Name、Model Name、HTTP Base URL、API Key、正数 Connect Timeout 和正数 Total Request Timeout。Base URL 必须使用 HTTP 或 HTTPS，且不得包含 User Information、Query Parameter 或 Fragment；Adapter 去除末尾 Slash 后追加固定 `/chat/completions` Path。

Programmatic Caller 在内存中传入 API Key。Server CLI 从指定环境变量解析凭据，默认使用 `DEEPSEEK_API_KEY`；它不接受明文 Key Argument。配置 Provider 但缺少凭据时启动失败。Config Representation、Log、Diagnostic、Session Event、Host Response 和 Exception Message 绝不包含 Key 或 Authorization Header。

Provider Config 是可选项，因此 Host 仍可运行 Browser-Only 和无密钥 Composition。未配置 Default Route 时，Invocation Endpoint 仍然存在，但省略 Route 的请求会被拒绝为不可用。如果 Backend Plugin 注册了对应 Route，显式请求仍可成功。

## Route Resolution

`DeepSeekHTTPConfig` 精确注册一个 `LLMRoute(provider, model)`，并将其选为 Host Default。重复注册会导致 Host 启动失败。Invocation 可以省略 Route 并使用解析后的 Default，也可以同时提供 Provider 和 Model，请求一个精确的已注册 Route。只提供其中一个 Field 属于无效输入。

Invocation Service 在 Agent Loop 接受 User Input 前校验 Effective Route。Admission 时 Route 不可用会导致失败，并且不会产生 `UserInputAccepted` 或 `ModelRequestRecorded` Event。每个已接受 Invocation 都会把完整 `LLMRoute` 传给 Loop，Effective Route 仍属于每个已记录的 `ModelRequest`；Adapter 执行期间绝不选择 Fallback Provider 或 Model。如果 Adapter 在 Admission 后卸载，或者 Middleware 选择了不可用 Route，受影响 Step 会记录 Failure，并保留此前的 Turn Event。

## DeepSeek-Compatible HTTP Adapter

对于每个 `ModelRequest`，Adapter 发送一个带认证的 JSON `POST`，并设置 `stream: true`。非空 Rendered System Prompt 成为第一个 System Message，随后按顺序附加 Request History。Assistant Tool Call 和 Tool Result 保留 Call ID。每个 `ModelToolDefinition` 转换为 OpenAI-Compatible Function Tool，并保持精确的 Name、Description 和 Parameter。Request Model 来自 Route Model；Transport Config 不能替换它。

Adapter 接受 `text/event-stream` Data Record，忽略 Comment 和 Keepalive Line，解码每个非终止 `data` JSON Value，并在解释前发出包含原始解码值的 `ModelChunk`。`[DONE]` 结束 Transport Input，但不能取代必需的 Terminal Choice。Content Delta 按到达顺序拼接。分片 Tool Call 按 Choice Index 和 Tool Index 组装；ID 和 Function Name 必须保持一致，拼接后的 Argument Text 必须在产生 Terminal Response 前解码为一个 JSON Object。

一个成功 Stream 产生且只产生一个 `ModelResponse`，其中包含组装后的 Content、Tool Call 和 Provider Finish Reason。缺少 Choice、Tool Call Fragment 不一致、Argument JSON 无效、Stream 提前结束、Terminal Completion 后仍有输出，或存在多个 Terminal Choice，均属于 Provider Protocol Failure，且不会虚构 Assistant Message。

HTTP、Network、Timeout 和 Provider Protocol 的运行故障产生一个终止 `ModelProviderFailure`，其中包含稳定 Code、Retryable Flag、可选 HTTP Status 和不含凭据的 Message。第九阶段扩展 Adapter Output，使一个 Stream 由且只由一个 `ModelResponse` 或 `ModelProviderFailure` 终止。Agent Loop 将 Provider Failure 记录为 `StepFailed`，Invocation 失败，并且不产生 `AssistantMessageCommitted` Event。Adapter Implementation Defect 仍可抛出异常；Loop 通过现有 Adapter-Error Path 记录它。

非成功 HTTP Response 只读取有上限的 Body 作为 Diagnostic。Retry-After 和 Status 可以进入 Failure Metadata，但 Adapter 不执行自动 Retry：重复模型请求会改变成本和时序，因此 Retry Policy 属于 Plugin。

## Session 和 Invocation Lifecycle

一个 Host 拥有一个 Process-Lifetime Session 和一个 Invocation Service。该 Session 的 Turn 在一个 Serialization Lock 下按 FIFO 顺序执行，因此并发 HTTP Request 的 Model History 不会交错。Queued Invocation 在取得执行所有权前不追加 User Input。已完成 Turn 保留在 Session History 中，供下一个 Turn 使用。

Provider 启动前，Agent Loop 将已接受 User Input 和完整 Effective Model Request 追加到权威的 Append-Only Session Log。Raw Chunk、Terminal Output、Tool Activity、Cancellation 和 Failure 保留第二阶段定义的顺序。第九阶段保留“先追加、后调用”和可重建义务，但当前 Session Log 仍是 Process-Local Memory；本阶段不承诺 Crash Recovery、Restart Persistence 或稳定 On-Disk Session Format。

每个 Request 携带由 Client 生成的不透明 Invocation ID。Service 拒绝已处于 Queued 或 Active 状态的 ID。Cancellation 移除 Queued Invocation 且不创建 Session Event，或者取消 Active Turn Task。Active Model 或 Tool Cancellation 通过 Loop 传播，记录现有 Structured Cancellation Outcome，关闭 Provider Response，并且绝不成为成功结果。第九阶段不保留 Durable Invocation Registry，因此 Settled Invocation ID 可以复用。

## Host API

`POST /api/v1/agent/invocations/{invocation_id}` 接受 JSON Object，其中包含一个非空 `input` String，以及可选的 Route Object；Route Object 必须同时包含 `provider` 和 `model`。该请求等待串行 Turn 完成，并返回 `200`，内容包含 Invocation ID、Session ID、Turn ID、Step Count 和终止 Assistant Message。Endpoint 不返回 Raw Provider Chunk、Credential、Internal Exception 或无关 Session History。

`DELETE /api/v1/agent/invocations/{invocation_id}` 请求取消一个 Queued 或 Active Invocation。ID 仍存活时返回已接受的 Cancellation Result；ID 未知或已经 Settled 时返回 Not-Found Result。即使并发 DELETE 发生竞争，Service-Level Cancellation 仍然幂等。

Malformed Input 和不完整 Route 返回 Structured `400` Response；重复 Live ID 返回 `409`；Route 不可用或 Default 未配置返回 `503`；Provider HTTP 和 Protocol Failure 返回 `502`；Provider Timeout 返回 `504`；Maximum-Step Exhaustion 和 Cancellation 返回稳定的非成功 Response。每个 Error Body 都包含 `code` 和 `message`，并且可以包含不暴露 Secret 的 Retryable Provider Metadata。

该 API 沿用 Host 的 Trusted-Deployment Stance。第九阶段不增加 Authentication、Authorization、Tenant Separation、Rate Limit 或 Internet-Facing Policy。

## CLI

Server CLI 接受显式 Provider Activation Option，包括 Provider Name、Model、Base URL、Credential Environment-Variable Name、Connect Timeout、Total Timeout 和 Maximum Step。省略 Provider Activation 时，Host 保持无密钥运行，且不读取 Credential Environment Variable。

`deepseek-harness-python invoke --url URL [--provider PROVIDER --model MODEL] TEXT` 通过相同 HTTP API 调用已经运行的 Host；成功时只向 Standard Output 写入终止 Assistant Content，失败时向 Standard Error 写入一条 Structured Diagnostic。Provider 和 Model Override 必须同时出现。Command 生成新的 Invocation ID；中断时先发送一次 Best-Effort DELETE，再以 Interrupt Status 退出。Command 自身绝不读取或传输 API Key。

## Host Shutdown 和失败处理

Host Shutdown 先停止接受新 Invocation，取消 Queued 和 Active Work，等待 Provider Response Cleanup 和 Tool Cancellation，然后继续第五阶段定义的 Plugin 和 PyCordis Teardown。并发 Shutdown 和 DELETE 共用同一幂等 Cancellation Path。`close()` 返回时，不保留 Invocation Task、HTTP Response、Adapter Registration 或持有 Credential 的 Client Session。

可以独立校验的 Invalid Config、重复 Default Route 和缺少已配置 Credential 会在 Listener 启动前失败。Provider Failure 影响当前 Invocation 及其 Session Event，但不会停止 Host 或卸载健康 Plugin。决策使用稳定 Failure Code；Remote Exception Text 和 Response Body 仅作为 Diagnostic Text。

## 验收标准

- Config Test 验证 URL、Timeout、Credential、Maximum-Step 和 Paired Route Validation，以及 Secret-Safe Representation 和 Diagnostic。
- Adapter Test 使用本地 Fake HTTP Server 验证 Request Mapping、Raw Chunk Order、分片 Content 和 Tool Call Assembly、恰好一个 Terminal Result、有上限的 Error Handling、Timeout、Cancellation Cleanup，以及不存在 Credential Leakage。
- Agent Test 验证 Operational Failure 成为 Terminal Provider Failure 和已记录 Step Failure，且不会虚构 Assistant Message。
- Invocation Test 验证在 User Input 前完成 Default 和 Explicit Route Resolution、FIFO Session Serialization、Completed-History Reuse、Queued 和 Active Cancellation、重复 Live-ID Rejection，以及 Shutdown Join 所有 Work。
- Host Test 使用 Fake Adapter 运行真实 HTTP Route，并在没有 DeepSeek Key 时验证 Structured Success 和 Failure Response。
- CLI Test 通过真实 Host API 验证 Terminal Output、Route Override Validation、非零 Failure Status 和 Best-Effort Interrupt Cancellation。
- 可选真实 API Test 使用 `DEEPSEEK_API_KEY`，缺少时自行 Skip，执行一个有界且无 Tool 的 Turn，并且绝不记录或打印 Credential。
- Ruff、严格 Pyright、Python Test、Source 和 Wheel Build、Isolated Wheel Import 及 Documentation Check 全部通过；`docs/progress.md` 记录精确 Evidence。

## 不在范围内

Disk-Backed Session、Crash Recovery、与 TypeScript 的 Session Format Compatibility、Compaction、Multi-Session Routing、Resumable 或 Server-Streamed HTTP Response、Automatic Provider Retry、Token Accounting、Rate Limiting、Credential Storage、OAuth、Approval、Sandbox Policy、Remote Package Installation、Worker-Process Isolation，以及 DeepSeek-Compatible Chat Completions Protocol 之外的 Provider Family，仍属于独立阶段。
