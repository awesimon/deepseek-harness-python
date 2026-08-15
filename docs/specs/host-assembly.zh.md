# Host Assembly 规范

状态：第五阶段规范

## 用途

Host Assembly 将生命周期内核、Agent Spine、Dynamic Plugin Manager 和 Browser Bridge 组成一个可运行应用。它负责进程启动和关闭，运行时行为仍由 Plugin 提供。

## 范围

第五阶段包括 PyCordis Browser Bridge Plugin、Typed Host Config、Plugin Catalog Discovery 和 Activation、aiohttp Listener、可选 Browser Runtime Delivery、命令行入口、确定性 Teardown，以及无密钥真实浏览器生命周期场景。

Host 不增加 Agent Behavior、Plugin-Specific RPC Method 或隐藏生命周期状态。Agent Spine、Plugin Manager、Client Artifact Registry 和 Browser Bridge 的 Provider Fiber 进入 Active 后，Host 从 PyCordis Service 获取它们。

## Core Composition

一个 `HarnessHost` 拥有一个 `Cordis` Runtime。启动时挂载 Agent Spine、Plugin Manager 和 Browser Bridge Provider Plugin。Browser Bridge Provider 将 Client Artifact Registry 声明为 Service Dependency，并向 Backend Plugin 提供 Bridge、RPC Registry 和 Event Registry Service。

Core Provider 未进入 `ACTIVE` 时 Host 会拒绝启动。Host 不在 Provider Plugin 之外构造第二个 Manager 或 Bridge。

## 配置

`HarnessHostConfig` 包含非空 Session ID、Bind Host、Port、零个或多个 Plugin Catalog Directory，以及可选 Browser Runtime Bundle Path。Listener 启动前解析所有 Path。Port 可以为 `0`，供测试请求操作系统分配端口。

每个 Plugin Catalog Directory 的直接子目录是带根 `plugin.toml` 的 Plugin Directory。启动时按稳定顺序发现 Candidate，拒绝所有 Discovery Diagnostic，安装每个唯一 Plugin ID 并 Enable。Required Contribution Failure 中止启动。Optional Contribution Failure 可以产生 `DEGRADED`，但不中止 Host。

## HTTP Application

Host 暴露 Browser Bridge Artifact 和 WebSocket Route，以及无密钥 `/health` Endpoint。配置 Browser Runtime Bundle 后，`/` 返回固定 Bootstrap Document，`/browser.js` 提供该精确文件，不支持目录遍历或 Fallback。Bootstrap 在 WebSocket 打开后创建一个 Cordis TS Root Context 和一个 `BridgeConnection`。

启动返回前 Listener 已完成 Bind。`base_url` 报告包含已分配 Port 的有效地址。Host Config 是可信本地输入；面向 Internet 的 Policy 不属于本阶段。

## 生命周期

`start()` 只能调用一次；关闭后再次调用也会失败。任何启动失败都会关闭已部分创建的 Listener，Disable 所有已进入 Serving State 的 Installed Plugin，并在返回原始错误前关闭 PyCordis。

`close()` 幂等，并等待并发调用者。它先停止接受 HTTP/WebSocket Traffic，按稳定逆序 Disable Installed Plugin，然后关闭 PyCordis。返回时 Client Publication、Backend Effect、Bridge Registration、Agent Service 和 Child Fiber 均已消失。单项 Cleanup 失败后继续尝试其他 Cleanup，最终报告 Exception Group。

Async Context Manager 在进入时调用 `start()`，退出时调用 `close()`。命令行入口等待进程终止 Signal，然后使用相同关闭路径。

## Browser Lifecycle Evidence

无密钥浏览器场景构建 Browser Runtime，在 Loopback Ephemeral Port 启动 Host，并 Enable 一个 Full-Stack Plugin。Chromium 加载 Bootstrap Document，导入 Content-Addressed Client Bundle，挂载真实 Cordis TS Fiber，调用 Effect-Owned Python RPC Method，交换 Event，并渲染可观察 Revision State。

该场景更新 Backend 和 Client Byte，在替代版本 Active 前观察旧 Client Effect Cleanup，拒绝过期 Revision，然后 Disable Plugin。页面观察到移除；Host 关闭后不保留 Backend Fiber 或 Client Publication。

## 失败处理

- 缺失 Catalog、无效 Browser Runtime Path、Discovery Diagnostic、重复 Plugin ID 和 Required Activation Failure 在 Host 返回 URL 前拒绝启动。
- Listener Bind Failure 会关闭 PyCordis Composition 和已安装 Plugin Contribution。
- Browser Runtime Delivery 只提供一个配置的 Regular File，绝不解析由 Request 控制的 Path。
- WebSocket 和 Client Activation Failure 保留第四阶段定义的 Browser Bridge Diagnostic。
- Shutdown 在尝试所有 Owned Cleanup 后聚合 Cleanup Failure。

## 验收标准

- Unit Test 验证 Core Service Composition、稳定 Catalog Activation、Assigned Port Reporting、Startup Rollback、Idempotent Close 和 Browser Runtime Path Validation。
- CLI 和 `python -m harness` 共用同一 Parser 和 Lifecycle Implementation。
- Backend Plugin 通过官方 PyCordis Service Key 注册 Bridge RPC 和 Event。
- 真实 Chromium 场景验证 Browser Bundle Delivery、Cordis TS Activation、RPC、双向 Event、Dual-Contribution Update、旧 Effect Cleanup、Stale Rejection、Disable 和 Host Teardown。
- Ruff、严格 Pyright、Python Test、TypeScript Type Check/Test/Build、Source/Wheel Build 和隔离 Wheel Import 全部通过。
- `docs/progress.md` 记录第五阶段 Evidence 和下一个具体 Milestone。

## 不在范围内

Persistent Inventory 和 Session、Process Isolation、Remote Package Installation、Dependency Resolution、Signature、Authentication、Authorization、TLS Termination、跨 Page 的 Client Activation Aggregation，以及 Plugin Authoring Template 仍属于独立能力。Browser Runtime Bundle 从 `frontend/` 构建；将它打包进可分发应用不属于本阶段。
