# Python Harness 架构规范

[English](architecture.md) | 中文

状态：实现草案规范

## 目标

为 DeepSeek Harness 构建插件优先的 Python 后端，同时保留 TypeScript Cordis 作为浏览器插件运行时。运行时基础稳定后，产品开发通常只需增加插件，而不必修改中央 Agent Loop。

## 两个运行时，一个逻辑插件模型

系统包含两个独立的运行时容器：

- **Cordis TS** 负责浏览器服务、UI Slot、Session 展示、客户端状态和客户端热重载。
- **PyCordis** 负责 Agent、Session Log、LLM Provider、工具、存储、沙箱、Workflow 和后端生命周期。

一个逻辑插件拥有一个身份和版本，可以向任一运行时或同时向两者提供功能：

| 形式 | 后端功能 | 客户端功能 |
|---|---:|---:|
| 仅后端 | 是 | 否 |
| 仅客户端 | 否 | 是 |
| 全栈 | 是 | 是 |

两端功能分别拥有独立的 Fiber 和 Effect，只能通过显式线协议通信。任何一端都不会向另一端暴露 Context 或进程内 Service 对象。

## 插件制品

根 Manifest 是插件身份、版本、激活策略、权限和功能位置的唯一真源。各语言自己的 Manifest 仅作为内部构建输入。

```toml
[plugin]
id = "com.example.git"
version = "1.0.0"
runtime_api = "1"

[backend]
entrypoint = "git_plugin:plugin"

[client]
bundle = "dist/client.js"
platform = "web"

[protocol]
schema = "protocol/api.schema.json"

[activation]
backend = "required"
client = "optional"
```

除 `[plugin]` 外，每个表都是可选的。嵌套前端目录中的 `package.json` 可以驱动 TypeScript 构建，但它是内部文件，不能定义第二套插件身份或版本。

## 激活

Plugin Manager 位于两个运行时之上，负责安装和聚合状态：

```text
DISCOVERED -> VALIDATED -> BACKEND_ACTIVE -> CLIENT_PUBLISHED -> ACTIVE
                                |                  |
                                v                  v
                         BACKEND_FAILED      CLIENT_FAILED
                                                   |
                                                   v
                                               DEGRADED
```

对于后端功能，Manager 导入入口点并挂载 PyCordis Fiber。对于客户端功能，Host 发布带内容哈希的 Bundle 记录；已连接的浏览器获取新 Revision 并挂载 Cordis TS Fiber。

必需功能失败时，插件的另一端功能会被回滚。可选功能失败时，插件进入 `DEGRADED`。浏览器运行在独立进程中，因此激活只能协调，无法做到事务式原子提交。

## 运行时可见性

动态变更在各能力对应的安全时点提交：

- UI Slot 在客户端 Fiber 注册后可见；
- RPC Endpoint 在后端 Fiber 激活后可见；
- 工具和 Prompt Section 在下一个 Agent Step 边界可见；
- 进行中的 LLM 请求或工具执行继续使用它开始时取得的能力快照；
- Provider 被替换后，依赖它的 Fiber 会停用、清理并重新激活。

## 能力结构

后端能力包含三个角色：

1. **Service Definition**：Python Protocol、请求/结果值、事件和错误。
2. **Service Provider**：在 PyCordis 中注册的一种实现。
3. **Consumer**：使用 Definition 的工具、命令、API 或其他 Service。

Provider 和 Consumer 都依赖 Definition，彼此不直接依赖。因此替换 Provider 时不需要修改 Consumer。

## 持久化 Agent 规则

Session Event Log 是模型历史的真源。模型可见的所有内容都必须能从持久化事件重建。Prompt 变更、工具 Schema、注入的上下文、请求配置、模型 Chunk、组装后的 Message、调用和结果都要根据其重放要求写入日志。

## 安全

- 后端 Service 默认不能被远程调用。
- 远程 Method 或 Event 必须有显式线协议声明，并只使用 JSON 兼容值。
- Credential 只留在后端；前端配置只接收显式的公开投影。
- 插件 Manifest 声明文件系统、子进程、网络、Credential 和浏览器权限。
- 不受信任的后端插件最终应在 Worker Process 中运行。PyCordis 卸载会移除注册项和资源，但不能从进程内存中抹除已经导入的 Python 代码。

## 能力顺序

[实现进度](../progress.md)统一记录完成状态和验证证据。阶段顺序只描述各规范之间的依赖：

1. [PyCordis 内核](cordis-core.zh.md)建立 Service、Fiber、Effect 和 Event。
2. [后端 Agent Spine](agent-spine.zh.md)建立持久化的模型可见状态和扩展 Registry。
3. [Dynamic Plugin Manager](plugin-manager.zh.md)管理根 Manifest、Revision 和两端 Contribution Lifecycle。
4. [Browser Bridge](browser-bridge.zh.md)把 Client Revision 投影到 Cordis TS，并传递显式 RPC 和 Event。
5. [Host Assembly](host-assembly.zh.md)组合 Provider、Catalog、HTTP/WebSocket Transport 和进程 Teardown。
6. [Plugin Authoring SDK](plugin-sdk.zh.md)提供两个生命周期 Runtime 上受支持的开发 API。
7. [Plugin Templates and Scaffolding](plugin-templates.zh.md)依赖 SDK，并生成三种 Contribution Form。
8. [Multi-Page Client Activation](multi-page-activation.zh.md)根据 Manager 和 Bridge State 推导部署 Readiness，不依赖开发工具。

Persistence、Filesystem、Subprocess、Sandbox、Terminal、LSP、Compaction、Subagent、Workflow、Skill、Settings 和 Credential 等产品能力各自需要后续规范，并继续作为这些基础之上的普通 Plugin。

## 验收里程碑

当一个生成的 Full-Stack Plugin 可以在没有网络依赖的 Runtime Behavior 下完成构建，通过 Assembled Host 激活，交换 Typed RPC 和 Event，更新两端 Revision，并在 Disable 时完整移除所有 Contribution 时，Authoring Path 得到验证。
