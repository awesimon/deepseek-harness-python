# 动态 Plugin Manager 规范

状态：第三阶段规范

## 用途

Dynamic Plugin Manager 负责一个逻辑插件中可选的 Python Backend 和 TypeScript Browser Contribution。它发现并校验插件制品，激活或移除两端运行时功能，发布不可变 Client Revision，并报告聚合状态。

## 范围

第三阶段包括可信本地插件目录、根 Manifest 解析、内容 Revision、Python Entry Point 加载、PyCordis Fiber 归属、Client Bundle 发布、Enable、Disable、Update、单次激活回滚和可观察状态。

远程 Package 下载、依赖安装、签名、Registry 分发、不受信任代码隔离、持久化 Inventory 和浏览器交付不属于第三阶段。浏览器交付由第四阶段提供；在允许第三方 Backend Plugin 前，进程型 `BackendHost` 必须替换可信进程内 Host。

## 根 Manifest

每个插件目录包含 `plugin.toml`。其中 `[plugin]` 表是身份和版本的唯一权威来源，并且必须至少存在 `[backend]` 或 `[client]` 之一。

```toml
[plugin]
id = "com.example.echo"
version = "1.0.0"
runtime_api = "1"

[backend]
entrypoint = "backend/plugin.py:plugin"

[client]
bundle = "frontend/dist/client.js"
platform = "web"

[protocol]
schema = "protocol/api.schema.json"

[activation]
backend = "required"
client = "optional"
```

Contribution 和 Protocol Path 必须是相对、规范化且位于插件根目录内的路径。解析 Symlink 后也不能逃逸根目录。Backend Entry Point 格式为 `<relative-python-file>:<attribute>`。该 Attribute 是 `PluginSpec`，或一个返回 `PluginSpec` 的无参数 Factory。嵌套的 `pyproject.toml` 和 `package.json` 只是构建输入，不能重新定义身份或版本。

每个已存在 Contribution 的 Activation Policy 是 `required` 或 `optional`；不存在 Contribution 时禁止配置其 Policy。未知 Field 会使校验失败，避免拼错的权限或路径被静默丢弃。

## Revision 身份

`PluginRevision` 是规范化 Manifest Byte 和所有已声明 Backend、Client、Protocol Artifact 的 SHA-256 Digest。Manager 每次构建 Revision 时只读取一次各文件，并为 Client 和 Protocol 发布保留不可变 Byte。

即使 Semantic Version 没有变化，已声明内容改变也会产生新 Revision。重复启用同一个已安装 Revision 是幂等操作。Update 要求 Revision 不同；在增加分发支持前可以进一步收紧 Version Policy。

## 运行记录和状态

Manager 为每个 Plugin ID 保存一个 Record：

```text
DISCOVERED -> VALIDATED -> STARTING -> ACTIVE
                              |          |
                              v          v
                           FAILED     DISABLING -> DISABLED
                              |
                              v
                           DEGRADED
```

Record 公开 Manifest、Source Root、Current Revision、Desired Enablement、Backend Fiber 状态、已发布 Client Revision、聚合状态和最近一次 Structured Diagnostic。状态归 Record 所有，而不归任何语言专用 Package File 所有。

## 发现和安装

`discover(directory)` 按稳定名称顺序扫描直接子目录，返回每个有效 `plugin.toml`，并为无效 Candidate 返回 Diagnostic。Discovery 不导入 Python，也不发布 Client Byte。

`install(plugin_root)` 校验一个 Artifact 并创建或替换 Disabled Record。现有 Record 被卸载前，另一个 Root 不能声明同一个 Plugin ID。Uninstall 要求插件已禁用，并移除其 Inventory 和未发布 Revision Byte。

## 后端激活

`BackendHost.start(revision, context)` 返回 Backend Activation Handle。可信进程内 Host 使用包含 Revision 的 Module Name 加载已声明文件，解析导出的 PluginSpec，并将其作为 Manager Context 的 Child 挂载。Handle 拥有该 Fiber，并通过正常 PyCordis Disposal 停止它。

每次 Backend Activation 都会获得私有的 `PLUGIN_RUNTIME_IDENTITY` Service，其中包含由 Manager 确定的 Plugin ID 和 Revision。该 Service 使用隔离 Realm，并随 Backend Activation 一起移除。Backend Plugin 使用此身份注册带 Revision 的 Browser Bridge RPC 和 Event，不自行推导 Digest，也不通过用户配置接收身份。

进程内 Host 只保证资源和注册项清理，不保证 Python Code Eviction。进程型 Host 会保留 Manager Interface，同时替换 Module Lifetime 和 Wire Transport。

## Client 发布

`ClientArtifactRegistry.publish(plugin_id, revision, bundle)` 存储按 Plugin ID 和 Revision 寻址的不可变 Bundle Byte，并返回 Disposer。发布不表示任何浏览器已经加载该 Revision。第四阶段使用该 Registry 提供 Bundle，并协调已连接页面。

仅客户端插件合法，并在发布后进入 Active。仅后端插件合法，并在 Backend Fiber 达到 `ACTIVE` 后进入 Active。全栈插件根据各自 Activation Policy 要求两端功能。

## Enable、Disable 和 Update

Enable 构建一个不可变 Revision，然后启动 Backend 并发布 Client Contribution。必需 Contribution 失败时，释放该次尝试创建的所有 Contribution，Record 进入 `FAILED`。只有可选 Contribution 失败时，保留成功 Contribution，Record 进入 `DEGRADED`。

Disable 先将 Record 标记为不可服务，再移除 Client Publication、释放 Backend Fiber、等待清理，最后进入 `DISABLED`。重复 Disable 是幂等操作。

Update 在触碰 Active Revision 前构建并校验 Candidate，然后禁用当前 Activation 并启用 Candidate。Candidate 失败时不留下部分激活内容，并保留上一 Revision Metadata 以便显式 Rollback；它不会声称上一版本代码仍在运行。

## 并发

同一个 Manager 的所有 Mutation 串行执行，读取 Snapshot 不可变。第二个 Enable、Disable、Update 或 Uninstall 等待当前操作结束，再根据结果状态执行。Plugin Code 可以触发普通 PyCordis Convergence，但不能递归修改自己的 Manager Record。

## 失败处理

- 无效 TOML、未知 Field、不安全 Path、缺失 Artifact、重复 ID、不支持的 Runtime API 和无效 Entry Point 均在激活前失败。
- Backend Import、Factory、Mount 或 Fiber Activation Failure 会转换为 Structured Diagnostic，并回滚该次尝试拥有的 Contribution。
- Client Publication Failure 按 Required 或 Optional Activation Policy 处理。
- Cleanup Error 保留在 Activation Record 中，且不会恢复 Serving State。
- `FAILED` 或 `DEGRADED` Plugin 可以先 Disable，再显式 Retry。

## 验收标准

- Manifest 测试覆盖仅后端、仅客户端、全栈、未知 Field、没有 Contribution、无效 Activation Policy 和逃逸根目录的 Path。
- Revision 测试验证 Hash 的确定性，以及 Backend、Client、Protocol 或 Manifest Byte 改变都会改变 Hash。
- Backend Plugin File 可以在运行时安装和启用、提供 PyCordis Service，并在 Disable 时完整移除。
- Active Backend 能解析 Manager 管理的精确身份，其他 Plugin 不能观察该隔离 Identity Service。
- Client Bundle 按不可变 Revision 发布并在 Disable 时移除，同时不声称浏览器已经激活。
- Full-Stack Required Failure 回滚另一端 Contribution；Optional Failure 产生 `DEGRADED`。
- Update 加载使用不同 Revision Module Name 的 Backend，并且绝不让两个 Revision 同时服务。
- 并发 Mutation 测试验证串行、幂等的 Enable 和 Disable。
- `docs/progress.md` 将可信进程内加载记录为显式限制，直到实现进程型 Host。
