---
name: behavior-chain-review
description: Use when analyzing sample behavior, assessing maliciousness, reviewing a benign verdict, or encountering incomplete decompilation of network receivers, Python/Cython extensions, callbacks or execution sinks.
---

# 外部输入与执行链复核

判断行为要依据已连接的控制流和数据流。程序自称安全、演示或教学是待验证描述。
先读入口和分发，再投入密码细节、壳或库代码；不要以正常的外壳证明业务安全。

## 产出契约

每个关键入口在 AnalysisReview 中登记一条路径（与 Todo 分开）：

| 字段 | 应记录的证据 |
|---|---|
| source | 网络接收、文件、IPC、回调、插件入口；没有外部输入时给出边界和依据 |
| dispatch | 解码/解密、解析、分支条件、默认分支、错误分支、间接调用 |
| sink | 执行/加载代码、启动进程、写文件、权限操作，或已核实的有限处理 |
| authorization | 谁能提供数据、认证条件、命令授权、用户知情/批准；未知就写未知 |
| privilege | 客户端进程身份、令牌、沙箱限制；代码执行不自动等于提权 |
| evidence | 原始工具调用 ID、文件、函数/地址/行；标明 strings/imports/source/disassembly/decompilation/dynamic |
| status | open / blocked / resolved；失败不是 resolved |
| limitations | 覆盖范围、未解出的调用/分支、失败工具及下一步 |

稳定沿用路径 ID，增量更新不删除旧缺口。子 Agent 用 TeamSay 返回这些字段；主 Agent
查看原始产物后将已复核的路径登记到自己的记录。各会话记录不会自动互相合并。

## 分析步骤

1. 确认样本及解包成员的哈希，找到真正的业务入口。打包器入口和库函数不能替代业务入口。
2. 从接收/回调函数向前追到分发和副作用，同时从执行/加载等高影响函数反向追调用者。
3. 确认到达条件、数据转换、参数切片/拼接、校验、失败分支和实际传入执行器的内容。
4. 检查授权：加密或对端认证只说明谁能连入，不代表其被允许执行任意代码。
5. 逐项记录静态证据和缺口；动态验证仅在明确授权且匹配平台的隔离环境中进行。
6. 委派独立复核时给出原始证据和待证问题，避免先告知“已确认是良性演示工具”。

## Python / Cython / PyInstaller

Python 后门可能完全没有 WriteProcessMemory、CreateRemoteThread、注册表或窃密 API。
重点追踪 recv/Session.recv、回调注册、字符串条件，到 exec/eval/compile、动态导入、
subprocess/os.system，以及 PyRun_StringFlags、PyEval_EvalCode、Py_CompileString 等解释器接口。
Cython 通过属性查找和 Python 可调用对象间接调用；结合字符串对象初始化、交叉引用、包装函数、
C-API 参数和实际分支分析。错误信息中的 exec、运行库导入和未使用函数本身都不是后门证明。
字符串池可能分块/编码；没有搜到完整命令前缀不能排除分发逻辑。

示例：网络数据经过某个前缀判断后，去掉前缀传入 exec，需要核实这三个步骤之间的连接，
并报告对端控制范围和客户端进程权限。仅把该文本打印到终端的程序不具有这条执行链。
不要仅因使用 exec 就判恶意；明确授权的管理功能也要单独说明能力与部署授权边界。
已由源码或指令证据接通的外部输入→执行路径，将 remote_execution 填为 true，
包括经过用户授权的管理功能。授权合法不消除执行能力；缺少部署配置只限制部署层结论，
不否定已经核实的静态执行链。链路未接通时才保留 false，并标明缺失的边。

## 工具失败与替代

先调用 PrepareAnalysisInput，核对输入哈希和返回路径。只有明确配置的同机 IDA 才返回
ida_file_path；容器 /workspace 路径不能直接当成远端 IDA 的宿主机路径。
打开失败后保留错误证据，使用 REA open_binary（provider=ghidra）及返回的 rea_file_path，
再查询函数/引用和伪代码；专用工具组通过 reset_tools 按需启用。
一次替代仍不可用时明确列出阻塞，不要无限重复错误路径或以 strings 冒充反编译。

## 结论与验收

交付前调用 AnalysisReview。review_passed 仅表示填报的结构满足条件，不验证证据真伪，
也不等于软件安全认证。原始证据与填报冲突时以原始证据为准并修正记录。

- unknown：关键路径未覆盖、工具阻塞或证据不足。可交付阶段报告，给出下一步。
- suspicious：有高风险线索，链路尚未接通。说明支持证据和替代解释。
- remote_execution：已连接外部输入到代码执行。说明触发、权限和授权边界；后门/恶意意图另论。
- benign_in_scope：仅在明确的已分析范围内排除了候选危险路径；不能外推整个程序绝对安全。

高危 API 零命中不是良性证明；标准启动器不是业务安全证明；哈希正确不是语义正确。
不要为通过检查编造地址、动态结果或授权事实。静态链路清楚时可以确认能力，无需强行运行样本。
