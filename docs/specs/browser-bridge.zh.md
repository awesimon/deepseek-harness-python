# 浏览器桥接规范

状态：第四阶段规范

## 用途

Browser Bridge 将 Plugin Manager 的 Client Revision 交付给浏览器页面，使每个页面的 Cordis TS Fiber 与 Host Intent 保持一致，并在同一个插件的 Backend 和 Client Contribution 之间传递显式注册的 JSON RPC 和 Event。

## 范围

第四阶段包括 Transport-Independent Host State Machine、版本化 JSON Frame、精确 Bundle Retrieval、页面局部 Reconciliation、过期结果拒绝、请求取消、Package-Private RPC、TypeScript Client Runtime Adapter，以及由 Python 和 TypeScript Fixture 共享的内存 Conformance Test。

Authentication、面向 Internet 的部署、Binary Streaming、Offline Cache Persistence、Browser Extension Packaging 和多页面 Quorum 不属于本阶段。WebSocket/HTTP Adapter 可以暴露核心协议，但 Transport Code 不拥有插件状态。

## 权威状态和身份

Plugin Manager 继续保存 Installed Plugin 和 Published Client Revision 的进程级权威状态。Bridge 只将该状态投影到页面，不能自行 Enable、Disable 或 Update Plugin。

`PageId`、`OperationId`、`RpcCallId`、Plugin ID 和 Revision 都是不透明 Field。每个 Load、Unload、Result、RPC、Event 和 Cancellation Frame 都携带足够身份，以拒绝旧 Page Connection、Operation 或 Revision 发来的消息。

## 协议版本

每个 Frame 都是包含 `protocol: "1"` 和 Discriminant `type` 的 JSON Object。未知 Protocol Version 会用显式不兼容错误关闭逻辑连接。受支持版本中的未知 Frame Type 只使该 Frame 失败，不会改变页面状态。

规范 JSON Schema 和共享 Fixture 位于 `harness/protocol/`。Python 根据 Schema 校验每个 Frame，TypeScript 使用相同 Server Frame Fixture，避免手写协议类型独立漂移。

## 连接和协调

页面使用 `hello` 打开逻辑连接，其中携带 Page ID 和当前已加载的 `pluginId -> revision` Map。Host 返回一个 `reconcile` Command，其中包含完整 Desired Client Graph 和新的 Operation ID。

每个 Desired Entry 包含 Plugin ID、Revision、Bundle URL、SHA-256 Digest、可选 Protocol Schema URL 和 Activation Policy。Desired Graph 中不存在的 Entry 必须 Unload。Revision 不同的 Entry 先 Unload，再 Load Target。相同 Entry 保持挂载。

页面串行应用一个 Reconciliation Operation，并为每个发生变化的 Plugin 报告结果，最后报告 Operation Result。较新的 Reconcile 会取代旧 Operation。被取代 Operation ID 的结果只保留作 Diagnostic，绝不改变当前页面状态。

## Bundle 获取和执行

Host 只为精确且当前已发布的 Plugin ID 和 Revision 提供 Bundle Byte。Response 包含 Immutable Cache Header 和声明的 SHA-256 Digest。缺失或已取消发布的 Revision 返回 Not Found，不能重定向到 Current。

TypeScript Adapter 导入一个 Content-Addressed Module，解析其 Client Plugin Export，并在 Cordis TS Context 下挂载。`createPlugin(api)` Export 通过带 Revision 的 `ClientPluginApi` 获得精确 Reconciliation Identity；不需要 Bridge API 时仍可直接导出 Plugin。生成的 Fiber 拥有 Listener、Slot、Style、Timer、RPC Handler 和其他 Effect。Unload 先释放 Fiber，再从 Adapter Active Table 中移除 Module Revision。

本阶段信任 Browser Code Execution。User Approval 和 Generated-Code Guard 是发布或协调前额外叠加的产品 Policy。

## 页面局部状态

Host 为每个 Page 和 Plugin 记录 `absent`、`loading`、`active`、`waiting`、`failed` 或 `unloading`，以及精确 Revision 和最新 Diagnostic。`active` 表示该页面报告已经建立 Cordis TS Fiber，不能推断其他页面的状态。

Disconnect 会移除临时页面状态，并取消该页面未完成的 RPC Call。Reconnect 从新的 `hello` Inventory 和完整 Reconciliation 开始；Host 不假设旧 Connection 的 Fiber 仍然存在。

## Package-Private RPC

Backend Plugin 在 `BridgeRpcRegistry` 中以自身 Plugin ID 和 Active Revision 注册 Method。Browser Code 只能调用相同 Plugin ID 和 Revision。Call 包含 JSON 兼容 Argument、不透明 Call ID 和可选 Cancellation Token。

Backend Plugin 从 Plugin Manager 安装的隔离 `PLUGIN_RUNTIME_IDENTITY` Service 获取精确 Plugin ID 和 Revision。Browser Contribution 从 Reconciliation Entry 获取相同身份，并通过 `PluginChannel` 将其加入每个 RPC 和 Event Frame。

Host 在调用 Handler 前拒绝缺失、已停止或过期 Revision。Success 和 Structured Failure Response 携带同一个 Call ID。Cancellation 是 Best Effort：它将 Call 标记为已取消，取消 Active Async Task，并抑制之后的 Success Response。

RPC Handler 归 Effect 所有。Disable 或 Update Backend 会在要求旧 Client Revision Unload 前移除 Handler，因此过期页面不能访问新 Backend Code 或其他插件代码。

## Event

`BridgeEventRegistry` 公开显式命名的 JSON Event。Backend Emission 面向同一 Plugin ID 和 Revision 的所有 Active Page，或一个 Page ID。Client Emission 面向同一个 Active Backend Revision。Event Forwarding 不会反射任意 PyCordis 或 Cordis TS Event Name。

Event 在每个逻辑 Connection 内有序，但不持久化。模型可见后果必须由所属 Backend Plugin 写入 Session Log，之后才能进入模型请求。

## 失败处理

- 无效 Frame、Schema Failure、未知 Version 和 Identity Mismatch 不会造成部分状态变更。
- Bundle Hash Mismatch 使 Client Activation 失败并报告 Diagnostic。
- Client Import 或 Cordis Activation Failure 会释放该次尝试拥有的 Effect，并报告 `failed`。
- RPC Handler Exception 转换为 Structured Error；Cancellation 和 Disconnect 不会变成 Success。
- Host Disable 或 Update 会取代未完成 Load Operation，并拒绝之后到达的过期结果。
- Required Client Failure 影响 Plugin Manager 聚合状态；Optional Client Failure 使插件保持 Degraded。

## 验收标准

- Schema Test 校验每个 Frame，并拒绝未知 Field、Version 和 Discriminant。
- Host Test 验证初始 Reconciliation、相同 Revision No-Op、Update 先 Unload 后 Load、Disconnect Cleanup 和过期 Operation 拒绝。
- Bundle Test 验证精确 Revision Retrieval、Immutable Digest Header 和 Disable 后 Not Found。
- RPC Test 验证同 Plugin/Revision 授权、Structured Error、Cancellation、Handler Disposal 和过期 Call 拒绝。
- TypeScript Test 挂载并卸载真实 Cordis TS 测试插件，并验证 Effect Cleanup。
- 一个全栈无密钥场景启用插件、协调模拟页面、调用 Backend Method、更新两端 Revision、拒绝旧 Call，并在 Disable 时移除两端 Fiber。
- aiohttp Adapter 在不拥有 Plugin State 的前提下，验证 HTTP Artifact Delivery 以及 WebSocket Reconciliation、RPC、Cancellation、Event 和 Connection Replacement。
