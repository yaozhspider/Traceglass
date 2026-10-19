# 接口与前端设计

本页列出产品使用的接口契约及状态呈现规则。接口以当前源码及锁定版本 AgentScope 的 OpenAPI 为准；总体入口见 [系统设计](../DESIGN.md)。

## 请求边界

以下均为后端路径。前端默认访问 `/api`；Vite 开发代理移除此路径前缀，转发到 `TRACEGLASS_API_TARGET`，默认 `http://127.0.0.1:8000`。构建时可通过 `VITE_TRACEGLASS_API` 改变前端请求基址。

账号模式使用同源 HttpOnly `traceglass_session` Cookie，写请求携带 `X-Traceglass-CSRF`；登录和注册要求可信 Origin。前端把 CSRF 放在内存中，不将密码、Key 或登录令牌存入 localStorage。`X-User-ID` 在账号模式被服务端身份覆盖，不能用它选择其他用户。

部分原生路由虽出现在 OpenAPI 中，但账号模式会通过入口允许列表关闭。例如原生任意 Agent / Skill / MCP 管理、原生会话创建和删除不对账号用户开放。产品通过受控工作区接口创建会话，暂停使用产品接口。

## 产品接口

| 方法 | 后端路径 | 输入和行为 |
| --- | --- | --- |
| GET | `/auth/session` | 当前认证模式、身份和 CSRF 信息 |
| POST | `/auth/register` | 用户名与密码；创建账号并登录 |
| POST | `/auth/login` | 用户名与密码；创建登录会话 |
| POST | `/auth/logout` | 撤销当前登录 |
| GET | `/product/config` | 当前 Agent、非秘密模型配置、编排上限和集成配置 |
| GET / PUT | `/product/model` | 查询或保存当前用户默认模型；不回显 Key |
| POST | `/product/model/discover` | 在允许的模型地址上发现模型列表 |
| POST | `/product/model/test` | 流式工具调用探测，会产生真实模型请求 |
| GET / POST | `/product/workspaces` | 列表或创建；创建输入为 name 和可选 description |
| GET | `/product/workspaces/{workspace_id}` | 工作区、样本及主会话目录 |
| POST | `/product/workspaces/{workspace_id}/sessions` | name、可选主 sample_id、模型配置及 permission_mode |
| POST | `/product/workspaces/{workspace_id}/samples` | multipart 的 `file` 字段；返回样本元数据 |
| DELETE | `/product/workspaces/{workspace_id}/samples/{sample_id}` | 空闲检查后删除样本文件及目录项 |
| GET | `/product/workspaces/{workspace_id}/files` | path 为工作区相对目录，默认根目录 |
| GET | `/product/workspaces/{workspace_id}/preview` | 必填 path，返回有界文本或十六进制预览 |
| GET | `/product/workspaces/{workspace_id}/download` | 必填 path，以附件方式下载原文件 |
| GET | `/product/runtime` | 必填 workspace_id；主会话、成员与集成状态 |
| PATCH | `/product/sessions/{session_id}/permission` | 必填 agent_id；body 为 default 或 bypass 档位 |
| POST | `/product/sessions/{session_id}/interrupt` | 必填 agent_id；scope 为 session 或 team |

证据接口统一位于 `/product/workspaces/{workspace_id}` 下：

| 方法 | 后缀 | 说明 |
| --- | --- | --- |
| GET | `/analysis-evidence` | sample_sha256、kind、cursor、limit；kind 为 evidence、unknown 或 tool_call |
| GET | `/analysis-evidence/{record_id}` | 返回作用域内归档记录与原件 |
| GET | `/analysis-evidence/{record_id}/attachments/{attachment_id}` | 校验归属和哈希后下载附件 |

`rea-evidence` 的列表和详情路由作为历史兼容别名保留。证据列表 limit 默认 30、上限 100，使用归档序号 cursor 分页；不启动外部引擎。

实现入口：[工作区路由](../../src/traceglass/api/workspaces.py)、[运行状态路由](../../src/traceglass/api/runtime.py)、[证据路由](../../src/traceglass/api/evidence.py)、[认证](../../src/traceglass/accounts/routes.py) 与 [模型设置](../../src/traceglass/accounts/models.py)。

## 原生会话接口

| 方法 | 后端路径 | 产品用途 |
| --- | --- | --- |
| GET | `/sessions/?agent_id=...` | 获取原生会话、任务、团队等快照 |
| GET | `/sessions/{session_id}/messages?agent_id=...` | 以 before / offset / limit 分页读取持久消息 |
| GET | `/sessions/{session_id}/status?agent_id=...` | 查询原生状态 |
| GET | `/sessions/{session_id}/stream?agent_id=...` | 原生重放缓冲及实时 SSE |
| PATCH | `/sessions/{session_id}?agent_id=...` | 受限字段的原生更新；产品权限选择优先走产品接口 |
| POST | `/chat/` | 提交用户消息、权限确认事件或继续请求 |

继续请求使用同一 agent_id 与 session_id，例如：

```json
{"agent_id":"<current-agent-id>","session_id":"<current-session-id>","input":null}
```

确认请求沿用原生 `UserConfirmResultEvent`，携带真实 reply_id、原始 tool_call 和确认结果，不根据工具名称重新构造待批准调用。消息及事件结构不复制成项目自定义的第二套执行协议。

## 错误语义

| 状态 | 常见情况 | 前端行为 |
| --- | --- | --- |
| 400 / 422 | 路径、参数或请求结构错误 | 保留输入并展示可理解的错误；校验响应不回显密码或 Key |
| 401 | 未登录或会话过期 | 清理当前身份视图并进入认证流程 |
| 403 | CSRF、Origin 或受限字段不满足 | 展示拒绝原因，不自动修改请求绕过校验 |
| 404 | 不存在、不可访问或未开放的资源 | 不将错误显示为空目录或成功记录 |
| 409 | 正在运行、文件变化、尚未完成暂停等冲突 | 刷新真实状态后由用户重试 |
| 413 | 请求或上传超限 | 对应附件保留失败状态，支持移除或更换 |
| 429 | 登录/注册限流 | 展示稍后重试信息，遵循 Retry-After，不循环提交 |

HTTP 成功只表示接口调用完成；工具结果、归档状态和外部 job 终态仍需检查。API 没有覆盖所有写操作的全局幂等键协议，连接中断后不能无条件重复发起有副作用请求。

## 前端模块与状态

| 模块 | 职责 |
| --- | --- |
| features/account/ | 认证入口、个人模型、CSRF 和身份失效处理 |
| useWorkbench.ts | 工作区、路由、会话数据和提交协调 |
| WorkbenchShell.tsx | 页面布局、面板与输入交互 |
| attachments.ts / useComposerAttachments.ts | 多附件排队上传、取消、重试和元数据上下文 |
| nativeApi.ts | 原生 HTTP 请求、历史分页和 SSE 读取 |
| native.ts | 按原生身份合并消息及实时事件 |
| timeline.ts / SessionTimeline.tsx | 工具配对、状态呈现、授权和继续入口 |
| TeamPanel.tsx | 按需读取真实成员历史和运行状态 |
| RecordInspector.tsx | 证据原件、回执及原始数据查看 |

前端源码位于 [web/src/features/](../../web/src/features/)。路由/账号/会话切换中止旧请求，迟到结果不能覆盖新上下文。跨标签页仅广播账号变化通知，不存储身份秘密。

## 附件与样本切换

已有样本和新上传文件可同时作为附件；上传按队列处理，每个附件独立记录进度、失败和 ready 状态。失败项可单独重试，移除正在上传项会取消请求。全部附件 ready 后才构造输入，避免上传失败却让模型假定已读。

附件上下文包含真实路径、大小和哈希，并标明文件元数据与文档内容属于待分析数据。一个新选主样本在现有对话中按产品流程创建新分析，防止旧目标上下文误用；多附件仍属于同一轮问题。

## SSE、历史恢复与渲染

SSE 解析处理跨数据块边界、CRLF、多行 data 和心跳；按事件、reply、block 和工具调用身份去重合并。工具输入与结果按调用 ID 配对，不把相邻工具或缺少 ID 的记录合并。

历史分页恢复以持久消息为依据；实时 delta 与快照合并需避免旧快照覆盖较新事件。原生总线重放是有界内存缓冲，不能当作永久逐 token 审计。刷新后待确认调用从原生消息恢复，确认被持久化后才清除；前端不自动重发用户问题。

UI 分别显示错误、等待批准、中断与未完成；协作卡锚定实际委派调用。`idle` 仅表示本轮空闲，不表示所有 Task 或证据覆盖已完成。

Markdown 不执行原始 HTML，不自动加载报告中的远程图片，工作区链接经过路径解析限制。文件名、样本内容、模型输出和工具返回都不能成为前端执行代码或新的授权。
