# 产品化路线与持久化 Session 规范

[English](productization-roadmap.md) | 中文

状态：Phase 12 规范

## 产品化顺序

基础阶段建立生命周期和 Wire 语义。产品化按依赖顺序增加用户可见的持久化和运维能力：

1. 持久化 Session Event 和只读 Session Projection。
2. 持久化 Plugin Inventory 和重启 Reconciliation。
3. API、JSON-RPC、ACP Service Assembly。
4. Credentials、Settings、Identity、Approval 和认证 Control。
5. Filesystem、Subprocess、Shell、Terminal、Sandbox、Compaction、Subagent 和 Workflow 能力。
6. Remote Distribution、Signature、Dependency Resolution 和 Backend 隔离执行。

每项能力都继续作为现有 PyCordis 和 Browser Bridge 基础上的 Plugin Capability。

## Phase 12 范围

Phase 12 增加可选的 SQLite-backed Session Store。`SessionLog` 仍是追加日志和 Projection 的权威 API。启用时，一个 Event 必须先提交到 SQLite，随后才对内存 Snapshot 可见；写入失败不会留下部分持久化 Event。

Store 使用一个进程内 SQLite Database、单调 Schema Version 和以 `(session_id, sequence)` 为键的 Event Table。Event Payload 使用带显式 Tag 的严格 JSON。未知 Tag、格式错误 Payload、非有限数字、Sequence Gap、重复 Sequence 和 Session Identity 不一致都必须在启动或追加时显式失败。

Host 配置增加 `--session-db PATH`。需要时自动创建父目录。不配置时保持进程内存行为。启动会在 Listener 绑定前加载指定 Session；进程重启后，已有 History 会像没有停止一样参与下一次 Agent Request。

Host 提供 `GET /api/v1/sessions/{session_id}`，只允许查询当前活动 Session。响应包含有序 Event Envelope 和确定性 Transcript Projection。这是本地只读 API，不是远程多用户 Session Service；Authentication、Pagination、Redaction Policy 和写操作留到后续阶段。

## 失败与生命周期

- SQLite 打开、Schema、Decode 或完整性失败时，Host 在绑定 Listener 前启动失败。
- Event Append 失败时，Agent Step 失败，Event 不进入内存 Log。
- 并发 Append 通过一个 SQLite Connection 串行化并保持单调 Sequence。
- Session Cleanup 由所属 PyCordis Effect 执行，在取消 Invocation 后、Runtime Teardown 完成前关闭 Database。
- Format Version 为 `0`，不承诺兼容；Schema 变化必须使用显式单调 Schema Version。

## 验收

- 新 Database 可以持久化所有支持的 Session Event Variant，并以相同 Projection 恢复。
- 使用相同 `session_id` 重启 Host 后，Model History 恢复且 Transcript 包含之前内容。
- 损坏 JSON、未知 Event Tag、Sequence Gap 和冲突 Append 不会留下部分内存状态。
- Session HTTP Route 返回有序只读观察，并拒绝其他 Session ID。
- 现有内存测试和全部基础检查保持通过。

## 排除项

持久化 Plugin Inventory、多 Session 路由、Migration Tooling、Compaction、Retention、Encryption、Access Control、Remote Streaming 和 Distributed Locking 不属于 Phase 12。
