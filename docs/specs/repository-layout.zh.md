# 仓库布局对齐规范

[English](repository-layout.md) | 中文

状态：完成

## 目的

仓库顶层布局与 DeepSeek Harness 对齐：浏览器基础设施和 Python 基础设施是平级工作区，共享文档保留在仓库根目录。

## 必需布局

```text
frontend/                 # TypeScript Cordis 浏览器 Runtime 和 SDK
python/                   # Python Harness Project
  harness/                # import harness
  tests/                  # Python 测试
  pyproject.toml
  uv.lock
docs/                     # 共享规范和进度
README.md
README.zh.md
```

`frontend/` 和 `python/` 是独立的构建面。插件自身可选的 `frontend/` 仍然位于插件根目录内，不改变仓库级 Browser Workspace 的归属。

## Python Project 边界

Python Project 使用 `uv --directory python ...` 或在 `python/` 内执行。Import Root 仍为 `harness`；不引入 `src/` 目录或 `deepseek_harness` 兼容 Import。Python Package Data、测试、构建输出和 Lock Metadata 都属于 `python/`。

根目录文档和浏览器命令使用明确的 `frontend/`、`python/` 路径，因此任一 Workspace 不依赖调用者的当前目录。此次布局调整不改变 Plugin Manifest、Runtime API、Wire Protocol 或 Package Name。

## 验收

- 根目录包含平级的 `frontend/` 和 `python/` Workspace。
- Python 测试、静态检查、构建、Wheel Smoke 和 Scaffolder Project 检查可从文档中的根目录命令通过。
- Frontend Typecheck、测试和构建可从 `frontend/` 通过。
- 文档引用和双语配对保持有效。
