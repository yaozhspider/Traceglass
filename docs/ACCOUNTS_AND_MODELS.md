# 多用户账号与个人模型

本版本支持开放用户名/密码注册，每个账号拥有自己的工作区、上传文件、会话、团队、证据及模型凭据。执行仍由 AgentScope 2.0.6 管理。

## 本机测试

本次独立预览：前端 `http://127.0.0.1:5177`，后端 `127.0.0.1:8002`，数据目录 `traceglass-data-accounts`。原有 5175/5176 服务与历史数据没有迁移或重启。

进入页面点击“立即注册”；用户名 3–32 位 ASCII 字母、数字、下划线、点或短横线（首位不能为点/短横线），不区分大小写；密码至少 12 个字符。注册后直接进入自己的空工作区。

打开“模型设置”，选择服务商或填写 API 地址、模型名称、API Key。支持 OpenAI Chat Completions 兼容接口。预设包含 OpenAI、DeepSeek、亚信安全、百炼、智谱、Kimi、硅基流动、火山方舟、MiniMax。预设是填写辅助，模型名称和可用性以服务商账号为准。

亚信安全接口选择“亚信安全 / DeepSeek”，API 地址 `https://llm.asiainfo-sec.com/v1`，模型 `deepseek-v4-pro`。也可粘贴完整的 `https://llm.asiainfo-sec.com/v1/chat/completions`；保存、列表发现与连接测试会统一使用 `/v1` 基地址，避免重复拼接接口路径。Key 按个人凭据加密保存，不写入预设。

2026-10-16 已实测该网关的模型发现、普通对话、流式工具调用，以及原生 Agent 的「工具调用 → 结果回传 → 继续回答」完整循环。模型设置接口也已用隔离测试账号验证地址规范化、Key 加密存储和留空复用；本次接入不自动切换现有账号或分析任务的模型。

- 获取模型列表：调用 `/models`，不支持此接口的服务商可手填模型名。
- 测试连接：发送一条真实的流式工具调用请求，可能产生少量费用；返回流式和工具调用检查结果。
- 保存配置：只保存，不自动发送模型请求；新建分析使用此配置。既有分析保留原模型和版本化凭据。
- Key 保存后不回显；相同 API 地址留空可保留 Key，换地址必须重填。
- 每人使用自己的 Key，不会继承部署者 `.env` 中的模型凭据。

实际推理需要服务商同时支持流式响应和工具调用。已用脚本化服务响应验证原生执行链路与账号隔离，没有逐家验证在线模型。

## 启动配置

自建服务使用私有 CA 或通过公网映射访问内部 TLS 名称时，可以配置专用 TLS 信任：

```sh
TRACEGLASS_MODEL_ALLOWED_ORIGINS=https://182.44.7.227:8001
TRACEGLASS_MODEL_TLS_CONFIG=/absolute/path/model-tls.json
```

`model-tls.json` 由部署管理员维护，不接受网页用户提交：

```json
[
  {
    "origin": "https://182.44.7.227:8001",
    "ca_file": "182-44-7-227-8001.pem",
    "server_name": "10.0.0.54"
  }
]
```

`ca_file` 是已确认属于该服务的 PEM 信任证书（相对路径以 JSON 文件目录为准）；
`server_name` 必须真实存在于证书 SAN 中。只适用于明确加入允许列表的 HTTPS 主机和端口。
网络连接和 HTTP Host 仍指向公网地址，TLS 用管理员配置的证书名称校验。证书链、有效期和
名称校验保持开启；专用客户端拒绝转向其他地址，也不修改系统信任库或其他服务商的客户端。
该配置统一用于列表发现、测试连接及原生主/子 Agent 推理（包括同模型的上下文压缩及配置过的备用模型）。
证书更新后需核实并更新 PEM；配置修改后重启后端。不要把证书私钥放进此目录或配置。

在项目根目录启动 API：

```sh
TRACEGLASS_AUTH_ENABLED=true \
TRACEGLASS_WORKSPACE_BACKEND=docker \
TRACEGLASS_DATA_DIR="$PWD/traceglass-data-accounts" \
TRACEGLASS_ALLOWED_ORIGINS=http://127.0.0.1:5177,http://localhost:5177 \
TRACEGLASS_SECURE_COOKIES=false \
.venv/bin/python -m uvicorn traceglass.api.app:create_app --factory --host 127.0.0.1 --port 8002
```

另一个终端在 `web` 目录启动：

```sh
TRACEGLASS_API_TARGET=http://127.0.0.1:8002 npm run dev -- --host 127.0.0.1 --port 5177 --strictPort
```

按原部署设置 `TRACEGLASS_REA_KIT`、`TRACEGLASS_LEGACY_PROJECT` 和技能目录。多用户模式不会自动导入旧工作区；旧的本地数据和密钥不会自动归属某个新注册用户。

## 对外部署

构建静态前端 `cd web && npm run build`，反向代理同一域名的 `/api/` 到 API（转发时去掉 `/api` 前缀），根路径服务 `web/dist`，支持 SSE、长连接和文件上传。不要直接把 Vite 开发服务器暴露公网。

必须设置：

```sh
TRACEGLASS_AUTH_ENABLED=true
TRACEGLASS_WORKSPACE_BACKEND=docker
TRACEGLASS_ALLOWED_ORIGINS=https://your-domain.example
TRACEGLASS_SECURE_COOKIES=true
```

非本机 HTTP 来源或未启用安全 Cookie 的公网配置会拒绝启动。API 应只接受受控反向代理流量；仅信任该代理的转发头。使用单个 API worker，原生消息总线与运行注册表还未改为跨进程共享。

自定义服务商：`TRACEGLASS_MODEL_ALLOWED_HOSTS` 为逗号分隔的**完整主机名**，设置后替换默认列表，这些条目允许 HTTPS/443。自建服务的 IP 或其他端口使用 `TRACEGLASS_MODEL_ALLOWED_ORIGINS`，例如 `https://182.44.7.227:8001`；支持逗号分隔多个地址，不包含 `/v1` 路径。它额外放行精确的协议、主机和端口，不影响默认服务商；页面 API 地址仍填写完整的 `https://182.44.7.227:8001/v1`。不允许带用户名、密码、查询参数的 URL。加入列表表示管理员信任该模型服务，不能加入用户任意控制的 DNS 域名。HTTPS 仍校验证书；自签名或证书链不完整需要配置可信证书或管理员提供的 CA，加入地址列表不等于信任证书。测试/发现接口额外拒绝非公网 DNS 地址和重定向；它们不是原生推理 HTTP 客户端的全局网络代理。

## 隔离及存储

- 身份由服务端 HttpOnly、SameSite=Strict Cookie 解析，忽略请求伪造的 `X-User-ID`。写操作检查 CSRF；登录/注册检查明确的 Origin。
- Cookie 有效期 7 天，数据库只保存会话令牌摘要；退出登录撤销当前会话。登录/注册按 IP 和用户名限速。页面切换账号时清空当前界面与待发送输入，通知同源其他标签页刷新身份。
- 密码使用带随机 salt 的 scrypt 存储。个人模型 Key 使用 Fernet 加密后交给 AgentScope 存储；加密主密钥在数据目录 `credentials.key`，权限为 0600，不能放进任何用户工作区。
- 用户 Agent 与凭据 ID 全局唯一。工作区拥有者在读取缓存和文件前校验；会话消息、运行状态、暂停、聊天、下载与证据查询均使用认证身份。
- 多用户模式关闭原生任意 Agent/MCP/Skill/凭据管理入口，关闭共享宿主机 IDA 和自定义部署 MCP 入口；保留沙箱内 reverse 工具桥接及独立 REA 会话。
- 账号工作区容器运行时断网、无端口映射、移除 capabilities、禁止提权；单容器 2 CPU、4 GiB 内存、512 进程。模型请求在 API 服务端执行，不受沙箱断网影响。需要联网的动态验证应另配专用受控环境，当前页面不开放联网开关。
- REA 沿用自己的独立无网络容器；分析目录只读挂载。当前策略不是强敌对租户的虚拟机隔离保证。

备份整个数据目录，至少包括 `accounts.sqlite3`、`agentscope.sqlite3`、`credentials.key`、`product-workspaces.sqlite3`、`rea-evidence.sqlite3`、工作区文件。数据库和主密钥必须配套，丢失主密钥将无法使用已保存 Key。停机备份或使用 SQLite 一致性备份；不要把主密钥提交到 Git。已有本地明文凭据会兼容读取，新写入凭据加密，不宣称自动清理历史数据库页。

## 当前边界

这是可测试的账号版本，尚无邮箱验证、找回密码、账号删除、管理员后台、按用户总存储/总并发配额或计费。单文件上限 100 MiB、认证限流和单容器资源限制不等于完整的滥用防护。公开开放前需补充部署层总容量与并发限制，并在独立沙箱宿主机验证容器策略；恶意样本不应与其他生产业务共用宿主机。

本次测试：后端账号/CSRF/越权/证据隔离/加密/重启恢复/模型版本化，原生 Agent 接收各自模型凭据（替换模型传输与沙箱 I/O 的离线集成测试），前端传输测试与浏览器双账号测试。真实 Docker 执行未通过现场验证：本机 Docker 引擎查询超时，未重启以免影响其他任务。
