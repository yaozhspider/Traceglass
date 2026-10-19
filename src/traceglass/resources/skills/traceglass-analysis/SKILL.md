---
name: traceglass-analysis
description: Use at the start of a Traceglass task involving samples, shipped applications, source review, vulnerability research, or supplied behavior reports, and when the target or investigation goal changes.
metadata:
  traceglass_revision: routing-v1
---

# Traceglass 任务入口

本 Skill 负责选路线；专项 Skill 负责分析方法，工具 Skill 负责调用细节。
每项任务先评估 REA 的相关能力，再决定实际工具；同一任务复用已有判断。

## 开始分析

1. 明确目标、用户问题、已有证据及本次是否允许执行。上传样本先核验哈希与格式；
   使用 reverse 的 `hash_file`/`identify_file` 或本地 `inspect_file`，参数以实际 schema 为准。
2. 读取 `rea-analysis` 选择格式路线。能力不明确时用 `SearchAnalysisTools` 按工具名或短关键词查询。
   判断结果写进计划或分析记录：`REA 适用能力 / 选用工具 / 前置条件或回退原因`。
   纯源码阅读、报告排版、哈希或已有日志解析也做适用性判断；通常直接使用对应工具，
   不启动 REA 引擎、不为了证明不适用运行诊断。
3. 搜到适用但未激活的工具，按返回的 `reset_tools` 参数保留仍需的组并激活，
   等成功后读取完整 schema 再调用。搜索结果不是分析结果；未尝试不写成工具失败。
   前置条件不满足、provider 不支持或实际失败时记录具体缺口，再用其他分析器或脚本。
4. 根据 [任务路由](references/task-routing.md) 选一个主要方法 Skill，按证据问题补平台或专题。
   仅加载本步需要的资料，已有正确流程不因换工具而重新建案。

## 工具与执行边界

REA 是 `mcp__rea__*`；reverse MCP 是 `mcp__reverse__*`，分别记录。
REA 按格式选择入口，JS/托管/运行时工具不套用原生二进制的打开流程。
查壳读取 `unpacking`；本地主沙箱 DIE 入口为 `mcp__reverse__detect_packer`，
按需激活 `reverse_malware`，`sample_path` 相对 `/workspace/samples`。

运行、调试、dump 或执行目标/harness 前读取 `sandbox-dynamic-debugging`，沿用当前任务已有授权。
Windows 原生 x86/x64 EXE 用已注册的 Windows 通道检查 `WindowsCapabilities`；
Linux 使用适用的已绑定 sandbox MCP。浏览器运行时还需适用的隔离浏览器/CDP 环境。
普通工作区、静态 REA 容器和宿主机不是未知样本的执行通道。
回收结果后再按当前任务的明确恢复授权处理 Windows 基线。

## 协作与交付

复杂任务先用 TaskList/TodoWrite 组织工作；复用返回的 task_id。仅在当前任务允许委派时
使用原生团队工具，主 Agent 复核成员原始证据后再收口；不共享引擎会话句柄。
证据引用、路径、完成状态和归档顺序统一遵循 [证据契约](references/evidence-contract.md)。
报告保存到工作区，专题报告章节按需交给 `docs-generator`；计划和成员断言不代替证据。

本包 references 可从工作区读取。外部 Skill 的 `../field-journal`、`../tool-index.md`
等未随包复制的资源，使用已注册的 `read_skill_resource` 按原 Skill 名和相对路径读取；
无法访问就记录缺口。历史先例和工具安装说明不是本次任务的授权或部署状态。
