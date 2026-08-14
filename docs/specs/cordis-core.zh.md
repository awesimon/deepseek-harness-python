# PyCordis 核心规范

[English](cordis-core.md) | 中文

状态：第一阶段规范

## 用途

PyCordis 是后端插件生命周期内核。它保留 Cordis 的行为理念，但不照搬 JavaScript 的实现技术。

## ServiceKey 和 Realm

`ServiceKey[T]` 是一种稳定的、以名称标识并带有静态值类型的键。注册项使用 `(ServiceKey, Realm)` 寻址。根 Context 为每个键使用一个根 Realm。`Context.isolate()` 返回一个子 Context，其中选定的键通过新的不透明 Realm 解析。

一个键和 Realm 最多只能有一个激活的注册项。重复注册会使提供它的 Fiber 失败，并回滚该 Fiber 激活期间创建的全部 Effect。

## 依赖声明

插件在 `PluginSpec.requires` 中声明所需的 ServiceKey。在每个键都能通过该 Fiber 的 Realm 解析到激活 Provider 之前，Fiber 保持 `PENDING`。

`Context.require()` 拒绝未声明的访问。反射和可选的基础设施代码可以使用显式的 `Context.lookup()` API；普通插件不得用反射隐藏依赖。

## Fiber 生命周期

```text
PENDING -> LOADING -> ACTIVE
             |          |
             v          v
           FAILED <- UNLOADING -> PENDING
                                      |
                                      v
                                  DISPOSED
```

依赖 Epoch 是 Provider 注册 Generation 组成的有序元组。当它发生变化时，激活的 Fiber 会卸载，并可以根据新的 Epoch 重新激活。失败的 Fiber 仅在依赖 Epoch 改变或收到显式重试请求后重试。

Provider 处于 `LOADING` 时发布的 Service 对 Provider 自身可见，但在 Provider 进入 `ACTIVE` 前不能满足依赖者。

## Effect

每个注册项都归一个 Effect 所有。Effect Setup 可以不返回 Cleanup、返回一个 Cleanup，或返回一组 Cleanup。同一个 Effect 内的 Cleanup 按相反顺序执行。Fiber 卸载时会并发释放顶层 Effect，并等待所有清理尝试结束。

Effect 只能执行一次。激活失败时，Fiber 在进入 `FAILED` 前释放该次尝试创建的全部 Effect。

## 事件

一个 `EventKey` 固定一个事件名称和分发模式：

| 模式 | 语义 |
|---|---|
| `EMIT` | 同步按序通知 Observer；拒绝 Awaitable |
| `PARALLEL` | 启动所有 Listener 并等待全部结果 |
| `SERIAL` | 按序等待 Listener，在第一个 Bail Value 处停止 |
| `WATERFALL` | 将 Listener 组合在显式 Terminal Callback 外层 |

串行结果只要不是 `None` 或 `False` 就会 Bail。Waterfall Listener 的最后一个参数是 `next`。不调用它会短路后续 Listener 和 Terminal 行为。

Listener 属于 Effect，会随其所属 Fiber 一起消失。

## 并发

运行时串行执行生命周期收敛。Plugin Code 可以在收敛期间注册 Service、Listener、Effect 或 Child Fiber；这些变更会将依赖图标记为 Dirty，并使同一次收敛持续到状态无法继续变化。

第一阶段假设只有一个 asyncio Event Loop。线程安全变更和跨进程插件属于后续阶段。

## 有意采用的 Python 设计

- 显式 `require()` 取代 JavaScript Context Proxy 访问。
- `ServiceKey[T]` 和 Python Protocol 取代 TypeScript Declaration Merging。
- 所有生命周期入口都是 Async。
- 不承诺移除已导入的 Module。Unload 表示资源和注册项的清理。
- Config Validation 是 `PluginSpec` 上的可选 Callable；Pydantic 集成属于 Plugin Manager 阶段。
