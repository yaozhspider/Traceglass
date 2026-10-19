# 工具入口审计与按需展示

核对日期：2026-10-10。本次整理复用 AgentScope 的 Toolkit、ToolGroup、reset_tools 和权限流程。
没有删除分析引擎，也没有用统一参数包装器改写底层工具。

## 审计依据

- REA：`traceglass-data/rea-kits/6.3.0/tools-after.json` 的已保存发现清单，139 个不同名称。
- reverse：原项目 `TOOL_METHODS` 的 33 个方法，加上适配器注册的 8 个资源/证据方法，共 41 个不同名称。
- local 的 `inspect_file`、`extract_strings`、`write_report` 只在 local 模式注入；legacy reverse 配置要求 Docker。因此通常不会同时暴露这两套入口。
- 实时连接可能与已保存清单不同；模型应以本次 `SearchAnalysisTools` 返回的目录为准。

上述清单未发现同名重复。主要重叠是能力交叉，不是相同实现：

| 能力 | 默认入口 | 保留的其他入口与差异 |
| --- | --- | --- |
| 函数反编译 | REA `procedure_pseudo_code` | reverse `decompile_function` 使用 r2ghidra；引擎、参数和当前样本状态不同 |
| 汇编 | REA `procedure_assembly` | reverse `disassemble_function` 使用 radare2 |
| 函数查找 | REA `search_procedures` | `list_procedures` 是枚举；reverse `list_functions` 使用 radare2 分析 |
| 概览 | REA `binary_overview` | reverse `binary_metadata` 是 rabin2 元数据；`ghidra_summary` 是独立 headless 分析 |
| 字符串 | 原始提取与索引搜索分别保留 | reverse 提取 ASCII/UTF-16LE；REA 查询引擎索引；FLOSS 恢复的字符串另属专项证据 |
| IDA | 按任务启用直接 IDA | `ida_headless_call` 桥接具有不同路径、权限与会话语义，不能当成直接 IDA 的等价别名 |
| 文件与报告 | 原生 Read/Write 与存证工具分别保留 | 保存报告、证据、命令输出和验证报告并非普通文件写入的同义操作 |

## 默认展示

REA 连接时，reverse 从 41 个常驻入口收敛为 11 个常驻 + 30 个按需入口；工具总数不变。
REA 未连接时，9 个基础静态逆向入口保持常驻，此时 reverse 为 20 个常驻 + 21 个按需入口。

| 按需组 | 数量 | 用途 |
| --- | ---: | --- |
| reverse_native | 9 | 备用静态逆向、元数据、独立复核；REA 缺失时不隐藏这些工具 |
| reverse_dynamic | 7 | 已有动态报告、事件、进程树与静动态关联 |
| reverse_malware | 7 | DIE/YARA/FLOSS/capa/PE/IOC |
| reverse_crypto | 2 | 算法线索与实验归档 |
| reverse_rules | 2 | Sigma 验证与转换 |
| reverse_execution | 2 | 高级工具调用及实验归档 |
| reverse_ida_bridge | 1 | 原 reverse 的 IDA 桥接 |
| analysis_ida | 由当前服务决定 | 直接 IDA 工具，按需启用 |

REA 的已有分组不变。用户明确指定引擎、首选工具不支持或失败、需要复核时，仍可启用备用组。
按需展示只减少工具定义进入模型上下文的数量，不代表禁用或授予某种执行权限。

## 统一发现与去重规则

`SearchAnalysisTools` 是跨服务目录，返回来源、用途、组、实时激活状态、参数定义大小、相关工具及保留当前组的激活参数。
支持完整名称、短英文关键词和已登记能力的中文词；名称精确匹配优先。空查询分页浏览。
首选项仅表示接入层建议，不证明后端引擎能处理当前样本，也不表示相关工具的参数或结果可以互换。
`SearchReaTools` 保留兼容旧会话；新提示词推荐跨服务查询使用统一入口。

仅合并同一组内同一个工具对象/连接对象的重复引用。不同实现即使同名也不擅自删除，
在 `audit.name_conflicts` 中列出；实际同名解析仍沿用原生 Toolkit 的优先级。
审计还返回默认/按需工具数、能力重叠和已删除的重复引用数。
清单发现失败的服务在 `unavailable_servers` 中列出，保留其原始注册供框架重试，不把失败视为服务没有工具。

分组直接保留 MCPTool/Gateway 工具对象，保持连接、schema、超时和只读标记。
工具调用仍走 AgentScope 原生参数校验、权限检查和执行，不自动激活、不自动改写参数、不代替模型执行备用工具。
reverse/IDA 未激活误调用沿用结构化 `tool_group_inactive` 恢复提示。

## 实现与验证

- `src/traceglass/integrations/tool_policy.py`：显式分组与能力对应表。
- `src/traceglass/integrations/tool_catalog.py`：每个 Agent 的工具清单、重复引用清理和统一发现。
- `src/traceglass/integrations/rea_activation.py`：当前回复范围内的激活错误说明。
- 单元测试覆盖 REA 缺失回退、状态更新、重复应用、连接失败、名称冲突与权限拒绝。
- 集成测试用真实 stdio MCP 传输验证查询 → 激活 → 执行；固定模型响应测试原生 Agent 的错误恢复。

这些测试验证接入行为；尚未对真实模型的工具选择率和上下文费用做前后对照实验。
