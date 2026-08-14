# 后端 Agent Spine 规范

状态：第二阶段规范

## 用途

Agent Spine 将持久化 Session Input 转换为模型请求、工具执行和持久化输出。它有意保持精简：插件通过 PyCordis Service 和 Event 提供 Prompt、Tool、LLM Adapter、Policy 和 Observation，而不是修改 Agent Loop。

## 范围

第二阶段包括：

- 不可变的 Message、Tool、模型请求、模型 Chunk 和模型响应值；
- 使用单调序号的只追加内存 Session Event Log；
- 从 Session Event 到模型历史和用户可见 Transcript 的确定性投影；
- 分层 Agent Scope、Prompt Registry 和 Tool Registry；
- 使用显式 Route Resolution 的 LLM Adapter Registry；
- 在每个 Step 对模型可见能力取快照的 Turn/Step Agent Loop；
- 归 Effect 所有的注册项和 Event 扩展点；
- 使用确定性 Fake LLM Adapter 的无密钥回放场景。

持久化文件或数据库存储、Compaction、Approval、Sandbox Policy、Timeout、Remote Client 和 Provider 专用 Wire Format 不属于本阶段。

## 不可变值

Session、LLM、Tool 和 Agent Module 之间传递的每个公开值都是不可变 Dataclass。JSON Field 使用递归的 JSON 兼容值。不透明标识使用不同的值类型，而不是可互换的字符串。

Message 包含 Role、Content，以及可选的 Tool Call 或 Tool Result Field。ModelRequest 包含一次 Provider Call 使用的完整 Message、渲染后的 System Prompt、Tool Definition、Route 和 Step Identity。ModelResponse 包含已提交的 Assistant Message 和零个或多个 Tool Call。流式 ModelChunk 用于诊断和展示；只有已提交响应会成为后续模型历史使用的 Assistant Message。

## Session Event Log

`SessionLog.append(event)` 分配严格递增的序号，并存储不可变 Envelope。Reader 获得 Snapshot，不能修改已存储 Event。

初始 Event 集记录：

- 已接受的 User Input；
- Adapter 启动前的完整有效 Model Request；
- 按到达顺序记录的每个 Raw Model Chunk；
- 已提交的 Assistant Message 和 Finish Reason；
- 使用精确 Argument 的 Tool Execution Start；
- 带 Result 或 Structured Error 的 Tool Execution Completion。

任何模型可见内容都在可见之前或可见时写入日志。模型历史只从 Session Event 派生。Agent Loop 不维护第二份可变 Conversation List。

## 投影

`SessionProjector.model_history()` 从已接受 User Input、已提交 Assistant Message 和已完成 Tool Execution 确定性生成 Message。Raw Chunk 和 Execution Start Event 不进入模型历史。

`SessionProjector.transcript()` 生成有序的用户可见 Entry，且不修改 Log。Projection 必须理解 Event Type，否则显式失败；禁止静默忽略未知的必需 Event。

## Agent Scope 和分层 Registry

Agent Scope 拥有不透明身份和可选 Parent。Contribution 可以是 Global，也可以附加到一个 Exact Scope。读取时依次合并 Global Contribution、从最远到最近的 Ancestor，最后合并 Exact Scope。读取结果中，较近 Scope 的同名 Contribution 替换较远 Contribution，但不会删除它。

Prompt Section 包含稳定 Name、Order 和 Render Callback。Tool Definition 包含稳定 Name、Description、JSON Schema Parameter 和支持 Async 的 Handler。注册操作返回 Disposer，并且始终归 PyCordis Effect 所有。

Step 在记录 ModelRequest 前捕获不可变的 Prompt Section 和 Tool Definition 快照。快照后的 Registry 变更只影响下一个 Step，不影响进行中的请求。

## LLM 路由

LLM Route 是显式的 Provider 和 Model Pair。`LLMRegistry.resolve(route)` 返回一个已注册 Adapter，否则在记录模型请求前失败。Adapter 执行期间不会选择后备 Provider 或 Model。

Adapter 接收一个不可变 ModelRequest，依次产生零个或多个 ModelChunk，最后产生且只产生一个 ModelResponse。在终止响应后继续产生 Chunk、不返回响应或返回多个响应，均属于 Adapter Protocol Error。

## Turn 和 Step 生命周期

一个 Turn 接受一个或多个 User Message，并包含一个或多个 Step。每个 Step 按以下顺序执行：

1. 从 Session Projection 读取模型历史。
2. 为 Agent Scope 捕获并渲染 Prompt 和 Tool Contribution。
3. 构建并持久追加完整的 ModelRequest Event。
4. 流式执行选定 LLM Adapter，并追加每个 Raw Chunk。
5. 追加已提交的 Assistant Message。
6. 如果响应包含 Tool Call，按响应顺序解析并执行每个调用，记录 Start 和 Completion，然后开始下一个 Step。
7. 如果响应不包含 Tool Call，以其 Assistant Message 结束 Turn。

未知 Tool Name 和无效 Tool Argument 会产生已记录的 Tool Error，并在下一个 Step 对模型可见。Tool Handler Exception 转换为 Structured Tool Error；Cancellation 会继续传播，不能变成成功结果。

Loop 强制执行可配置的正数最大 Step 数。达到限制时 Turn 失败，同时保留已经追加的全部 Event。

## 扩展事件

第二阶段在稳定时点定义归 Effect 所有的 Hook：

- `agent/pre-step` 可以通过 Waterfall Delegation 转换待处理 ModelRequest，之后请求才写入日志；
- `agent/post-step` 在响应写入日志后观察已提交响应；
- `tools/pre-execute` 可以通过 Waterfall Delegation 在执行开始前转换 Tool Invocation；
- `tools/post-execute` 观察已经写入日志的 Tool Outcome。

Waterfall Listener 必须调用 `next()` 才会 Delegate。Short Circuit 表示有意替换原行为。

## 失败处理

- 缺少 LLM Route 时在记录请求前失败，因为不存在有效 Provider Call。
- Adapter Protocol Failure 记录为 Step Failure，不会虚构 Assistant Message。
- Projection 拒绝未知必需 Event。
- 同一 Layer 中重复注册 Prompt、Tool 或 LLM 时立即失败。
- 释放 Contribution 会从后续 Snapshot 中移除它，但不会重写已有 Session Event。

## 验收标准

- 单元测试验证单调追加、不可变 Snapshot、确定性历史投影和未知 Event 拒绝。
- Registry 测试验证 Global/Ancestor/Exact 优先级、重复拒绝和 Effect 所有权释放。
- Adapter 测试验证显式路由和“恰好一个终止响应”约束。
- Loop 测试验证 Request 先于 Provider 调用写入、Raw Chunk 保留、Tool Execution Log、多 Step 继续、未知 Tool Error、最大 Step Failure 和 Capability Snapshot Isolation。
- 一个无密钥场景通过真实 PyCordis Composition 运行确定性 Fake Adapter 和 Tool，并断言生成的 Session Transcript。
- `docs/progress.md` 记录精确验证命令和第二阶段剩余排除项。
