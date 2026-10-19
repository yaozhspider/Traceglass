---
name: ghidra-reverse
description: Use when Ghidra-specific static analysis, headless scripting, decompilation or cross-reference verification is needed through an available REA provider or an explicitly configured Ghidra tool.
metadata:
  traceglass_revision: routing-v1
---

# Ghidra 操作

任务方法使用 `reverse-engineering`；先按 `rea-analysis` 检查已注册的 Ghidra provider。
本 Skill 补 Ghidra 调用与验证细节，不要求 IDA 先失败，不在分析任务中自举或安装 MCP。

1. 核对样本哈希、`PrepareAnalysisInput` 返回的 `rea_file_path` 与自己的 `binary_session`。
   引擎可用且目标未打开时 `open_binary(provider_id="ghidra")`，参数按实际 schema。
2. 用可用的 overview、函数、字符串与交叉引用定位问题；确认基址、架构和分析状态。
   相同会话已打开正确目标就复用，不反复导入。
3. 结合伪代码与汇编检查分支、参数、调用关系与数据类型，不从命名推断行为。
   缺函数或失败分支保留证据，必要时使用已配置的独立引擎交叉验证。
4. 仅在任务明确需要脚本/headless 且存在已核实路径时执行对应静态工具；
   不照搬旧端口、安装目录或 tool-index 的历史记录。参考命令不是部署事实。
5. 原始输出与位置按公共证据契约归档；结束原生会话时释放自己的目标。
   动态调试另走 `sandbox-dynamic-debugging`。

需要 Ghidra API/快捷操作时按需读 [ghidra-cheatsheet.md](references/ghidra-cheatsheet.md)。
