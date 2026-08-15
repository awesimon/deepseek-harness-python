# Browser Chat 与 DeepSeek-Compatible API 规范

[English](chat-product-surface.md) | 中文

状态：Phase 13 规范

## 范围

Host 从现有 Browser Bridge Bootstrap Page 提供浏览器聊天界面。页面保留 TypeScript Cordis Bridge Connection，并在 Host 的公开 Chat Completions API 之上增加带 Session 感知的聊天展示层。

Host 提供 `POST /chat/completions` 和 `POST /v1/chat/completions`，遵循 DeepSeek/OpenAI Chat Completions 的 Request 和 Response 约定。浏览器发送 `model`、`messages` 和 `stream`；Host 将最后一个非空 User Message 映射到当前 Session 的 Agent Invocation，并返回已配置的精确 Model。

## 行为

- 页面通过经过 Escape 的 Bootstrap Metadata 获取当前 `session_id` 和配置的 Model。
- 页面加载和刷新时读取 `GET /api/v1/sessions/{session_id}`，渲染 User、Assistant、Tool 和 Step Error Transcript Entry。
- 提交消息会先乐观追加到页面，再发送 Chat Completions Request，并追加已提交的 Assistant Response。
- 非 Streaming Request 返回包含一个终止 Choice 的 `chat.completion` Object。
- Streaming Request 返回合法的 `chat.completion.chunk` SSE Record，随后返回 `data: [DONE]`；当前 Agent Response 以一个 Content Chunk 和一个终止 Chunk 发出。
- Route、Provider 和 Protocol Failure 保留结构化 HTTP Status 和 Error Code。
- 页面支持 Desktop 和窄 Mobile Viewport，保留键盘提交，并为 Composer 和操作提供可访问 Label。

## 验收

- Browser Build、TypeScript Check 和 Chat API Test 通过。
- Host Test 证明两个 Chat Completions Path、JSON Output、SSE Output 和 Route Unavailable Error。
- 真实 DeepSeek-Compatible Endpoint 可以完成一次由浏览器发起的提问，且 API Key 不会暴露给浏览器。

## 排除项

内部 Agent Loop 的逐 Token 转发、无状态多对话路由、请求传入的 System Prompt 或 Tool Definition、Usage 统计、Authentication 和 Remote Deployment Policy 留待后续阶段。
