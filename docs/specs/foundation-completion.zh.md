# Harness 基础完成规范

本文档定义 Python Harness 的最终基础里程碑，包括 Browser Bridge 收尾和后续插件开发使用的仓库布局。

## 范围

本里程碑包括直接 Python 包布局、Browser Bridge 规范 JSON Schema、Python Frame 校验、Backend/Client Event 转发、HTTP/WebSocket Adapter、Cordis TS Client Adapter，以及无密钥全栈生命周期场景。

现有 [Cordis Core](cordis-core.zh.md)、[Agent Spine](agent-spine.zh.md)、[Plugin Manager](plugin-manager.zh.md) 和 [Browser Bridge](browser-bridge.zh.md)规范继续负责各自子系统。本文档定义运行时基础完成所需的集成能力。

## 仓库布局

导入包位于 `python/harness/`。Setuptools 只从 `python/` Project 发现 `harness` 及其子包。测试、Pyright、Ruff、字节码编译、可编辑安装、源码发行包和 Wheel 使用同一位置，不依赖 `PYTHONPATH` 覆盖。

发行名保持 `deepseek-harness-python`，受支持的导入根保持 `harness`。仓库不包含 `src/` 包目录或 `deepseek_harness` 兼容包。

浏览器运行时 Adapter 位于 `frontend/`，它属于 Harness 基础设施，不定义第二个插件身份。每个逻辑插件可以独立包含可选的 `frontend/` 构建目录，其根 `plugin.toml` 仍是身份权威来源。

## 协议权威

版本 1 Browser Bridge Frame 由 `python/harness/protocol/` 下随包发布的 JSON Schema 定义。每个 Frame 都包含 `protocol` 和 `type`，拒绝未知字段，并且只使用 JSON 兼容值。Python 解码在构造不可变协议值前完成校验，编码后也校验输出对象。TypeScript 协议类型和测试根据相同字段定义和共享 Fixture 做机械校验。

支持的 Frame 包括 Hello、完整图 Reconciliation、逐插件结果、Operation Completion、RPC Call/Result/Cancel 和显式命名 Event。不支持的版本终止逻辑连接。无效 Frame 失败时不得改变 Page 或 Plugin 状态。

## Client 发布与协调

Client Publication 为一个 Plugin ID 和 Revision 保留精确 Bundle 字节、SHA-256 Digest、可选插件协议 Schema 字节和 Activation Policy。发布或移除 Client Revision 时通知已连接 Transport，并创建新的完整图 Reconciliation Operation。

HTTP Adapter 只提供当前精确 Revision，并返回不可变缓存 Header 和 Digest。它在相同 Revision 下提供可选插件协议 Schema。缺失、已停止或过期 Revision 返回 Not Found。

WebSocket Adapter 在其他流量前只接受一个 Hello Frame，负责替换重复 Page ID 的 Connection，并按 Connection 串行发送 Frame。它通过 Transport-Independent `BrowserBridge` 路由 Reconciliation Result、RPC、Cancellation 和 Event。Disconnect 移除页面状态并取消其 Call。

## Event 转发

Backend Plugin 按 Plugin ID、Revision 和 Event Name 注册 Inbound Event Handler。注册返回幂等 Disposer，并应归 PyCordis Effect 所有。Client Event 只能到达匹配的 Active Page Revision。

Plugin Manager 为每次 Backend Activation 提供隔离的 `PLUGIN_RUNTIME_IDENTITY` Service。Bridge Registration 使用其中由 Manager 管理的 Plugin ID 和 Revision；身份不是 Plugin Config Field。

Backend Emission 面向所有匹配的 Active Page，或指定 Page ID。Page Sink 归 Connection 所有，并在 Disconnect 时移除。Event Forwarding 不反射任意 PyCordis 或 Cordis TS Event Name，也不持久化。

## Cordis TS Client Adapter

TypeScript Adapter 为每个 Active Plugin ID 和 Revision 拥有一个 Cordis TS Child Fiber。Reconciliation 保留相同 Revision，先卸载被移除或改变的 Revision，再加载替代版本；导入前校验 Bundle SHA-256，并报告每个改变的 Plugin 和 Operation Completion。

Client Module 导出 Cordis Plugin 或 `createPlugin(api)`。Factory 接收绑定到 Reconciliation Entry Plugin ID 和 Revision 的 `ClientPluginApi`，其 RPC 和 Event Method 始终携带该身份。Hash、Import、Export 或 Fiber Activation 失败时报告 `failed` 并移除该次尝试拥有的资源。Unload 先释放 Fiber，再删除 Active Revision。

## 全栈生命周期

无密钥场景安装并启用一个全栈 Plugin，建立 Page Connection，激活 Client Fiber，调用 Effect-Owned Backend RPC Method，转发 Event，更新两端 Contribution，拒绝过期 Revision，然后 Disable Plugin。完成时 PyCordis 和 Cordis TS Contribution 都必须消失。

## 失败处理

- Schema Error、不支持的 Version、未知 Frame Type 和身份不匹配在状态变化前失败。
- Bundle 或插件协议 Schema 请求不会从过期 Revision 重定向到当前 Revision。
- 被替换的 WebSocket Connection 不能删除或修改替代它的新 Connection。
- RPC Cancellation 和 Disconnect 抑制成功结果，并返回或发送结构化 Cancellation Result。
- Client Activation Failure 只影响对应页面，并保留精确 Operation 和 Revision 的 Diagnostic。

## 验收标准

- 仓库从根目录命令直接导入、类型检查、测试、构建和安装 `python/` Project。
- Python Schema Test 接受所有受支持 Frame，并拒绝未知字段、Version、Discriminant 和无效 Success/Error 组合。
- Event Test 验证同 Plugin/Revision 授权、定向与广播发送、顺序、Disposal 和 Disconnect Cleanup。
- HTTP/WebSocket Test 验证精确 Artifact Delivery、自动 Reconciliation、Frame Routing、Connection Replacement 和 Cancellation。
- TypeScript Test 使用真实 Cordis Plugin 验证 Mount、Preserve、Replace、Unload、Effect Cleanup 和 Hash Rejection。
- 无密钥全栈场景验证双运行时的 Enable、Reconciliation、RPC、Event、Update、Stale Rejection 和 Disable。
- `docs/progress.md` 不包含未完成的 Browser Bridge 实现项。

## 不在范围内

面向互联网的 Authentication、Authorization Policy、TLS Termination、Package Download、Signature、Dependency Installation、持久化 Inventory、持久化 Session Storage，以及不受信任 Backend Plugin 的 Process Isolation 仍属于部署或产品能力。这里提供的 HTTP/WebSocket Adapter 是应用构件，远程暴露前必须置于宿主应用的安全策略之后。
