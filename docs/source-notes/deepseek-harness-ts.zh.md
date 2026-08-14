# DeepSeek Harness TypeScript 源码笔记

[English](deepseek-harness-ts.md) | 中文

参考 Commit：`47f943859bef60e4160492346772ded9b24f765a`

用途：记录已为 Python 改写检查过的 TypeScript 机制。重新打开原始源码前先查阅本文档。

## Cordis Context 和 Service

- `vendor/cordis/src/context.ts`：Context 是由 Proxy 支持的 Service Repository。子 Context 继承 Metadata。`isolate(name, label?)` 为后代替换 Service 名称到 Realm Label 的映射。`intercept()` 对特定 Service 的插件配置进行分层。
- `vendor/cordis/src/reflect.ts`：Service 实现按 Isolation Label 存储。插件属性读取受已声明的 Injection Chain 约束。`provide()` 是一个 Effect；它拒绝同一 Realm 中的重复 Provider，并在可用性改变时通知依赖该 Service 的 Fiber。
- Python 决策：使用显式 `ServiceKey`、`require` 和 `lookup` API 取代 Proxy 行为，保留基于 Realm 的寻址和重复注册拒绝。

## Fiber 生命周期

- `vendor/cordis/src/fiber.ts`：每次调用 `ctx.plugin()` 都会创建一个 Fiber，其状态包括 `PENDING`、`LOADING`、`ACTIVE`、`FAILED`、`UNLOADING` 和 `DISPOSED`。
- Fiber 使用满足 `inject` 的 Service Provider UID 构建激活 Epoch。缺少依赖时得到未激活 Epoch。Provider 身份改变会使依赖 Fiber 卸载后重新加载。
- 插件构造期间发布的 Service 通常要等 Provider Fiber 激活后才对依赖者可用。Provider 在拆卸期间保留自身访问权，直到依赖者清理完成。
- `effect()` 保证 Setup 可重入安全，收集一个或多个 Disposer，并保证只释放一次。同一个 Effect 内的 Cleanup 按注册顺序逆序执行；Fiber 卸载时并发启动顶层 Effect 的释放并等待完成。
- Python 决策：保留依赖 Epoch、激活失败回滚、分组逆序清理和顶层并发释放。初始实现针对单个 asyncio Event Loop。

## 事件

- `vendor/cordis/src/events.ts`：Listener 属于注册它的 Fiber。分发支持同步 `emit`、并发等待 `parallel`、按序等待并 Bail 的 `serial`、同步 Bail，以及 Around Middleware 形式的 `waterfall`。
- Waterfall Listener 的最后一个参数是 `next`。返回而不调用它会短路剩余 Listener 和内置 Terminal Operation。
- 分发可以携带 Receiver，并通过其 Context Filter 选择允许的 Listener。DSH Agent Scope 在此机制上构建路由 Carrier。
- Python 决策：第一阶段实现 `emit`、`parallel`、`serial` 和 `waterfall`。带 Scope Filter 的 Event Carrier 随 Agent Scope 在第二阶段实现。

## DSH Scope 与 Cordis Isolation

- `packages/core/scope/src/index.ts`：DSH Scope 使用不透明对象身份和 Parent Relation。祖先 Scope 中注册的 Listener 会收到后代事件；事件不会向下流动。
- `packages/core/scope/src/store.ts`：Registry 包含一个 Global Layer 和按需创建的 Exact-Scope Layer。读取顺序为 Global、远端祖先、最近 Scope，因此最近 Scope 中的同名项目获胜。
- 该机制不同于 Cordis Service Isolation。Isolation 选择 Service 实现；DSH Scope 为一个 Agent 选择 Registry Contribution 和 Event Listener。
- Python 决策：第一阶段只实现 Service Isolation。Agent Scope 和 Layered Registry 属于第二阶段。

## Agent Spine

- `docs/architecture.md` 和 `packages/core/agent-loop/src/agent.ts`：一个 Turn 包含零个或多个 Step。一个 Step 包含一次模型请求及其工具执行。
- Agent Loop 认领 Inbox Input，组装 Prompt Section 和 Tool Schema，运行 `agent/pre-step`，追加进入的 User Message，从 Session Log 派生模型历史，流式执行请求，记录 Raw Chunk 和组装后的 Assistant Message，执行 Tool Call，并在仍需模型工作时继续循环。
- `followup` 面向下一个 Turn 并唤醒 Driver。`steer` 面向下一个 Step 并唤醒它。`inject` 面向下一个 Step，但不唤醒它。
- 能力拦截应通过 Agent、Tool 和 LLM Event 实现，而不是修改 Agent Loop。

## Session Log

- `packages/core/session`：只追加的 Session Event Log 是权威数据。模型历史是其 Surface Projection，不是单独维护的 Message List。
- 模型可见输入必须能从日志重建。Raw Assistant Chunk 为重放和 UI 保真度保留；组装后的 Message 驱动历史。
- Surface Event 可以追加或替换一个闭区间，从而在不重写 Raw Log 的情况下实现 Compaction。
- Python 决策：第二阶段保留只追加权威日志和 Surface Projection。不得在没有 Durable Event 的情况下增加模型可见 Context。

## Tool 和 LLM Pipeline

- `packages/core/tools`：工具执行依次经过 `tools/pre-execute`、Monotonic Guard、`tools/execute`、`tools/post-execute`、Definition Finalization 和最终结果观察。Approval、Sandbox Policy、Timeout 和 Rewriting 仍由插件实现。
- `packages/llm/llm`：Adapter 注册 Provider Route。Streaming 暴露 Raw Chunk，并且只有一个终止 Finish。Provider 的运行故障转为终止 Stream Result；插件或 Consumer 故障可以抛出异常。
- Agent Loop 冻结并记录有效 Request Header，包括渲染后的 Prompt 和 Tool Schema。

## 浏览器插件运行时

- `.agents/notes/implemented/architecture/2026-07-23-client-plugin-loading-model.md`：Host 和 Browser 运行独立的 Cordis Tree，但使用相同的 Loader 治理模型。
- Client Plugin Package 声明 `dsh.client` 并导出 `./client`。Host 扫描已挂载的 Loader Entry，解析构建后的 `client.js`，计算哈希，提供文件，并发送浏览器 Boot Graph。
- 浏览器 Module System 将独立 Bundle 加载进 Lazy Module Table。Cordis Loader 负责激活、依赖等待、释放和刷新。HMR 使旧 Module 失效，释放旧 Fiber，移除其拥有的 Style，导入新 Revision 并挂载新 Fiber。
- 仅客户端 Package 仍有一个空的 Host `apply()`，使一个 Host Composition Row 负责它的 Roster Presence。双端 Package 在根入口和 `./client` 都有实际行为。仅后端 Package 没有 Client 声明。
- Python 决策：保留 Cordis TS。根插件 Manifest 取代 npm Metadata，成为跨语言身份；嵌套前端 Package Metadata 仅用于构建。

## Profile 和组合

- `packages/boot/app-boot` 和 `packages/bundle/base`：Profile 先组合有序的 Bundle Patch Layer，再应用 Profile、Home 和 Command Overlay。各 Row 并发挂载；激活由 Service 依赖决定，而不是 Row 顺序。
- 用户 Patch 变更可以重新组合整个 Tree。候选配置失败时保留上一个有效 Tree。
- Python 决策：插件组合和事务式配置替换在生命周期内核之后实现。

## 尚未解决的问题

- 面向多个已连接浏览器客户端的生产级安装/更新事务。
- Python Session 格式，以及第一个里程碑是否读取 TypeScript v0 Log。
- 第三方后端插件的 Worker Process 隔离粒度。
- Wire IDL 选择和 TypeScript/Python 代码生成工具链。
