# 插件开发 SDK 规范

状态：Phase 6 规范性文档

## 目的

插件开发 SDK 在 PyCordis 和 Cordis TS 之上为仅后端、仅前端和全栈插件提供稳定 API，同时不暴露 Manager 或 Browser Bridge 的管理细节。插件仍是由生命周期拥有的普通贡献，因此开发者添加行为时无需实现 Revision 寻址、注册清理或线路帧构造。

## 范围

Phase 6 包括 Python 后端开发 API、TypeScript 前端开发 API、绑定 Revision 的 RPC 和 Event 辅助 API、不可变协议描述符、聚焦的内存测试 harness，以及三种贡献形式的可执行示例。

SDK 是现有 PyCordis `PluginSpec`、Cordis TS 插件、`PLUGIN_RUNTIME_IDENTITY` 和 Browser Bridge Service 之上的轻量开发层。它不创建第二套插件运行时、生命周期、注册表或身份来源。

## 身份权威

根 `plugin.toml` 的 `[plugin]` 表仍是 Plugin ID、语义版本和 runtime API 的唯一权威。Plugin Manager 计算内容 Revision 并向后端 Fiber 注入 `PLUGIN_RUNTIME_IDENTITY`；浏览器 reconciliation 向 `ClientPluginApi` 注入相同的 Plugin ID 和 Revision。

生产 SDK 的构造函数、装饰器、工厂、协议描述符和配置字段均不接受 Plugin ID 或 Revision。SDK context 仅将两个值作为只读信息供诊断和应用数据使用。插件开发者无法通过 SDK 使用其他身份发布、注册、调用、发送或监听。

SDK 可以接受诊断插件名称，因为 `PluginSpec.name` 用于在错误中标识 Fiber；该名称不具有包、授权或线路语义，并且默认为 `plugin-backend`。

## Python 后端开发 API

`harness.sdk` 模块导出 `define_backend_plugin`、`define_bridge_backend_plugin`、`BackendPluginContext`、`BridgeBackendPluginContext`、`BackendPluginChannel`、`RpcMethod`、`ClientEvent`、`ServerEvent`、`rpc_method`、`client_event` 和 `server_event`。

```python
plugin = define_backend_plugin(setup, requires=(MY_SERVICE,))
plugin = define_bridge_backend_plugin(setup, requires=(MY_SERVICE,))
```

两个工厂均返回 `PluginSpec[None]`，将 `PLUGIN_RUNTIME_IDENTITY` 加入声明的依赖，拒绝重复的开发者依赖，并向 `setup` 传递只读 context。`define_bridge_backend_plugin` 还声明 `BROWSER_BRIDGE`、`BRIDGE_RPC_REGISTRY` 和 `BRIDGE_EVENT_REGISTRY`，并提供绑定 Revision 的 `BackendPluginChannel`。`define_backend_plugin` 不依赖 Browser Bridge Service，因此仅后端插件仍可用于没有浏览器传输的 Manager 组合。

`BackendPluginContext` 公开 `cordis: Context`、`plugin_id` 和 `revision`。开发者通过 `cordis` 解析自己显式声明的应用 Service 并创建自定义 Effect；SDK 不增加反射式 Service 查找或隐式依赖。

`BridgeBackendPluginContext` 在上述 API 基础上增加 `channel`。`BackendPluginChannel.register_rpc(method, handler)` 使用注入的身份注册方法，`on_client_event(event, handler)` 注册从前端到后端的 Event handler，`emit_client_event(event, payload, *, page_id=None)` 发送从后端到前端的 Event 并返回投递数量。注册方法是异步的，因为它们会建立 PyCordis Effect；开发者不会获取或管理底层注册表 disposer。

后端 RPC handler 接收不可变、兼容 JSON 的参数映射，并直接或通过 awaitable 返回兼容 JSON 的值。前端 Event handler 接收来源 Page ID 和兼容 JSON 的 payload。SDK 在投递前使用 Agent JSON value 规则验证出站值，不会强制转换不受支持的 Python 对象。

`setup` 可以同步或异步执行，也可以返回 `PluginSpec` 接受的 cleanup 形式。返回的 `PluginSpec` 是 `plugin.toml` 引用的后端入口；插件代码不会调用 Manager 来安装或启用自身。

## TypeScript 前端开发 API

`@deepseek-harness/browser-bridge-client` 包在低层 Bridge client API 之外还导出 `defineClientPlugin`、`ClientPluginContext`、`RpcMethod`、`ClientEvent`、`ServerEvent`、`rpcMethod`、`clientEvent` 和 `serverEvent`。

```ts
export const createPlugin = defineClientPlugin(async (ctx) => {
  const value = await ctx.call(describe, { verbose: false })
  ctx.on(changed, (payload) => render(payload))
  return () => removeRenderedState(value)
})
```

`defineClientPlugin(setup)` 返回由 Browser Bridge adapter 使用的 `createPlugin(api)` 工厂。生成的 Cordis TS 插件根据 adapter 提供的 `ClientPluginApi` 和当前 Cordis Context 创建 `ClientPluginContext`。在没有绑定 Revision 的 API 时调用会导致 activation 失败。

`ClientPluginContext` 公开当前 Cordis Context 以及只读的 `pluginId` 和 `revision`。`call(method, args, signal?)` 调用匹配的后端 RPC，`emit(event, payload)` 发送前端 Event，`on(event, handler)` 注册后端 Event listener，并使其 disposer 归前端 Fiber 所有。`effect(setup)` 将自定义 cleanup 所有权委托给同一个 Cordis Fiber。Setup 及其返回的 cleanup 遵循 Cordis TS 插件生命周期语义。

前端 API 不公开原始 Page、Operation 或 Call 标识符。开发者可以为 RPC cancellation 传递 `AbortSignal`，但不能寻址其他插件或 Revision。

## 全栈协议辅助 API

`RpcMethod[Arguments, Result]`、`ClientEvent[Payload]` 和 `ServerEvent[Payload]` 是包含非空线路名称和方向的不可变描述符。Python 通过 `rpc_method`、`client_event` 和 `server_event` 构造描述符；TypeScript 使用 `rpcMethod`、`clientEvent` 和 `serverEvent`。后端 Channel 接受 RPC Method 和 Client-Origin Event，前端 Context 根据操作接受 RPC Method 和两个方向的 Event。静态类型会拒绝在错误方向使用描述符。

描述符不携带 Plugin ID、Revision、注册表、连接或可变 handler 状态。每项操作都会将描述符名称与 SDK context 已绑定的身份组合。同一方向的重复名称会使 activation 失败，而不会替换现有注册。

`plugin.toml` 中可选的 `[protocol].schema` artifact 仍是跨运行时数据规范，并参与 Manager Revision 计算。Phase 6 描述符提供方向安全的名称和兼容 JSON 的泛型类型；它们不生成语言 binding，也不执行插件专用的 JSON Schema 验证。全栈示例会对齐 Python 描述符、TypeScript 描述符和 Schema 名称，并通过共享 fixture 执行这些描述符。

## 版本与兼容策略

`plugin.runtime_api` 选择 Host 的开发和加载 API 主版本。Phase 6 SDK 支持 `runtime_api = "1"`；不受支持的值会在导入后端代码或发布前端 Bundle 之前使 manifest validation 失败。Python distribution 和 TypeScript 包使用语义包版本，模板固定兼容的 SDK 版本范围，而不是复制 SDK 实现代码。

新增 SDK API 和可选协议字段可以在相同 runtime API 下发布。移除 API、更改生命周期所有权、更改身份授权或更改现有线路语义时，必须按适用情况使用新的 runtime API 或 Browser Bridge protocol version。不受支持的旧格式会显式失败；SDK 不会猜测、降级或安装兼容别名。

插件语义版本是开发者控制的元数据，而 Revision 是 Manager 控制的内容摘要。无论包版本或 SDK 版本如何，修改后端、前端、manifest 或声明的协议字节都会改变 Revision。

## Effect 与生命周期所有权

每项 SDK 注册均由当前后端或前端 Fiber 创建并由该 Fiber 处置。Disable 或 update 后，旧后端 Revision 可以继续服务之前，后端 RPC 和 Event 注册会消失。导入的模块释放之前，前端 Event listener 和自定义 Effect 会消失。

Setup 失败会处置该次 activation 尝试建立的所有 Effect。Cleanup 按所属 Cordis 运行时确定的逆序执行，依照该运行时的 cleanup 策略继续，并通过 Fiber 诊断保持可见。SDK 辅助 API 不保留隐藏的全局注册表，也不会在 Fiber dispose 后继续持有开发者 callback。

应用 callback 仅可在当前生命周期内捕获其 SDK context。Dispose 后发起的调用通过底层 inactive context、stale Revision 或 disposed connection 行为失败；SDK 不会重新连接或重新确定目标。

## 测试支持

`harness.sdk.testing` 提供 `BackendPluginHarness` 和 `FullStackPluginHarness`。后端 harness 使用合成的 Manager 所有身份和声明的测试 Service 挂载一个开发者入口，并执行确定性 dispose。全栈 harness 增加内存 Bridge Service，可调用已注册 RPC、发送前端 Event、捕获发出的后端 Event，并能断言 dispose 后没有剩余注册。

TypeScript 包公开仅供测试使用的 `createClientPluginHarness`，它使用假的、绑定 Revision 的 `ClientPluginApi` 挂载真实 Cordis 插件，记录 RPC 和 Event 流量，分发后端 Event，并 dispose Fiber。测试 harness 可以接受显式 fixture 身份，因为它们代替 Manager 和 reconciliation 基础设施；生产开发 API 不接受该身份。

测试支持执行 Host 使用的相同公开工厂和生命周期路径。它不导入私有注册表、不修改插件返回的 `PluginSpec`，也不声称覆盖浏览器传输。真实 HTTP、WebSocket 和 Chromium 行为仍由 Host 全栈场景覆盖。

## 失败处理

- 空描述符名称、重复依赖、重复注册、缺少注入身份和不可用的 required Service 会使 activation 失败，且不留下注册。
- 非 JSON 的 RPC 参数、结果和 Event payload 会在 SDK 可控的最早发送或返回位置失败；这些值绝不会以字符串化作为 fallback。
- 后端 handler 异常使用 Browser Bridge 的结构化 RPC 错误路径，cancellation 绝不会成为成功结果。
- 缺失或 stale 的后端 Revision 通过现有 Bridge 授权拒绝前端 RPC 和 Event。
- 前端 setup、listener 或 cleanup 失败保留为 Cordis Fiber 诊断，且不会导致 SDK 挂载未跟踪的替代项。
- 测试 harness teardown 会报告 cleanup 失败，同时仍尝试 dispose 每个所属运行时对象。

## 验收标准

- 仅后端示例使用 `define_backend_plugin`，解析一个声明的 PyCordis Service，并在 disable 时移除其 Effect，且不依赖 Browser Bridge Service。
- 仅前端示例使用 `defineClientPlugin`，在真实 Cordis TS Context 中挂载，并在 unload 时移除 Event listener 和自定义 Effect。
- 全栈示例使用描述符和两端 SDK context 完成 RPC 与双向 Event，且插件代码不传递 Plugin ID 或 Revision。
- 测试证明每个后端注册均使用 Manager 注入的身份，每个前端操作均使用浏览器 reconciliation 身份。
- 测试拒绝生产工厂的 identity 或 Revision 参数，并证明 stale 操作无法通过描述符重新确定目标。
- Python 和 TypeScript 测试 harness 覆盖 setup 成功、setup rollback、cancellation、handler failure、JSON rejection 和幂等 dispose。
- 类型检查通过代表性示例证明 RPC 参数/结果和 Event 方向类型。
- 现有原始 `PluginSpec` 和 `createPlugin(api)` 入口继续工作，因为 SDK 会编译为这些既有运行时形式。
- README 和进度文档将 SDK 标识为受支持的开发路径，并保留受信任本地代码的限制。

## 排除项

项目模板和 scaffolding 命令单独指定。协议代码生成、插件专用运行时 Schema 验证、UI 组件、依赖安装、远程分发、签名、进程隔离、权限、持久状态、Revision 之间的状态迁移和多页面 activation 聚合不属于 Phase 6。
