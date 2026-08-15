# 多页面 Client Activation 聚合规范

状态：第八阶段规范

## 用途

多页面 Client Activation 聚合根据 Browser Bridge 中所有已连接页面的状态，推导一个带 Revision 的 Client Readiness 结果，并通过 Plugin Manager 报告。它使浏览器激活状态可观察，同时不转移 Manager 对 Plugin Lifecycle 的权威，也不转移 Bridge 对页面局部 Fiber 的权威。

## 范围

第八阶段包括正式的 PyCordis Aggregation Provider、经过校验的 Host Quorum Config、确定性的 Page Membership、带 Revision 的 Aggregate Snapshot、Required 和 Optional Readiness Rule、Structured Diagnostic、Update 和 Disable Handling，以及无密钥多页面浏览器验证。

本阶段聚合 [Browser Bridge 规范](browser-bridge.zh.md)定义的页面局部状态。它不改变 Bundle Evaluation、Cordis TS Fiber Ownership、Package-Private RPC、Event Routing 或根 Plugin Manifest。

## 权威状态

Plugin Manager 继续作为 Installation、Desired Enablement、Current Revision、Contribution Policy、Publication 和 Plugin Record 的权威。Browser Bridge 继续作为 Live Page Connection、Accepted Connection Generation、Reconciliation Operation，以及每个页面报告的 Plugin ID 和 Revision State 的权威。

Aggregation Provider 只拥有派生的 `ClientActivationSnapshot`。它不能 Enable、Disable、Update、Publish、Reconcile 或 Dispose Plugin。它向 Manager 所有的 Reporting API 提交 Snapshot；只有 Plugin ID、Desired Enablement 和 Revision 仍与 Current Record 一致时，Manager 才会接受。

Backend-Only Plugin 的 Client State 为 `not_applicable`。Publication Success 和 Browser Activation 仍是不同事实：已发布的 Client Contribution 可以是 `unobserved`、`reconciling`、`active`、`degraded` 或 `failed`。

## 配置

`HarnessHostConfig` 包含经过校验的 Client Activation Aggregation Config，其中有 Default Quorum 和可选的 Plugin ID Override。Quorum 可以是 `all_connected` 或 `any_connected`；省略配置时默认为 `all_connected`。Override 必须指向一个带 Client Contribution 的 Installed Plugin；重复、未知、Backend-Only 或不受支持的 Entry 会使 Host 启动失败。

`all_connected` 要求每个 Eligible Page 都将 Current Revision 报告为 Active。`any_connected` 要求至少一个 Eligible Page 将 Current Revision 报告为 Active。空 Eligible Set 不满足任一种 Quorum。

Quorum 是 Deployment Choice，不是 Plugin Package Identity，因此不会向 `plugin.toml` 添加 Field。根 Manifest 的 `activation.client` 保留不同用途：`required` 将 Quorum Satisfaction 纳入 Plugin Readiness，`optional` 则允许 Browser Activation 失败时其他 Contribution 仍保持 Ready。

## Page Membership

Bridge 接受页面在当前 Logical Connection 上的 `hello` 后，该页面成为 Eligible。第八阶段没有 Route、Tenant、Capability 或 Plugin-Interest Selector，因此每个 Eligible Page 都参与每个当前已发布 Client Plugin 的聚合。

Membership 由不透明 Page ID 和 Connection Generation 共同标识。使用相同 Page ID 替换 Connection 时，会在一次串行 Membership Change 中移除旧 Generation 并接纳已接受的 Replacement；两个 Generation 绝不会同时计数。来自被替换 Generation 的 Frame 不能改变 Membership 或 Aggregate State。

只有 `hello` Inventory 中与当前已发布 Revision 精确匹配的内容才能初始化 Page State。Bridge 校验后，匹配且已加载的 Revision 计为 Active。缺失、未发布或过期的 Inventory Entry 不计为成功，并进入正常 Reconciliation。

Disconnect 会立即从 Eligible Set 中移除页面，并按照 Bridge 规范取消其未完成 Call。Reconnect 会创建新的 Generation，并根据新的 Inventory 重新评估。Aggregator 不会把已断开的页面保留为 Quorum Member，也不会假设其 Cordis TS Fiber 仍然存在。

## 聚合模型

每个不可变 `ClientActivationSnapshot` 都包含 Plugin ID、发布时的 Current Revision、Client Activation Policy、Quorum、State、Eligible Page Count、Active Page Count、Pending Page Count、Failed Page Count 和 Current Page Diagnostic。Count 和 Diagnostic 从同一个串行 Bridge Snapshot 派生，因此 Caller 不会观察到由不同 Connection Generation 拼接的 Total。

对于已发布 Revision，朝向该 Revision 的 `loading`、`waiting` 和 `unloading` 都属于 Pending。精确 Revision 的 `active` 属于 Success。Terminal `failed`、Operation Result 结束后 Desired Revision 仍为 Absent，或 Operation-Level Failure 中仍未解决的 Desired Entry 都属于 Failed。其他 Revision 的 State 在 Current Operation Settled 前属于 Pending，不能仅因过期就计为 Target 的 Active 或 Failed。

Aggregate State 按以下顺序派生：

1. 没有 Eligible Page 时，State 为 `unobserved`。
2. Quorum 已满足且没有 Eligible Page 失败时，State 为 `active`。
3. Quorum 已满足且至少一个 Eligible Page 失败时，State 为 `degraded`。
4. Quorum 未满足且至少一个 Eligible Page 仍为 Pending 时，State 为 `reconciling`。
5. 非空 Eligible Set 已 Settled 但未满足 Quorum 时，State 为 `failed`。

没有 Current Publication 的已安装 Client Contribution 为 `not_published`。Publication 撤回后，只要任何 Eligible Page 仍将被撤回 Revision 报告为 Active 或 Unloading，它就是 `draining`；之后变为 `not_published`。没有 Client Contribution 的 Plugin 始终为 `not_applicable`。

## Plugin Manager 状态

Client Aggregation 是 Manager Aggregate Plugin State 的一个输入；Desired Enablement 和 Backend Activation 仍是独立输入。Host Startup 和 `enable()` 在 Process-Local Contribution 启动且 Publication 成功后完成，不等待 Browser Connection，因为页面开始 Reconcile 前 HTTP Listener 必须可用。

对于 Required Client Contribution，`unobserved` 或 `reconciling` 使已启用 Plugin 为 `WAITING`，`active` 使其为 `ACTIVE`，`degraded` 使其为 `DEGRADED`，`failed` 使其为 `FAILED`。Browser Activation Failure 不会自动撤回 Publication 或停止已成功的 Backend Contribution；Membership Change 或之后成功的 Reconciliation 可以恢复同一个 Revision 的 Aggregate State。

对于 Optional Client Contribution，当每个 Process-Local Required Contribution 都 Ready 时，`unobserved`、`reconciling` 或 `active` 不会阻止 Plugin 进入 `ACTIVE`；对于 Client-Only Plugin，该条件自然成立。`degraded` 或 `failed` 会使 Plugin 进入 `DEGRADED`；它绝不会回滚或使成功的 Required Backend Contribution 失败。

Process-Local Required Activation Failure 保留第三阶段的 Rollback Behavior，并优先于 Client Aggregation。Publication 之后的页面失败属于 Readiness State，而不是新的 Activation Attempt，因此其可恢复 `FAILED` State 不会重新执行第三阶段的 Contribution Rollback。Required Client 处于 Waiting 时，Optional Backend Failure 仍显示在 Contribution Diagnostic 中，但 Aggregate State 保持 `WAITING`；Client Quorum Settled 后，Optional Failure 产生 `DEGRADED`。Desired Disablement 优先于所有 Readiness Result，并产生 `DISABLING` 或 `DISABLED`。

`/health` 继续作为 Process Liveness Endpoint，不因 Browser Readiness 阻塞 Host Startup。需要等待 Plugin 在浏览器中可用的 Caller 应使用 Manager Snapshot，后者是 Readiness 和 Diagnostic 的权威 API。

## 状态和诊断

每个 Failed Page Entry 都保留 Structured Diagnostic，其中包含稳定 Error Code、Plugin ID、Target Revision、Page ID、Connection Generation、可用时的 Operation ID、Page State 和简洁 Message。Pending Entry 暴露 Target Revision 和 Operation Identity，但不伪造 Error。

Plugin Record 只能将最近一条过期、被替换、已断开、已撤回或属于 Previous Revision 的 Client Diagnostic 保留为 Non-Current Context。它不参与 Current Count 或 Readiness。健康的精确 Revision Report 会清除该 Page 的 Current Failure。Revision Replacement 在评估 New Target 前清除 Current Aggregate Diagnostic。

Snapshot 按 Page ID 确定性排序。Browser Exception Text 是 Diagnostic Data，不是 Protocol Decision Key；Aggregation 只根据经过校验的 State 和 Identity 分支。

## Update 和 Disable

Manager 接受并发布 Candidate Revision 后，Update 启动新的 Aggregation Generation。Previous Revision 的 Page State 或 Result 都不能满足 New Generation。替换 Old Revision 的 Page 在报告 Candidate Active 或 Failed 前属于 Pending；Aggregate 可以随 Membership Change 在 `unobserved`、`reconciling`、`active`、`degraded` 或 `failed` 之间变化。

已发布 Candidate 的 Browser Failure 遵循上述 Required 或 Optional Status Rule，并且不会静默重新激活 Previous Revision。之后的显式 Update 或 Retry 负责恢复到另一个 Revision。

Disable 先将 Plugin 标记为 Non-Serving 并撤回 Publication，再完成 Client Unload。Manager State 可以在不无限等待 Browser Acknowledgement 的情况下进入 `DISABLED`；带精确 Revision 的 Bundle、RPC 和 Event Authorization 已不可用。Client Snapshot 报告 `draining`，直到所有仍连接的 Page 都将被撤回 Contribution 报告为 Absent 或断开，然后报告 `not_published`。

Host Shutdown 可以按照 [Host Assembly 规范](host-assembly.zh.md)先关闭 WebSocket Traffic，再 Disable Plugin。因此 Connection Removal 会使 Membership Settled，不要求已被 Host 断开的 Page 返回 Unload Acknowledgement。

## 失败处理

- Invalid 或 Stale Frame 仍属于 Bridge Failure，不能改变 Aggregate Snapshot。
- 单个 Plugin Activation Failure 只影响对应 Plugin ID、Revision 和 Page Generation；其他 Page 或 Plugin 继续 Reconcile。
- Operation-Level Failure 使用同一个 Operation Identity 将该 Operation 中每个未解决 Desired Entry 标记为 Failed，已经校验的 Result 保持不变。
- Duplicate Page ID Replacement 对 Aggregation 是原子的，并拒绝来自 Replaced Generation 的 Late Result。
- Disconnect 移除 Quorum Membership，但可以将最近一条 Client Diagnostic 保留为 Non-Current Context。
- 发送给 Manager 的 Snapshot 经过 Revision Check；并发 Update 或 Disable 会拒绝 Stale Report，并根据 Current Authority 触发重新计算。
- Internal Aggregation Failure 必须产生显式 Provider 或 Host Diagnostic，且不能报告伪造的 `active` State。

## 验收标准

- Pure Aggregation Test 覆盖 Empty Membership、`all_connected`、`any_connected`、Pending Work、Partial Failure、Total Failure、Recovery 和 Deterministic Diagnostic。
- Membership Test 验证 Accepted `hello`、Exact-Revision Inventory、Disconnect、Reconnect、Duplicate Page ID Replacement，以及拒绝 Old Connection Generation 的 Result。
- Manager Test 验证 Backend-Only `not_applicable`、Required 的 `WAITING`/`ACTIVE`/`DEGRADED`/`FAILED` Transition、Optional Failure Degradation、Process-Local Failure Precedence，以及不重新发布同一个 Revision 的 Recovery。
- Update Test 验证 Old Revision 或 Operation Result 不会参与 Candidate Aggregate，并且 Candidate Browser Failure 绝不会静默恢复 Previous Revision。
- Disable Test 验证立即撤销 Serving、可观察的 `draining`、最终的 `not_published`，以及剩余 Page 断开后完成。
- Host Config Test 在提供 Traffic 前拒绝无效 Quorum Name 和无效 Plugin ID Override。
- 无密钥 Chromium 场景连接至少两个页面，在两种 Quorum Mode 下展示不同 Page Outcome，通过 Membership 或 Reconciliation Change 恢复，Update 到 New Revision，并在 Disable 后不留下 Current Page 或 Backend Registration。
- Ruff、严格 Pyright、Python Test、TypeScript Type Check/Test/Build 和 Documentation Check 全部通过；第八阶段代码落地时，`docs/progress.md` 记录 Implementation Evidence。

## 不在范围内

按 Route、Tenant、User、Browser Capability 或 Plugin-Declared Interest 选择 Page 不属于第八阶段。Cross-Process 或 Cross-Host Quorum、Durable Page History、Metrics Retention、Background-Tab Lease、Offline Activation、Authentication、Authorization 和自动回滚到 Old Revision 仍属于独立能力。

Plugin SDK 以及 Backend-Only、Client-Only 或 Full-Stack Authoring Template 由各自规范负责。第八阶段使用同一个 Root Manifest 和 Client Protocol，不会让 Aggregation 与 Template Generation 耦合。
