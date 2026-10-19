---
name: rea-analysis
description: Use when evaluating REA capabilities for a Traceglass task, choosing a format-specific REA entry point, activating deferred tools, or recovering a failed REA session. Covers native, managed, JavaScript, archives, firmware and runtime evidence.
metadata:
  traceglass_revision: routing-v1
---

# REA 项目适配

每项新任务先判断 REA 是否提供相关证据能力。工具以本次实际注册的 `mcp__rea__*`
名称和 schema 为准；`mcp__reverse__*` 属于另一个服务。
官方 `reverse-engineer-anything` 保留上游方法；复杂任务按需读取其格式参考或
`ListReaWorkflows`/`GetReaWorkflow`。本项目由服务端管理部署，不在分析中运行 setup、doctor 或安装工具。

## 按目标选择入口

下表是检索线索；实际调用前确认工具已注册、已激活及前置条件。

| 目标/证据问题 | 检索入口 | 会话要求 |
| --- | --- | --- |
| 原生 EXE、DLL、ELF、Mach-O | `binary_session`、`open_binary`、`analyze_function` | 首次或目标变化才打开；复用匹配目标 |
| 提取后的 JS、浏览器扩展、Electron 源码树 | `analyze_javascript_application` | 显式目录路径；不先打开原生引擎 |
| 压缩 JS 恢复与静态数据流 | `recover_javascript_sources`、`trace_javascript_semantics`、`trace_application_feature` | 依实际 schema 提供源码、图或同连接证据；核实输出目录和工具依赖 |
| ZIP/APK/IPA/MSIX 等包体清单 | `open_binary`、`inspect_artifact` | 归档会话；不能把清单当代码语义分析 |
| Android 代码 | `inspect_android_package` | 显式包路径；核实 JADX/Java 等依赖 |
| .NET 托管代码 | `inspect_managed_artifact` | 显式制品路径；NativeAOT 改走原生路线 |
| 固件布局 | `inspect_firmware_regions`、`extract_firmware` | 核实 provider、提取目录是否可写；只读部署不强行提取 |
| 已有浏览器或 Node/Electron 运行时 | `list_browser_targets`、`list_javascript_runtime_targets`、`observe_javascript_runtime` | 需要实际可达端点和匹配目标；容器 loopback 不是宿主机 loopback |
| 授权的浏览器验证 | `capture_browser_scenario` | 核实隔离环境、浏览器/端点和动作范围；REA 不自带安全沙箱 |
| 完整源码阅读、文档排版、哈希、已有 CAPE/DDAN 报告 | 通常使用工作区、reverse 或报告解析器 | 记录不适用理由即可，不启动原生引擎 |

REA 在独立容器运行，`/workspace` 只读，默认无网络。工具出现在目录中不代表其
写入、浏览器连接或执行前提在这个部署中可满足；不可用时保留真实原因并继续可行路线。

## 发现到调用

`SearchAnalysisTools(query="工具名或短关键词")` → 若未激活则按结果 `reset_tools`
→ 等待成功并读取实际 schema → 补齐参数 → 调用目标工具 → 检查原始返回。
保留仍需的组，`reset_tools` 会替换全部可选组。一次宽泛检索不足时换精确名称或翻页。
搜到工具后明确选用、前置条件不足或不适用，不能直接用“没工具”跳过。

区分四种状态：已注册定义、已激活 schema、已连接服务、当前操作实际可用。
`deferred` 表示首次实际使用才连接；搜索和激活不启动分析，也不证明 provider 已就绪。
`tool_group_inactive` 按返回参数激活；不存在的工具重新发现；provider/路径/资源错误
保留证据后选择替代入口，不原样反复重试。避免用 Bash/Python 重写已有合适的语义工具。

## 原生引擎与证据

原生路线用 `PrepareAnalysisInput` 返回的 `rea_file_path`，通过 `binary_session` 核对可用引擎。
首次/会话失效用 `open_binary`，`provider_id` 以支持情况选择。Ghidra 可用
`binary_overview`；其他 provider 不一定实现它，直接用实际可用的函数/引用工具。
每个 Agent 独立连接和句柄，归档 ID 不能当引擎句柄；完成原生分析后可 `close_binary`。

保存原始输出、样本/成员哈希、证据 ID、位置与覆盖缺口，遵循 `traceglass-analysis`
的证据契约。静态图和成功反编译不等于已观察执行。
运行未知样本、调试或实验前读取 `sandbox-dynamic-debugging`；已有沙箱报告改用
`dynamic-malware-analysis`，不会因需要动态证据就重新运行样本。
