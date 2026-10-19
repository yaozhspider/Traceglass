# Traceglass 文档导航

项目安装和首次启动从 [项目 README](../README.md) 开始；理解实现从 [系统设计](DESIGN.md) 开始。

## 当前设计

以下文档以 2026-10-19 的源码基线 `a75bd45` 为依据，描述已有实现。每篇列出实现入口、约束和未覆盖能力，后续开发应同步更新对应章节。

| 文档 | 内容 |
| --- | --- |
| [系统设计](DESIGN.md) | 目标、总体架构、组件职责、部署边界 |
| [执行与协作](design/EXECUTION.md) | 会话、任务、团队、权限、暂停、继续与失败处理 |
| [数据与证据](design/DATA_MODEL.md) | 数据库、对象关系、文件映射、证据引用及恢复边界 |
| [工具与 Skill 接入](design/INTEGRATIONS.md) | 工具发现、REA 生命周期、Skill 路由、Windows 作业 |
| [接口与前端](design/API_AND_FRONTEND.md) | HTTP 接口、认证、SSE、工作台状态及附件流程 |
| [部署与安全](design/DEPLOYMENT_AND_SECURITY.md) | 配置、隔离、单进程部署、备份、故障排查 |
| [验证与设计决策](design/VALIDATION_AND_DECISIONS.md) | 测试映射、验收场景、关键取舍及待完善事项 |

## 配置与专项说明

| 主题 | 文档 |
| --- | --- |
| 账号、个人模型及 TLS | [账号与模型](ACCOUNTS_AND_MODELS.md) |
| REA 构建、部署与连接配额 | [REA 集成](REA_INTEGRATION.md) |
| Skill 来源、加载与迁移 | [Skills 与工具](SKILLS_AND_TOOLS.md)、[Skill 整理](SKILL_ORGANIZATION.md) |
| 工具选择与能力重叠 | [工具目录](TOOL_CATALOG.md)、[REA 路由](REA_TOOL_SELECTION.md)、[入口审计](TOOL_DEDUPLICATION.md) |
| Windows 静态工具与动态调试 | [Windows 来宾工具](WINDOWS_GUEST_TOOLS.md)、[配置示例](../config/windows.example.json) |
| 脱壳工具 | [NoVmp](NOVMP.md) |
| 证据覆盖与分析约束 | [分析质量](ANALYSIS_QUALITY.md) |
| 事件与历史 | [原生会话与事件](TRACE_FORMAT.md) |
| 评测 | [评测说明](EVALUATION.md) |

## 历史方案与验证记录

`superpowers/specs/`、`superpowers/plans/` 及带日期的功能文档保留设计过程和当时的验证结果。它们不能替代当前设计，也不能作为某个部署此刻已启用相应能力的证明。

- [早期 Agent 设计](superpowers/specs/2026-09-24-traceglass-agent-design.md) 已被原生 AgentScope 路线替代，其中的自定义 Plan-Execute 和永久 TraceEvent 不是当前实现。
- [原生运行时迁移](superpowers/plans/2026-09-28-native-agentscope.md) 解释执行框架取舍。
- [工作台交互](2026-10-08-analysis-workbench.md)、[权限档位](2026-10-08-execution-permissions.md)、[暂停与继续](2026-10-08-pause-and-continuation.md) 保留相应功能细节。

新增能力时，更新当前设计、接口或数据契约、验证范围和配置说明；部署凭据、个人配置、运行数据及分析镜像继续放在源码仓库外。
