# 验证范围与设计决策

本页记录当前架构的可验证契约和扩展边界。系统入口见 [系统设计](../DESIGN.md)，评测方法见 [评测说明](../EVALUATION.md)。

## 分层验证

| 层次 | 验证内容 | 代表测试 |
| --- | --- | --- |
| 工作区契约 | 所有权、主样本关联、上传/预览/下载、路径与大小限制 | tests/contract/test_product_workspaces.py |
| 账号与模型 | CSRF、越权、凭据加密、版本化、URL 与 TLS 策略 | tests/contract/test_accounts.py、test_personal_models.py；tests/unit/test_model_tls.py |
| 原生执行 | 真实工具、Task / Team、历史存储及成员会话 | tests/integration/test_native_execution.py |
| 暂停与权限 | 准备期中断、授权等待、继续、定向成员恢复和晚到消息 | tests/integration/test_pause_continue.py、test_execution_permissions.py、test_native_wakeup.py |
| 框架适配 | Docker 文件缓存、默认提示迁移和账号运行配置 | tests/integration/test_native_file_cache.py、test_prompt_migration.py、test_account_execution.py |
| 工具与 Skill | 目录、分组、延迟注册、来源优先级及迁移保护 | tests/integration/test_analysis_catalog.py、test_native_lazy_rea.py；tests/unit/test_skill_layout.py、test_skill_migration.py |
| REA 生命周期 | 有状态会话、配额、排队、回收、句柄重开和 workflow | tests/integration/test_rea_sessions.py、test_rea_lifecycle.py、test_rea_workflows.py |
| 证据与复核 | 失败归档、样本绑定、引用、结构标签及独立复核输入 | tests/unit/test_evidence_ledger.py、test_analysis_review.py；tests/integration/test_native_verifier.py |
| Windows | 参数、账号/工作区、未知状态、并发租约、收尾及恢复屏障 | tests/unit/test_windows_tools.py、test_windows_concurrency.py；tests/integration/test_windows_catalog.py |
| 前端投影 | SSE、工具配对、待授权、快照合并、附件和安全导航 | web/tests/ |

单元测试可替换 Docker / VMware 传输；原生集成测试使用真实 AgentScope 服务、存储及工具，但通常替换外部模型响应。因此不能从这些测试推断某个在线模型、provider 或来宾程序此刻可用。

## 检查命令与隔离

从项目根目录运行：

```sh
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q src
cd web
npm test
npm run build
```

测试应使用临时数据和受控传输；涉及环境加载的测试需隔离部署凭据，不能通过覆盖 `TRACEGLASS_ENV_FILE` 而改变正在验证的默认 `.env` 语义。真实引擎或 VM 检查使用明确的测试目录，先确认不会与活动分析争用资源。

## 验收场景

1. 创建工作区 → 上传无害夹具 → 核验哈希和实际路径 → 发起分析 → 查看工具原件 → 保存并重新读取报告 → 刷新恢复历史。
2. 两个账号分别设置模型和工作区；验证文件、会话、证据、暂停及模型凭据不能跨账号访问，伪造 X-User-ID 不改变身份。
3. 适用任务查询工具 → 激活分组 → 检查 schema → 成功调用；分别注入组未激活、provider 缺失、超时和容量错误，确认缺口不会被写成零命中或成功。
4. 创建协作任务并委派 → 暂停全部 → 接收迟到回报 → 明确恢复原成员；验证历史和 owner 保留，等待批准的调用不会自行执行。
5. 生成带样本绑定的调用归档 → 引用原件完成 AnalysisReview；依次验证错误样本、截断、失败调用、定位缺失和附件损坏不能通过严格引用。
6. 在已授权、已配置基线的 Windows 环境提交受控 EXE；确认创建、操作、关闭、回收及恢复分别有结果，unknown 不被重复启动，共享任务不被其他作业恢复覆盖。

最后一项是实机动态验收，离线测试与空调试器会话不能代替。DLL、驱动、ARM64、托管调试、完整进程转储及全量网络/注册表监控不在当前接口承诺中。

## 评测与统计

评测分别覆盖证据回放、真实静态分析和产品编排，报告 completed、invalid、timeout 和 ungraded。模型使用量、工具调用、错误、重复调用、REA 启动与占用以及代码/Skill/schema/镜像指纹分别记录。

真实工具调用次数与成功率按来源及实际回执统计：搜索到未调用的 REA 工具不算失败，reverse 与 REA 也不混为一类。提供方未返回的用量或内部重试不能补造。成本或质量比较需确认模型、参数、预算、样本、缓存和重试策略可比。

## 已采用的设计决策

| 决策 | 原因与代价 |
| --- | --- |
| 使用 AgentScope 原生执行与对象 | 避免两套调度与状态；部分兼容点依赖固定版本内部接口 |
| 产品目录与执行状态分开 | 工作区元数据轻量、原生历史可复用；跨库关系依赖服务校验 |
| 工作区共享文件、引擎会话独立 | 团队可共享报告且引擎状态不串用；成员需各自打开目标并消耗连接 |
| 工具分组按需激活 | 降低 schema 上下文体积；模型需完成发现、激活和读取实际 schema 的步骤 |
| 先评估 REA，按格式选择路径 | 复用分析能力，同时保留不适用原因与备用引擎；提示引导不是调用强制保证 |
| 归档原件与结构复核分层 | 原始来源可复查且失败可追踪；完整性不能替代语义证据审查 |
| Skill 默认不覆盖同名副本 | 保留用户修改；已有工作区需显式迁移，重启不会自动统一版本 |
| Windows 持久租约与恢复屏障 | 处理启动不确定和整机副作用；失败作业有时需要管理员介入恢复 |
| 单 API 进程与 SQLite | 适合当前工作台规模；不能直接增加 workers 声称全局配额和唤醒一致 |

## 尚未实现及扩展前提

| 方向 | 当前缺口 | 进入下一阶段前需具备的条件 |
| --- | --- | --- |
| 多进程 / 多节点 | 总线、运行注册、团队锁及 REA 配额进程内维护 | 共享状态与锁、可靠唤醒、资源所有权及故障接管测试 |
| 对外规模化服务 | 无按用户总容量、总并发、模型费用配额与完整管理能力 | 部署限额、审计、账号管理及独立沙箱宿主机验收 |
| 长期审计 | SSE 原始事件为有界内存数据 | 定义持久事件保留策略、敏感数据处理和存储预算 |
| 报告审批与发布 | 文件报告、证据和 Review 已有，独立审批/版本发布流程未实现 | 明确版本身份、审批对象、权限及历史不可变性契约 |
| 动态分析平台扩展 | 当前只支持限定的 Windows 调试和已有报告/通道 | 新目标类型的 worker、隔离、回收、未知状态及真实样本验收 |
| 自动语义判定 | 结构、引用和模型复核不保证结论真实 | 独立标注、可复现样本、反例与人工复核的质量基线 |

这些是后续建设边界，不代表已承诺的本轮代码变更。修改能力时同步更新设计、公开接口、运维说明和可验证的验收范围。
