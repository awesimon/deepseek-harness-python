# Plugin 模板与脚手架规范

状态：第七阶段规范

## 用途

Plugin 模板与脚手架提供从 Plugin ID 到可运行的仅后端、仅客户端或全栈 Plugin Project 的最短受支持路径。生成的 Project 使用第六阶段 SDK、保留唯一根身份，并包含验证其 Contribution 形式的无密钥检查。

## 范围

第七阶段包括三种内置模板、确定性 Generator、Validation Command、根 Manifest 生成、本地 Build 配置、示例 Plugin Behavior、无密钥模板测试和防覆盖文件系统写入。

这些模板面向由 Dynamic Plugin Manager 使用的可信本地 Plugin Catalog。它们展示当前 SDK Entry Point 和 Lifecycle Ownership，而不引入另一层 Runtime Abstraction。

## 命令接口

Distribution 提供 `deepseek-harness-plugin`，并提供等价的 `python -m harness.scaffold` Module Entry Point。两者使用同一个 Parser，并支持以下命令：

```sh
deepseek-harness-plugin create \
  --kind full-stack \
  --plugin-id com.example.echo \
  --destination plugins/echo

deepseek-harness-plugin validate plugins/echo
```

`create` 要求提供 `--kind`、`--plugin-id` 和 `--destination`。`--kind` 只接受 `backend`、`client` 或 `full-stack`；`--version` 默认为 `0.1.0`。Generator 在创建目录前校验全部输入，并在成功后输出已创建的 Plugin Root。

`validate` 接受一个 Plugin Root，通过 Dynamic Plugin Manager 使用的同一套公共校验解析其 Root Manifest，根据该 Manifest 推导 Contribution Form，检查对应模板的 Source File，并要求 Manifest 声明的每个 Runtime Artifact 都存在。因此，新生成的 Client 或 Full-Stack Project 需要先执行文档中的 Frontend Build Command，产出 `frontend/dist/client.js` 后才满足 Runtime Validation。

Usage 和 Input Error 返回非零状态，并向标准错误输出一条可操作 Diagnostic。两个命令都不会启动 Host、安装 Plugin、调用 Package Manager 或下载 Dependency。

## 身份和输入

生成的根 `plugin.toml` 是 Plugin ID、Semantic Version、Runtime API、Contribution Entry Point、Protocol Artifact 和 Activation Policy 的唯一权威来源。Plugin ID 和 Version 只作为 Runtime Metadata 写入一次。

嵌套的 `pyproject.toml` 和 `frontend/package.json` 只包含 Build 和 Dependency Metadata。Tooling Name 由 Plugin ID 确定性派生，但不会声明或覆盖 Runtime Identity。模板绝不接收、计算、持久化或传递 Runtime Revision。

只有 Dynamic Plugin Manager 的 Manifest Validator 接受 Plugin ID 和 Version 时，Generator 才接受它们。Generator 从已安装 Harness SDK 获取写入的 `runtime_api` 和 SDK Dependency Version，而不维护模板私有的 Compatibility Constant。

## 生成目录结构

每种模板都包含 `.gitignore`、`README.md` 和 `plugin.toml`。生成的文档针对该 Contribution 形式给出精确的 Build、Test、Validation 和 Host Catalog Command。

仅后端模板采用以下结构：

```text
.gitignore
README.md
plugin.toml
pyproject.toml
backend/
  plugin.py
tests/
  test_backend.py
```

其 Manifest 声明 `backend/plugin.py:plugin`，不声明 Client 或 Protocol Contribution。Python Project 声明本地开发所需的 Harness SDK Dependency，Runtime 仍直接加载 Manifest Entry Point。

仅客户端模板采用以下结构：

```text
.gitignore
README.md
plugin.toml
frontend/
  package.json
  pnpm-lock.yaml
  tsconfig.json
  src/
    plugin.ts
  tests/
    plugin.test.ts
```

其 Manifest 声明 `frontend/dist/client.js`，不声明 Backend 或 Protocol Contribution。Frontend Build 输出唯一的 ESM Bundle，`.gitignore` 排除 `frontend/dist/`。

全栈模板组合上述两种结构，并增加一个共享 Protocol Artifact：

```text
.gitignore
README.md
plugin.toml
pyproject.toml
backend/
  plugin.py
frontend/
  package.json
  pnpm-lock.yaml
  tsconfig.json
  src/
    plugin.ts
  tests/
    plugin.test.ts
protocol/
  api.schema.json
tests/
  test_backend.py
```

其 Manifest 声明 Backend、Client 和 Protocol Path。两个 Contribution 都是 `required`，使生成的示例不能在只有一端 Active 时表现为健康状态。

## SDK 使用

第七阶段依赖[第六阶段 Plugin Authoring SDK](plugin-sdk.md)，不会把其 Lifecycle 或 Bridge Implementation 复制到生成的 Project 中。生成的 Example 及其 Test 是该公共 SDK 的下游 Compatibility Fixture。

生成的 Backend Code 只从 `harness.sdk` 导入受支持的 Authoring API。仅后端 Code 使用 `define_backend_plugin`；全栈 Code 使用 `define_bridge_backend_plugin`、强类型 RPC 和 Event Descriptor，以及 `BridgeBackendPluginContext` 提供的 Revision-Bound Channel。

生成的 Client Code 从 `@deepseek-harness/browser-bridge-client` 导入 `defineClientPlugin`、`rpcMethod`、`clientEvent` 和 `serverEvent`。Full-Stack Code 使用根据 Browser Bridge Adapter Input 构造的 `ClientPluginContext` 上的 Revision-Bound Operation。生成源码不会把 Plugin ID 或 Revision 作为配置接收，不会读取 Manager Internal，也不会直接构造 Bridge Protocol Frame。

Registration 和 Resource 通过 SDK Helper 创建，并归当前 Cordis Effect 所有。每个示例都包含可观察的 Setup 和 Cleanup Behavior，使作者能够看到 Disable 和 Update 会移除旧 Contribution。

测试只从 `harness.sdk.testing` 或 `@deepseek-harness/browser-bridge-client/testing` 导入 Helper。Production Plugin Module 不依赖 Testing Helper。

## 构建输出

Python Backend Source 就是已声明的 Runtime Artifact，不需要生成副本。Python Dependency 可用且 `validate` 成功后，Backend Template 即可安装。

Client 和 Full-Stack Template 提供 `pnpm run typecheck`、`pnpm run test` 和 `pnpm run build`。对相同 Source 和已锁定 Dependency，Build 结果具有确定性；它只输出作为 Browser ESM 的 `frontend/dist/client.js`，且不嵌入 Timestamp、Absolute Path、Plugin ID Override 或生成的 Revision。

检入的 Lockfile 固定生成 Frontend 的 Dependency Graph。生成的 Build Output 可丢弃并从 Source Control 排除；Plugin Manager 在计算 Runtime Revision 时读取精确的已构建 Byte。

## 生成安全性

Destination 必须不存在，包括空目录或 Symbolic Link。第七阶段不提供 Overwrite 或 Force Option。Parent Directory 必须已经存在并解析为 Directory。

Generator 将每个文件渲染到 Resolved Parent 下私有的临时同级目录，在不要求已忽略 Build Output 的情况下校验生成的 Source Structure，再把完整目录 Rename 到请求的 Destination。失败时只移除由 Generator 所有的临时目录。它绝不移除、截断、合并 Existing Destination，也不改变其 Permission。

Template Path 是固定 Relative Path，不受 Plugin ID 或 Version Value 影响。Generator 拒绝包含 Path Separator 的值，以及无法生成有效派生 Python 和 npm Tooling Name 的值。

## 确定性

在每个受支持平台上，相同 Generator Version 和规范化输入生成相同 Relative Path 和 File Byte。File 使用 UTF-8、LF Line Ending、稳定顺序、固定 Ordinary-File Permission，并在格式允许时只保留一个 Trailing Newline。

生成内容不包含 Wall-Clock Time、Random Identifier、Current Working Directory、Username、Temporary Path、从 Environment 派生的 Package Registry 或 Network Result。同一 Destination 的 Relative 和 Absolute 写法不会改变 File Content。

## 失败处理

- 无效 Kind、Plugin ID、Version、Parent、Destination 或派生 Tooling Name 在写入任何 Destination 前失败。
- Final Rename 时检测到 Destination Race 会失败，且不修改赢得竞争的 Directory。
- Rendering 或 Validation Failure 会移除 Temporary Tree，并报告失败的 Template File 或 Rule。
- `validate` 按稳定 Path 顺序报告每个 Root Manifest 和 Declared Artifact Diagnostic，且绝不导入 Backend Plugin Code。
- 缺少 `frontend/dist/client.js` 时报告必需的 Build Command，而不是生成或下载它。
- Package Manager、Compiler 或 Plugin Test Failure 保持为普通 Tool Failure，不会让 Scaffolder 重写 Project。

## 无密钥验证

每个生成的 Backend Test 通过第六阶段 Test Helper 挂载导出的 PluginSpec，观察其 Example Registration，释放它并验证 Cleanup。每个生成的 Client Test 通过 Browser SDK Test Helper 挂载导出的 Cordis TS Plugin，并在不使用 Browser 或 Network 的情况下验证 Setup 和 Effect Cleanup。

Full-Stack Fixture 还使用内存中的 Revision-Bound Channel，验证一次 Typed RPC Call 和双向各一个 Event。它不要求 LLM Key、正在监听的 Host、Chromium 或 Internet Access。

Repository Test 把三种 Kind 都生成到 Temporary Parent，使用已批准 Template Fixture 比较目录树和 Byte，执行 Validation，并针对 Workspace SDK 运行文档中的 Type Check、Build 和无密钥 Test。使用相同输入再次生成可验证 Byte Equality；Existing Destination 和模拟 Rendering Failure 则验证 User File 保持不变且不存在 Partial Output。

## 验收标准

- 两个 CLI Entry Point 生成完全相同的仅后端、仅客户端和全栈 Project。
- 每个生成的 Root Manifest 都通过 Dynamic Plugin Manager Validation，并只包含其 Kind 要求的 Contribution Section。
- 嵌套 Python 和 Frontend Metadata 不能重新定义 Plugin ID、Version、Runtime API 或 Revision。
- 生成的 Backend Code 使用第六阶段 Python SDK API，生成的 Client Code 使用第六阶段 Browser SDK API，且不进行 Low-Level Protocol Construction。
- Backend Template 通过 Python Static Check 和无密钥 Test；Client Template 通过 TypeScript Check、无密钥 Test 和确定性 ESM Build。
- 生成的 Full-Stack Plugin 完成 Build 和 Validation，在 Assembled Host 中激活、交换 Typed RPC 和 Event，并在 Disable 时移除两端 Contribution。
- Existing Destination、Symlink Destination、Invalid Input、缺失 Client Build 和 Mid-Generation Failure 都不会留下 Partial Plugin，也不会修改 User-Owned File。
- Determinism Test 验证相同规范化输入得到相同 Path 和 Byte。

## 排除项

远程 Registry、Package Search、Package Installation、Dependency Installation、Lockfile 更新、Signing、Provenance、Trust Policy、不受信任代码隔离、Publication、Upgrade Migration 和 IDE Integration 不属于第七阶段。Custom Template Repository 和 Interactive Prompting 也被排除；增加其他模板需要后续规范。
