# 插件控制面与本地分发规范

[English](plugin-control-plane.md) | 中文

状态：第十阶段规范

## 用途

插件控制面把动态 Plugin Manager 转换为可运维的本地开发服务。它通过 Loopback HTTP API 暴露 Inventory 和 Lifecycle Command，提供基于该 API 的 CLI（命令行界面），监视可信 Catalog 以执行 Hot Update，并使 TypeScript Browser SDK 可以在 Harness Workspace 外解析。

Manager 继续作为 Plugin Identity、Revision 构建、Contribution Activation、Rollback 和 Aggregate State 的权威。控制面校验 Operator Intent，将它与 Filesystem Intent 串行化，并返回从 Manager 派生的 Snapshot；它不会实现第二套 Lifecycle。

## 范围

第十阶段包括可选启用且带版本的 Control API、Inventory 和 Readiness Snapshot、Install、Enable、Disable、Update、Rollback、Uninstall、Optimistic Mutation Precondition、Operation Diagnostic、不会重试的 CLI Client、带 Debounce 的可配置 Catalog Watching，以及供生成的 Client Project 使用且可在本地分发的 Browser SDK Tarball。

本阶段操作已经由[动态 Plugin Manager](plugin-manager.zh.md)接受的可信本地 Plugin Root。它不下载 Plugin Code 或 Dependency。Backend Code 在现有限制下仍然可信并在进程内运行。

## 信任和暴露范围

Control API 没有 Authentication，因此只有 Effective Host Listener 仅绑定 Loopback 时才能使用。在 Wildcard、Public 或 Loopback/Non-Loopback 混合地址上启用它时，Host Config 会在 Bind 前失败。Adapter 不信任 Forwarded-Address Header，不启用 CORS，也不从 `Host` 或 Proxy Header 推导 Authorization。

带有 Browser `Origin` Header 的 Request 必须使用 Control Listener 的精确 Origin。不带 `Origin` 的 Request 可供 CLI 和 Local Automation 使用。Mutation Request 必须使用 `application/json`；Form Body 和 Text Body 会被拒绝，因而无关 Web Page 无法提交简单的 Cross-Origin Form Mutation。

通过 HTTP 或 Filesystem Discovery 提供的 Plugin Root 必须解析为 Configured Trusted Catalog Directory 的 Immediate Child。Symlink 和 Normalized Path 不能逃逸这些 Catalog。Loopback Placement 限制了暴露范围，但它并不是针对同一 User 运行的其他 Process 的 Security Boundary。

## 控制资源和快照

`GET` Response 是从一次串行化 Control-Plane Observation 捕获的 Immutable JSON Snapshot。Collection Result 按 Plugin ID 排序。每个 Plugin Entry 包含 Plugin ID、Semantic Version、Current Revision、Retained Previous Revision、Source Root、Desired Enablement、Aggregate State、Process-Local Contribution State、Published Client Revision、完整的 Client Activation Readiness、Current Diagnostic，以及不透明且单调递增的 `mutationVersion`。

Client Readiness 保留[多页面 Client Activation 聚合规范](multi-page-activation.zh.md)定义的 Field，包括 Quorum、Aggregate State、Page Count、Current Page Diagnostic、Target Revision 和 Operation Identity。Control Snapshot 绝不会把 `unobserved`、`reconciling`、`degraded` 或 `failed` 折叠成通用 Enabled Flag。

Collection 还报告不透明的 `inventoryVersion` 和 Watcher Status。Watcher Status 包含 Watching 是否启用、Configured Catalog 和 Policy、Pending Root 数量、存在时正在 Dispatch 的 Root，以及最新 Structured Watcher Diagnostic。Inventory Version 和 Mutation Version 是 Process-Local Concurrency Token，不是 Durable Identifier 或 Plugin Revision。

每条 Diagnostic 都包含 Stable Code、Concise Message、已知时受影响的 Plugin ID 或 Path、可用时的 Operation Identity，以及可用时的 Current 或 Candidate Revision。Python Exception Class 和 Traceback 属于 Log，不属于 Protocol Field。

## HTTP API

API Prefix 是 `/api/control/v1`。Unknown Field 和 Unsupported Media Type 在任何 Manager Operation 前失败。

| 方法 | 路径 | 操作 |
|---|---|---|
| `GET` | `/plugins` | 返回有序 Inventory 和 Watcher Status。 |
| `GET` | `/plugins/{pluginId}` | 返回一个 Current Plugin Snapshot。 |
| `POST` | `/plugins/install` | 将一个经过校验的 Catalog Root 安装为 Disabled。 |
| `POST` | `/plugins/{pluginId}/enable` | Enable Current Revision。 |
| `POST` | `/plugins/{pluginId}/disable` | Disable 所有 Current Contribution。 |
| `POST` | `/plugins/{pluginId}/update` | 从 Installed Root 构建并应用 Candidate。 |
| `POST` | `/plugins/{pluginId}/rollback` | 激活 Retained Previous Revision。 |
| `POST` | `/plugins/{pluginId}/uninstall` | 移除一个 Disabled Inventory Record。 |

Install 接受 Catalog 内的 `pluginRoot`，并要求 `expectedAbsent: true`。Update 只读取已经安装的 Root；Request 不能把 Installed Plugin ID 重定向到另一个 Directory。Rollback 要求 `targetRevision` 等于 Snapshot 中的 Retained Previous Revision。Uninstall 保留 Manager Rule，即 Plugin 必须已经 Disabled。

每个 Accepted Mutation 返回 Operation ID、取值为 `succeeded` 或 `failed` 的 Outcome，以及在释放串行化 Slot 前捕获的 Post-Operation Snapshot。Uninstall 返回 Tombstone，其中包含被移除的 Plugin ID、Revision 和 Final Mutation Version。Contribution Activation 或 Cleanup Failure 属于 Outcome 为 `failed` 的 Accepted Operation，并携带 Manager Diagnostic；Malformed、Stale 或 Unauthorized Intent 属于 Rejected Request。

HTTP Status 区分 Transport 和 Command Admission：成功的 Observation 和 Accepted Operation 使用 `2xx`；Invalid Input 使用 `400`；Unknown Inventory 使用 `404`；Stale 或 State-Conflicting Intent 使用 `409`；Unsupported Content 使用 `415`；Closing Service 使用 `503`；Unexpected Adapter Failure 使用 `500`。Error Response 使用同一种 Stable JSON Envelope，并且绝不包含 Traceback。

## 变更前置条件

除 Install 外，每个 Mutation 都要求提供来自 Prior Snapshot 的 `expectedRevision` 和 `expectedMutationVersion`。控制面会在 Serialized Operation 内、调用 Manager 前立即比较这两个值。Mismatch 返回带 Current Snapshot 的 `409`，并且不会 Import Code、Publish Bundle、Disable Contribution 或增加 Version。

每次 Accepted Operation 改变 Inventory、Desired Enablement、Current 或 Previous Revision 或 Serving Contribution 后，Mutation Version 都会改变。Browser Membership 和 Readiness Report 不会改变它。精确的 Idempotent Enable 或 Disable 如果没有产生 Manager Change，则成功且不增加该值。

Revision Comparison 防止 Stale Client 指向不同 Code；Monotonic Mutation Version 还会检测返回同一个 Content Digest 的 Disable/Enable 和 Rollback/Update Cycle。API 绝不会猜测 New Precondition，也绝不会自动重试 Conflict。

## 串行化操作

一个 Host 拥有一个 FIFO Mutation Coordinator，由 HTTP Command、Startup Catalog Action 和 Filesystem Watcher Action 共享。它同一时间最多向 Manager 提交一个 Mutation，包括不同 Plugin ID 的 Operation，这与 Manager 的 Process-Wide Serialization 一致。Read Response 只能观察 Mutation 之前或之后的 State，绝不会观察 Partially Updated Record。

每个 Queued Item 都携带其 Source、Operation ID、Target Root 或 Plugin ID，以及捕获的 Precondition。Precondition 在 Dispatch 时重新检查，而不只是在入队时检查。变为 Stale 的 Watcher Item 会被丢弃，并根据新的 Filesystem 和 Manager Snapshot 重新调度；HTTP Item 返回 Conflict，并且绝不会被 Retarget。

Manager Dispatch 开始后，HTTP Client Disconnect 不会取消 Mutation，因为 Contribution Teardown 或 Activation 无法安全地中途放弃。Operation 会完成，Diagnostic 继续通过 Inventory 可观察，而 Retry 仍然要求 Fresh Snapshot。在 Dispatch 前取消的 Request 会从队列移除，且不会调用 Manager。

Plugin Callback 不能为自己的 Record 递归调用 Coordinator。此类 Attempt 会显式失败，而不是等待 Active Operation。

## 文件系统监视器

Watching 是可选启用的 Host Config。每个 Enabled Watcher 声明 Catalog Root、Positive Debounce Duration、取值为 `ignore`、`install_disabled` 或 `install_enabled` 的 Create Policy，以及取值为 `ignore`、`disable` 或 `uninstall` 的 Delete Policy。Host 会拒绝 Trusted Catalog Set 之外的 Watched Catalog，以及不支持的 Timing 或 Policy Value。

Watcher 观察 Root `plugin.toml` 和每个 Manifest-Declared Backend、Client 及 Protocol Artifact。Event 按 Plugin Root 合并。Debounce Interval 之后，一次 Rescan 构建一个 Immutable Candidate，并通过 Mutation Coordinator 提交 Intent。在 Build 或 Activation 期间到达的 Change 只会安排一次针对 Latest Filesystem State 的 Later Rescan。

对于 Installed Root，不同且有效的 Candidate 会调用 Update，并保留 Record 的 Desired Enablement。同一个 Revision 属于 No-Op。Invalid 或 Incomplete Candidate 会保留 Current Revision 继续 Serving，记录 Watcher Diagnostic，并等待之后的 Filesystem Event 或显式 API Command，而不会进入 Failure Polling Loop。

Create Policy 控制 Newly Valid Immediate Child 是被忽略、Installed Disabled，还是 Installed And Enabled。Delete Policy 控制一个 Root 在整个 Debounce Interval 内保持 Absent 后，是被忽略、Disabled，还是 Disabled And Uninstalled。只有 Disable Cleanup 没有 Diagnostic 时才执行 Automatic Uninstall；否则保留可观察的 Disabled Record。Delete 后 Recreation 会根据 Fresh State 评估，并且绝不会复用 Stale Candidate。

Watcher 和 HTTP Operation 使用完全相同的 Validation、Precondition、Rollback、Publication、Browser Bridge Reconciliation 和 Snapshot Path。Watcher 不调用 Package Manager，不重新构建 Frontend Source，不推断 Undeclared Artifact，也不编辑 Plugin File。

## CLI

`deepseek-harness-python plugin` 是 Control API 的 HTTP Client，并提供 `list`、`show`、`install`、`enable`、`disable`、`update`、`rollback` 和 `uninstall`。它接受显式 Control URL，只默认使用一个有文档说明的 Loopback URL，并且绝不会 Import 或 Construct Plugin Manager。

执行 Mutation 前，CLI 读取相关 Snapshot 并发送其精确 Precondition，除非 Caller 提供显式 Revision 和 Mutation Version。它不会重试 `409`；它会输出 Current Conflict Snapshot，由 Person 或 Script 重新做出决定。Rollback 还会发送选中的 Retained Revision。

Successful Command 向 Standard Output 写入一个 Stable JSON Document。Rejected Request、Transport Failure 和 Outcome 为 `failed` 的 Accepted Operation 返回 Nonzero，并向 Standard Error 写入 Concise Diagnostic；Server 提供 Snapshot 时，JSON Snapshot 仍可从 Standard Output 获取。后续可以添加 Human-Oriented Formatting，而不改变 JSON Mode。

## 本地 TypeScript SDK 分发

Browser Authoring Package 仍然是 TypeScript Cordis Runtime Dependency，并以带版本且兼容 npm 的 Tarball 形式供本地开发分发。Frontend Library Build 发出其 Public Runtime 和 Type Export，之后 Packaging Step 创建 Tarball，并记录 Package Version 和 SHA-256 Digest。Python Wheel 和 Source Distribution 都将该精确 Artifact 作为 Package Data；Artifact Stale 或缺失时，Distribution Build 失败。

`deepseek-harness-plugin sdk export` 将 Bundled Tarball 复制到显式 Destination，不下载或安装任何内容。当 Existing File 具有相同 Digest 时，Export 是 Idempotent；如果 Existing File 不同，则拒绝覆盖，除非 Caller 选择另一个 Destination。

Client-Only 和 Full-Stack Scaffold 将 Tarball Vendor 到 `frontend/vendor/` 下，并使用 Relative `file:` Dependency 声明 Browser SDK。其 Lockfile 记录 Local Tarball 及其 Dependency Graph，因此 `pnpm install --frozen-lockfile` 不再依赖 Workspace Symlink 或不存在的 Public Browser SDK Version。Backend-Only Scaffold 不包含 Browser Artifact。

Bundled Package Version 必须匹配 Scaffolder 使用的 SDK Compatibility Constant。对于同一个 Harness Distribution，Generated Project 保持 Deterministic。Cordis、TypeScript、Bundler 和 Test Runner 等 Public npm Dependency 仍然要求 Configured Registry 或已经填充的 Package-Manager Store；第十阶段不宣称 Offline Dependency Installation，也不把 Browser SDK 发布到 Remote Registry。

## 启动和关闭

Host Startup 会校验 Control 和 Watcher Config，激活 Initial Catalog Plugin，绑定 Loopback Application，然后启动 Filesystem Observation。Startup Failure 通过现有 Host Rollback Path 关闭 Adapter 和 Watcher。Initial Catalog Activation 和之后的 Control Operation 使用同一个 Mutation Coordinator。

Shutdown 首先将 Control Plane 标记为 Closing 并拒绝 New Mutation，然后停止接收 Watcher Event，并取消尚未到达 Manager Dispatch 的 Queued Operation。它会等待正在 Dispatch 的一个 Mutation Settled，然后 Host 按照 [Host Assembly 规范](host-assembly.zh.md)关闭 Browser Connection、Disable Plugin 并关闭 PyCordis。Concurrent Close Call 会 Join 同一次 Shutdown。

Queued Cancellation 和 Watcher Shutdown 具有 Bounded、Configured Timing 和 Structured Diagnostic。Control Plane 绝不会在它 Dispatch 的 Manager Mutation 仍在运行时报告 Shutdown Complete，也绝不会在 Plugin Teardown 开始后启动 Deferred Hot Update。

## 失败处理

- Invalid JSON、Unknown Field、Unsafe Root、Unsupported Action 和 Invalid Precondition 在没有 Manager Mutation 的情况下失败。
- Candidate Validation Failure 保持 Current Installed 或 Active Revision 不变，并报告 Candidate Path 和 Stable Error Code。
- Activation 和 Cleanup Failure 保留 Manager State 和 Diagnostic；Control Adapter 不伪造 Success，也不重新激活 Old Revision。
- Stale HTTP Intent 返回 Current Snapshot，而 Stale Watcher Intent 触发一次 Fresh Rescan，且不能覆盖 Newer API Decision。
- Filesystem Overflow 或 Watcher Backend Failure 将 Watching 标记为 Failed，并保持 HTTP Control 可用；本阶段要求显式重启 Host 才能恢复。
- SDK Asset Digest 或 Version Mismatch 在写入 Partial Project 或 Package 前使 Export、Scaffold Generation 或 Distribution Build 失败。
- Unexpected Response Serialization Failure 使用 Operation ID 记录 Log，并返回 Generic Error；除这个 Local API 已经授权的 Field 外，不泄露 Local Path。
- Shutdown 在单个 Queue、Watcher、Adapter 或 Plugin Cleanup Failure 后继续，并通过 Host Aggregate Cleanup Error 报告这些 Failure。

## 验收标准

- API Test 验证 Loopback-Only Startup、Exact-Origin Browser Request、Content-Type Enforcement、Stable JSON Error，以及拒绝 Non-Loopback Control Exposure。
- Inventory Test 验证 Deterministic Ordering、完整的 Manager 和 Browser Readiness Field、Watcher Status、Immutable Observation 和 Stable Diagnostic Code。
- Lifecycle Test 通过真实 Host API 对 Backend-Only、Client-Only 和 Full-Stack Plugin 执行 Install、Enable、Disable、Update、Rollback 和 Uninstall。
- Concurrency Test 验证 FIFO Manager Dispatch、Atomic Response Snapshot、Stale Revision 和 Mutation-Version Conflict、没有 Automatic HTTP Retry，以及 Stale Watcher Rescan。
- Cancellation 和 Shutdown Test 验证 Pre-Dispatch Cancellation、Client Disconnect 后完成 Post-Dispatch Work、Closing-Service Rejection、Queue Draining，以及 Teardown 开始后没有 Mutation。
- Watcher Test 验证 Debounce Coalescing、In-Flight Rescan、Same-Revision No-Op、Valid Hot Update、Invalid-Candidate Preservation、Create Policy、Delete Policy 和 Cleanup-Failure Retention。
- 一个无密钥 Chromium Scenario 修改 Full-Stack Plugin 的 Backend 和 Built Client Artifact，通过 API 观察一个 New Revision，Reconcile 已连接 Page，并验证 Old Backend 和 Cordis TS Effect 均不存在。
- CLI Test 针对 Listening Host 运行，覆盖每条 Command，保留 Structured Output，对 Failed Outcome 返回 Nonzero，并且不重试地暴露 Conflict。
- Isolated Wheel Installation 导出 Browser SDK，生成 Client-Only 和 Full-Stack Project，在没有 Workspace Link 的情况下安装其 Locked Frontend Dependency，并通过 Type Check、Test、Build、Validation 和 Host Activation。
- Ruff、严格 Pyright、Python Test、TypeScript Check/Test/Build、Python Distribution Build、Wheel Smoke Test 和 Documentation Check 全部通过；第十阶段 Implementation 落地时，`docs/progress.md` 记录 Evidence。

## 不在范围内

Remote Plugin Registry、Plugin Search 或 Download、Remote Browser SDK Publication、Dependency Installation、Lockfile Update、Signature、Provenance、Trust Policy、Untrusted-Code Sandboxing、Process Isolation、Production Authentication、Authorization、TLS Termination、Proxy Deployment 和 Multi-User Control 不属于第十阶段。

Watcher 不编译 Source，不迁移 Plugin State，不保留 Durable Operation History，不同步多个 Host，也不保证 Process Restart 后 Recovery。Persistent Inventory、Durable Audit Log、Background Job API、Logical Plugin 之间的 Dependency Graph，以及基于 Browser Readiness 的 Automatic Rollback 需要独立规范。
