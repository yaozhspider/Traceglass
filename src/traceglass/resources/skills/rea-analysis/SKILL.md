---
name: rea-analysis
description: Use when REA MCP tools are available for Ghidra or Hopper decompilation, function discovery, cross references, or independent static verification of a binary.
---

# REA 分析

以本次会话实际发现的 `mcp__rea__*` 工具名称和 schema 为准。REA 在独立容器中运行，
与原生工具共享 `/workspace` 文件视图，但工作区对 REA 只读，且默认无网络。
主 Agent 和每个子 Agent 的当前二进制、分析会话与缓存彼此独立；不能使用其他成员的会话句柄。

1. 先调用 `binary_session` 确认自己的当前状态。首次使用、服务重启或会话失效后重新打开文件。
2. 用 `open_binary` 指定 `/workspace/samples/...` 或工作区内已提取文件的绝对路径，
   默认 `provider_id="ghidra"`；只有需要独立复核且可用时选择 Hopper。
3. 用 `binary_overview` 检查格式、架构和分析状态，身份哈希用原有 reverse 工具核验。
4. 根据具体证据问题查询函数、字符串和交叉引用；用 `search_procedures` 缩小范围，
   再用 `procedure_pseudo_code` 获取关键函数伪代码。参数以当前 schema 为准，不猜地址或签名。
5. 记录引擎、样本哈希、函数/地址、原始输出及反证，用原生 `Write` 或原有存证工具保存产物。
   不让 REA 写入只读样本目录，也不把容器内部缓存路径作为用户可下载的报告路径。
6. 当前二进制不用时调用 `close_binary` 释放分析资源；分析同一文件的成员也必须独立打开它。

Ghidra 和 Hopper 是静态反编译证据来源。Hopper 为演示版，工具存在不等于没有厂商限制。
工具报错、缺少函数、引擎超时不证明样本不可分析或没有恶意行为。
动态行为分析加载 `dynamic-malware-analysis`；受控执行验证加载 `sandbox-dynamic-debugging`；
算法、字符串解密和控制流混淆加载 `crypto-obfuscation-analysis`。
不要把成功反编译或算法命中写成已经运行验证。

## 按需启用工具

常用二进制工具默认可用，其余使用 AgentScope 原生 `reset_tools` 启用对应 `rea_*` 组。
该调用替换全部分组选择：只保留本步骤需要的组，完成后关闭，避免大型重建工具的定义占满上下文。
工具是否可用以当前 schema 和调用结果为准；不要一次启用所有组。
