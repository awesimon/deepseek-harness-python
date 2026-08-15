# Python 包目录

本文档定义 Python Harness 的发行名和导入名。命名规则适用于项目源码、测试、文档和动态加载的后端插件示例。

## 名称

- Python 发行名是 `deepseek-harness-python`。
- 导入根包是 `harness`。
- 项目 Python 源码位于 `python/harness/`。
- 公共模块使用 `harness.cordis`、`harness.agent`、`harness.plugins` 和 `harness.bridge` 命名空间。

发行元数据和 Python 导入面向不同使用方，因此发行名不决定导入根包。打包发现机制只包含 `python/` Project 内的 `harness` 及其子包。

## 导入行为

所有项目导入和生成的后端插件 Fixture 都必须使用 `harness`。本项目尚未发布，也没有外部兼容承诺，因此不提供 `deepseek_harness` 兼容模块。

从本仓库同步的环境必须将 `import harness` 解析到 `python/harness/__init__.py`。导入 `deepseek_harness` 必须抛出 `ModuleNotFoundError`。

## 失败处理

代码导入不存在的模块时，打包、测试和插件加载按 Python 标准行为失败。运行时不得为 `deepseek_harness` 提供别名、重定向或静默兼容。

## 验收标准

- 构建安装和可编辑安装都公开 `harness` 及其公共子包。
- 生产源码和插件 Fixture 不包含 `deepseek_harness` 导入或 `src/` 包路径。
- 使用规定的目录结构时，单元测试、Ruff、严格 Pyright 和字节码编译全部通过。
- 干净运行环境可以导入 `harness`，并拒绝导入 `deepseek_harness`。

## 不在范围内

本次变更不修改发行名、仓库名、插件 Manifest 字段或 TypeScript 包，也不增加兼容别名，不改变 Python 模块解析之外的运行时行为。
