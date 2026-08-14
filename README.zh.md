# DeepSeek Harness Python

[English](README.md) | 中文

DeepSeek Harness 的 Python 后端运行时。浏览器继续使用 TypeScript Cordis 插件运行时；本项目提供 PyCordis 和后端 Agent 运行时，并通过显式的线协议连接两端。

实现从生命周期内核开始，因为后续所有能力都依赖它的服务、Effect、事件和隔离语义。

## 状态

第一至第三阶段已经完成：

- 架构和迁移规范；
- TypeScript 源码机制索引；
- PyCordis 服务注册表和 Fiber 生命周期；
- 可逆 Effect；
- 带类型的事件键和 Waterfall 分发；
- 服务隔离 Realm。
- 只追加 Session Event 和确定性投影；
- 有 Scope 的 Prompt 和 Tool Registry；
- 显式 LLM 路由和 Stream 约束；
- 支持 Tool Execution 的持久化多 Step Agent Loop。
- 严格根插件 Manifest 和内容寻址 Revision；
- 运行时 Backend Enable、Disable、Update 和 Rollback；
- 面向浏览器桥接的不可变 Client Bundle 发布。

后续阶段将增加进程型插件隔离、浏览器交付与协调，以及可安装的多端示例。当前验证证据和下一里程碑见[实现进度](docs/progress.md)。

## 目录

```text
docs/specs/          Normative design and phased implementation plan
docs/source-notes/   Stable summaries of the TypeScript reference behavior
src/deepseek_harness Python packages
tests/               Standard-library unit tests
```

## 测试

```sh
uv run python -m unittest discover -s tests
```
