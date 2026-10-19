# Aicube-Traceglass

Traceglass 是面向逆向工程与恶意软件分析的 AI 工作台。它将样本管理、模型对话、工具调用、团队协作和证据归档整合到同一个界面，帮助分析人员追踪结论及其依据。

项目使用 **AgentScope 2.0.6** 作为执行框架，后端为 Python / FastAPI，前端为 React / TypeScript / Vite。REA 发布版本锁定为 **6.3.0**，分析引擎、Windows 调试环境和其他 MCP 服务按部署配置接入。

设计与开发文档见 [文档导航](docs/README.md) 和 [系统设计](docs/DESIGN.md)，包括执行协作、数据证据、工具接入、API、部署安全及验证边界。

## 主要功能

- **分析工作区**：创建和搜索工作区，上传、选择样本，浏览文件，查看文本及十六进制预览，下载分析产物。
- **可追踪的分析过程**：流式展示模型回复、工具输入与结果、任务清单和子会话；支持暂停、继续和历史恢复。
- **团队协作**：主分析 Agent 按任务创建专项成员，通过原生 Task / Team 工具分工和复核；每个团队最多 8 个成员，不含主分析 Agent。
- **REA 集成**：按需发现和激活工具，使用独立 MCP 连接执行分析；支持连接配额、空闲回收、工作流查询及证据归档。
- **专项分析 Skill**：内置 19 个技能包，覆盖通用逆向、恶意软件、漏洞、协议、加密与混淆、脱壳、JavaScript 和浏览器扩展等方向。
- **证据管理**：按账号和工作区保存分析回执、引用及工具失败记录，通过 `AnalysisReview` 检查结论的证据引用。
- **账号与模型**：可选多用户注册和登录；各账号管理自己的工作区及 OpenAI Chat Completions 兼容模型凭据。
- **Windows 动态调试**：可选通过 VMware Guest Operations 接入专用 Windows 虚拟机，使用固定工具配置及 x64dbg / x32dbg 自动化接口。

技能描述、工具注册、服务连通和实际执行能力分别核验。安装 Skill 不会自动安装它引用的外部工具。

## 环境要求

| 依赖 | 要求与用途 |
| --- | --- |
| Python | 3.11 及以上；建议使用独立虚拟环境 |
| Node.js | 建议 22.18 及以上，用于前端开发与构建 |
| 模型服务 | 分析任务需要支持流式响应及工具调用的 OpenAI 兼容接口 |
| Docker | Docker 工作区及 REA 容器需要；本地界面开发可不使用 |
| 分析镜像 / REA kit | 单独准备并配置，不随源码仓库分发 |
| Windows / VMware 环境 | 可选；仅 Windows 来宾工具与动态调试需要 |

## 本地启动

### 1. 获取项目并安装后端

```sh
git clone git@gitlab.asiainfo-sec.com:LLMSP/aicube-traceglass.git
cd aicube-traceglass
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
```

编辑 `.env`，先使用本地工作区启动开发环境：

```dotenv
TRACEGLASS_WORKSPACE_BACKEND=local
TRACEGLASS_DATA_DIR=traceglass-data
LLM_MODEL=your-model-name
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://your-model-service.example/v1
```

应用默认读取当前目录的 `.env`，进程环境变量优先。可通过 `TRACEGLASS_ENV_FILE` 指定其他配置文件。未配置模型时仍可启动服务和查看已保存内容，实际分析需要补齐模型配置。

```sh
traceglass
```

后端默认监听 `http://127.0.0.1:8000`。也可以显式指定地址和端口：

```sh
python -m uvicorn traceglass.api.app:create_app --factory --host 127.0.0.1 --port 8002
```

### 2. 启动前端

在另一个终端执行：

```sh
cd aicube-traceglass/web
npm ci
TRACEGLASS_API_TARGET=http://127.0.0.1:8000 npm run dev -- --host 127.0.0.1 --port 5177 --strictPort
```

打开 `http://127.0.0.1:5177`。若后端使用 8002，需同步修改 `TRACEGLASS_API_TARGET`。

Vite 将 `/api` 请求代理到后端，并移除此前缀，支持流式事件。省略端口参数时，Vite 默认使用 5173。

### 3. 开始分析

创建工作区，上传并选择样本，输入分析目标。工作台展示工具回执、任务进度和分析结果。没有接入的引擎或工具需要先按下面的部署文档配置。

`local` 工作区仅提供文件目录划分，不能隔离进程执行。执行不可信样本前应配置专用分析环境。

## 分析环境配置

### Docker 工作区与旧工具库

设置 `TRACEGLASS_WORKSPACE_BACKEND=docker`，并将 `TRACEGLASS_SANDBOX_IMAGE` 指向已准备好的分析镜像。

如需复用现有 reverse 项目的工具与技能，可设置 `TRACEGLASS_LEGACY_PROJECT=/absolute/path/to/reverse`。项目只读取旧库，使用桥接后的领域工具，不运行旧库的 Agent 执行引擎。已有基础镜像时，可构建适配镜像：

```sh
python scripts/build_analysis_image.py --legacy-project /absolute/path/to/reverse
```

脚本依赖已有的基础镜像，详细前置条件、MCP 配置及可选 IDA 接入见 [Skills 与工具](docs/SKILLS_AND_TOOLS.md)。

### REA

REA 通过独立容器 kit 接入。kit 包含镜像清单及与镜像绑定的工具元数据；构建需要已有的基础引擎 kit 和镜像：

```sh
python scripts/build_rea_image.py \
  --base-kit /absolute/path/to/rea-container-kit \
  --output traceglass-data/rea-kits/6.3.0-pwntools
```

构建并验证后，将 `TRACEGLASS_REA_KIT` 设置为生成目录的绝对路径，再重启后端。工具目录可查询不等于引擎已连接，首次实际调用时才建立相应 Agent 的连接。

新任务先评估 REA 的适用性，再按样本格式和证据需求选择工具。纯文档、普通源码阅读和已有报告解析可使用对应工具。完整构建、连接生命周期及验证方法见 [REA 集成](docs/REA_INTEGRATION.md)，工具选择见 [REA 工具路由](docs/REA_TOOL_SELECTION.md)。

### Windows 来宾工具

安装可选依赖并从示例准备私有配置：

```sh
pip install -e '.[windows]'
```

将 [Windows 配置示例](config/windows.example.json) 复制到仓库外的私有目录，填写 VMware 主机、虚拟机、工具路径和授权账号。密码通过配置引用的环境变量提供，再设置 `TRACEGLASS_WINDOWS_CONFIG=/absolute/private/windows.json`。

样本执行默认关闭。动态调试需要管理员配置执行权限、固定基线快照、匹配的调试器插件和客户端，并满足任务授权及隔离条件。具体能力和回收流程见 [Windows 来宾工具](docs/WINDOWS_GUEST_TOOLS.md)；脱壳工具说明见 [NoVmp](docs/NOVMP.md)。

## 多用户账号与个人模型

多用户模式要求 Docker 工作区，并预先准备分析镜像。示例配置：

```dotenv
TRACEGLASS_AUTH_ENABLED=true
TRACEGLASS_WORKSPACE_BACKEND=docker
TRACEGLASS_ALLOWED_ORIGINS=http://127.0.0.1:5177,http://localhost:5177
TRACEGLASS_SECURE_COOKIES=false
```

启动后在页面注册账号，并在“模型设置”中填写 API 地址、模型名称和 API Key。个人账号使用自己的模型凭据，不继承部署者的模型 Key。

亚信安全接口可填写 `https://llm.asiainfo-sec.com/v1`，模型为 `deepseek-v4-pro`；页面也接受完整的 `/v1/chat/completions` 地址并统一规范化。API Key 加密保存在本地数据目录中，不随源码分发。

对外部署须配置 HTTPS、安全 Cookie、明确的允许来源和受控反向代理，使用单个 API worker。账号隔离、模型地址允许列表、私有 CA、备份及当前限制见 [账号与模型配置](docs/ACCOUNTS_AND_MODELS.md)。

## Skill 组织

`traceglass-analysis` 提供统一任务入口，`rea-analysis` 适配本项目的 REA 能力；专项方法、平台知识、引擎操作和报告技能按需加载。

来源优先级为：旧库默认技能 → 项目内置技能 → `TRACEGLASS_SKILL_ROOTS` 显式指定的技能。新工作区安装当前版本；既有工作区的同名技能不会仅因重启而覆盖，需单独审核迁移。

- [Skill 职责与整理说明](docs/SKILL_ORGANIZATION.md)
- [任务路由与技能组合](src/traceglass/resources/skills/traceglass-analysis/references/task-routing.md)
- [工具目录](docs/TOOL_CATALOG.md)

## 项目结构

```text
src/traceglass/
  api/                # FastAPI 路由及原生服务组合
  accounts/           # 账号认证、个人模型及凭据加密
  agents.py           # 主分析 Agent 和成员模板
  workspaces/         # 工作区、样本文件和路径映射
  integrations/       # REA、旧工具桥接、工具发现及 Skill 管理
  tools/              # 分析、任务、证据及复核工具
  evidence/           # 证据归档与引用查询
  windows/            # Windows Guest Operations 与调试适配
  evaluation/         # 回放、静态分析和产品编排评测
  resources/skills/   # 内置 Skill、参考资料及脚本
web/                  # React 工作台
tests/                # 单元、契约与集成测试
scripts/              # 镜像构建、检查及维护工具
config/               # 可提交的配置示例
docs/                 # 部署、能力边界及设计文档
evals/                # 评测用例和静态测试夹具源码
```

## 验证与评测

```sh
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q src
cd web
npm test
npm run build
```

自动化测试覆盖原生执行、账号与证据隔离、工具发现、Skill 路由、Windows 工具契约等。脚本化模型和离线适配测试不能替代实际模型、Docker 引擎或 Windows 样本调试验收。

评测入口、静态夹具构建和结果边界见 [评测说明](docs/EVALUATION.md)，分析质量约束见 [分析质量](docs/ANALYSIS_QUALITY.md)。

## 数据与部署边界

- `.env`、本地 TLS 配置、运行数据、数据库、密钥、依赖目录及构建产物不提交到 Git；分析镜像和商业工具单独部署。
- 备份需要同时保留数据库、工作区文件及数据目录中的 `credentials.key`。丢失主密钥会导致已有模型凭据无法解密。
- 未开启认证的本地模式仅适合受信任的本机开发。账号模式仍需部署层容量、并发和访问控制，不应直接开放开发服务器。
- 消息总线、团队锁及 REA 连接配额按进程维护，目前使用单进程部署；不能据此声明跨进程的全局并发控制。
- Windows 共用来宾账号及系统状态，任务目录和租约不能代替强隔离。恶意样本需在专用、受控的分析环境中执行。
- 证据引用检查验证记录和引用关系，分析结论仍需结合原始证据复核。工具失败、未覆盖路径和未完成操作应保留在报告中。
