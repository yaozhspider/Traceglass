---
name: reverse-engineering
description: 用于理解编译后的程序、库、固件、APK、脚本或自定义虚拟机的实现逻辑。适合静态分析、反汇编、反编译、控制流恢复、算法识别和在授权沙箱中的动态验证。
license: MIT
metadata:
  traceglass_revision: routing-v1
  user-invocable: "false"
---

# 逆向工程

## 什么时候使用

当问题的核心是“这个程序是怎么工作的”，使用本 Skill。包括：恢复函数逻辑、
寻找校验/解密代码、分析混淆或加壳、理解协议和定位关键数据流。

如果只是漏洞利用、磁盘取证或单纯数学密码题，应改用更专门的 Skill。

## 外部输入与执行能力：优先核查

分诊发现网络接收、IPC、文件/消息解析、插件入口，或代码执行/进程启动线索时，先读取
[外部输入与执行路径](references/external-input-execution.md)。
在深入算法细节前，建立一行一个路径的检查表：**输入来源 → 解码/解密 → 条件与分派 →
最终操作及参数来源 → 证据位置 → 缺口/下一步**。从输入向前、从执行点向后交叉追踪。

- 输入处理、线程/回调和高影响操作的连接优先于 AES 轮表、日志和普通辅助函数；低排名不能跳过这些路径。
- 函数归属由注册表、调用者和数据流确认；编译器序言、Cython 错误字符串、库名不足以把执行调用归为初始化。
- 对执行能力分别写明静态支持、触发条件、动态状态、权限边界和用途是否已知。加密/认证、演示名称、无持久化都不能排除远程代码执行。
- 核心接收路径、分派、执行参数或间接调用未解时，摘要保留该缺口，不得给出全局“良性/无后门”的高置信判断。格式校验通过不代表行为覆盖完整。
- 用户质疑已有报告时，从原始指令重新核查争议路径；报告和模型笔记不是相互独立的证据。新证据推翻旧归因时明确撤回旧结论。

## 子 Skill 路由

本 Skill 提供通用逆向方法；任务入口与工具选择由 `traceglass-analysis`、`rea-analysis`
统一。不一次加载所有子 Skill，只在任务确实需要时加载：

| 情况 | 加载的子 Skill | 用途 |
|---|---|---|
| 通用反编译、调用/数据流、制品分析 | `rea-analysis` | 按目标格式选择实际注册的 REA 能力 |
| 需要 IDA 反编译、函数图或 IDA MCP | `ida-reverse` | IDA 数据库、反编译和 IDA 专用工具 |
| 需要 radare2 命令行、`pdg` 或 r2 CFG | `radare2` | r2/rabin2 分析和快速交叉验证 |
| 需要 Ghidra 专用脚本或引擎细节 | `ghidra-reverse` | 已配置的 Ghidra provider、headless 项目和脚本 |
| 需要从大量函数中挑选重点 | `key-function-analysis` | 函数优先级、调用关系和深挖顺序 |
| 发现加密、解密、编码、压缩或代码混淆 | `crypto-obfuscation-analysis` | 候选识别、证据评分、语义 IR 和验证编排 |
| 需要运行、调试、跟踪样本 | `sandbox-dynamic-debugging` | 按平台使用 Windows 虚拟机或 Linux Runtime Sandbox |
| 已有 CAPE/DDAN 等动态报告 | `dynamic-malware-analysis` | 解析和归纳已有报告，不重新运行样本 |
| 目标是 APK、.NET、Go/Rust、固件等 | 对应专项 Skill | 使用平台或语言专用分析方法 |

推荐顺序：经统一入口评估 REA 后，使用本 Skill 完成分诊；再按目标格式选择工具，
必要时加入 `key-function-analysis`；只有获得动态授权后才加入
`sandbox-dynamic-debugging`。`ida-reverse`、`ghidra-reverse` 和 `radare2`
是替代关系，通常不需要同时加载。

当前任务已允许动态分析、且静态证据需要运行验证时，Windows 原生 x64/x86 EXE
主动走 `sandbox-dynamic-debugging` 的 Windows 路线：搜索 Windows、激活
`windows_analysis`、检查 `WindowsCapabilities`，再按架构创建 x64dbg/x32dbg 会话。
不因缺少 Linux `sandbox` MCP 跳过可用的 Windows 通道；仅静态分析不启动样本。
DLL、驱动、ARM64、托管专用调试另行核实支持。沿用已有授权，回收证据后按明确的
恢复授权处理基线；无适用通道或 VM 被占用时记录 blocked 并继续静态分析。

## 路由与执行边界

本 Skill 是二进制逆向的通用方法。先完成分诊，再根据目标和任务线索
选择专项 Skill；不要因为目录中存在某个 Skill 就默认加载它。

- 识别到 Go/Rust，或用户明确提到 Go/Golang/Rust：加载 `go-rust-reverse`，并按需读取本目录的 `go-reverse.md` 或 `languages-compiled.md`。
- 识别到 ELF/Linux：按需读取 `elf-analysis.md`。
- 任务涉及加密、解密或编码：按需读取 `crypto-decode-tools.md`，需要实验时再加载 `sandbox-dynamic-debugging`。
- 任务涉及加密、解密、编码、压缩或代码混淆：加载 `crypto-obfuscation-analysis`；需要执行复现代码时再加载 `sandbox-dynamic-debugging`。
- 任务涉及混淆、加壳或反调试：按需读取 `anti-analysis.md` 和 `patterns.md`。
- 需要大量函数筛选、调用链排序或关键函数深挖：加载 `key-function-analysis`。
- IDA、Ghidra、radare2 只选择实际可用且最适合当前目标的一套工具 Skill。

包内参考文件可用工作区 Read；未复制的外部跨包资源用 `read_skill_resource` 按需读取。
不要把整个目录一次性读入上下文。
每次读取前说明它与当前目标的关联，读取结果只作为方法参考，当前样本结论仍须由
分析工具输出和可复核证据支持。

## 多工具协同原则

工具 Skill 是工具使用说明，不是分析主线，也不会限制可用工具。加载
`ida-reverse`、`ghidra-reverse`、`radare2` 或某个语言工具后，仍应根据证据需要
组合使用其他已注册工具，例如 `strings`、`rabin2`、`GoReSym`、`redress`、
`angr`、`capa`、`FLOSS`、`crypto-detector` 和 IDA MCP。

不要因为当前加载了一个工具 Skill 就只调用该工具。每次工具选择都说明：
要验证的假设、该工具能提供的证据、是否需要另一个工具交叉验证，以及工具不可用
时的替代方案。语言专项 Skill（例如 `go-rust-reverse`）负责分析目标特征和工具
编排；具体工具 Skill 只负责对应工具的调用细节。

### 分诊阶段的工具组合

| 工具 | 主要用途 | 何时使用 |
|---|---|---|
| `FLOSS` | 提取栈字符串、解码字符串和混淆字符串 | 普通 `strings` 结果不足或怀疑运行时解码 |
| `capa` | 根据规则识别网络、注入、持久化、加密等能力 | 快速建立行为候选，不作为单独定论 |
| `crypto-detector` | 扫描加密算法和密码学常量候选 | 出现疑似加密函数、S-box、轮常量或密钥处理 |
| `GoReSym` / `redress` | 恢复 Go 函数、包和运行时元数据 | 识别为 Go，尤其是 stripped 二进制 |
| `rabin2` / `readpe` / `file` | 格式、架构、节区和导入导出分诊 | 每个新样本的第一轮静态检查 |

推荐首轮顺序：`file`/`readpe`/`rabin2` → `strings` + `FLOSS` → `capa`；识别为
Go 后加入 `GoReSym`/`redress`，发现加密或混淆线索后再加入 `crypto-detector`
和 `crypto-obfuscation-analysis`。所有命中都必须回到函数、调用链和数据流中复核。

在本项目中，FLOSS 和 capa 通过实际注册的 `floss_strings`、`capa_analyze` 工具
调用；不要把它们当作动态沙箱 shell 中必然存在的命令。reverse 样本参数使用
相对于 `/workspace/samples` 的路径，例如 `sample.bin`；REA 使用实际返回的绝对路径。通过 `reverse_tool_call` 时，
`arguments_json` 可以传 JSON 字符串或对象，但 `options`/`commands` 必须是字符串数组。

## 目录内 Markdown 资源如何使用

目录内的参考文档是“按需读取”的知识库，不是默认上下文。完成分诊后，按下表
选择最相关的 1～3 个文件，并在读取前说明选择理由：

| 任务线索 | 读取文件 |
|---|---|
| 通用工具用法、IDA/Ghidra/r2/Frida/angr 选择 | `tools.md` |
| 需要动态调试、hook、符号执行或覆盖率 | `tools-dynamic.md`；实际运行再加载 `sandbox-dynamic-debugging` |
| 壳、混淆、反调试、反虚拟机 | `anti-analysis.md`；LLVM 混淆再读 `references/ollvm-deobfuscation.md` |
| 已知二进制模式（XOR、S-box、自修改、VM 等） | `patterns.md`；CTF 专项才读 `patterns-ctf*.md` |
| 加密算法识别、配置解码 | `crypto-decode-tools.md`；实验命令读 `sandbox-dynamic-debugging/references/crypto-experiment-playbook.md` |
| ELF/Linux | `elf-analysis.md` |
| Go/Rust/C++ 等编译型语言 | `go-reverse.md` 或 `languages-compiled.md` |
| Python/WASM/Android/.NET 等平台或语言 | `languages.md`、`languages-platforms.md` 或对应专项 Skill |
| 内核、驱动、固件、硬件 | `kernel-driver-reverse.md`、`platforms*.md` |
| 自定义 DSL/虚拟机 | 加载独立的 `dsl-vm-reverse` Skill |
| AI 辅助逆向方法 | `references/ai-assisted-re.md` |
| 需要记录经验而非执行步骤 | `field-notes.md` |

没有命中任务线索的文档不要读取；不要为了“完整”一次性加载整个目录。

## 总体原则

- 先收集证据，再提出结论；工具输出不是事实本身。
- 默认只做静态分析，不运行用户样本、不修改原始文件。
- 所有修改和实验都针对 case 目录中的副本。
- 每个重要结论都记录来源：文件偏移、虚拟地址、函数、字符串、API 或实验产物。
- 明确区分“观察到”“推测”“已验证”；无法确认时说明缺口。

## 四阶段工作流

### 阶段一：分诊（Triage）

目标是快速确定样本是什么，以及后续应使用哪些工具。

1. 计算 MD5/SHA-256，记录文件大小和路径。
2. 识别格式、架构、位数、端序和操作系统。
3. 检查节区、入口点、导入/导出、符号和编译器/壳特征。
4. 提取普通字符串；必要时使用 FLOSS 等工具寻找栈字符串或解码字符串。
5. 检查是否为 ELF、PE、Mach-O、APK、.NET、脚本、固件或混合文件。

阶段产物：一份简短的文件身份和工具可用性记录。不要在此阶段猜测完整行为。

分诊完成后再根据上表读取专项参考文档。

### 阶段二：静态分析（Static）

目标是建立函数、调用关系和数据流的证据链。

1. 列出入口点、`main`、初始化、线程/回调、外部输入和高影响操作；优先闭合相关行为链，再处理错误处理和高调用度辅助函数。
2. 可用时使用 `rank_key_functions` 辅助排序；否则按入口、输入、执行点和调用关系建队列，不为排序切换已有可用引擎。外部输入、执行点及连接它们的低分函数也进入必查队列，再对关键函数反汇编/反编译。
3. 画出关键调用链：谁调用它、它调用谁、输入从哪里来、结果流向哪里。
4. 结合字符串交叉引用、导入 API、全局变量、结构体和内存读写解释行为。
5. 优先跟踪注册的回调、线程入口、恢复分支和退出清理，形成业务主链，再展开 CRT/库辅助函数。对未解间接调用单独记录缺口；不要凭邻近函数补出调用关系。
6. 自动函数列表漏项或反编译为空时，从调用目标核对边界，使用 r2 手工 `af` 或 IDA 静态反编译交叉验证。排名返回 `ranking_degraded` 时先检查引用采集，低分不是低风险。

工具选择：先按 `rea-analysis` 使用适合目标的 REA 能力；provider 不支持、实际失败、
需要独立交叉验证或用户指定时使用已配置的 IDA/r2/其他分析器。记录实际工具与限制，
不把未调用的工具写成失败，不把未执行的工具结果写成事实。

### 阶段三：算法与加解密分支

当发现数据变换、密钥、校验或疑似加解密逻辑时进入本分支：

1. **提出假设**：例如 AES、RC4、TEA/XTEA、XOR、哈希或自定义算法，并写明依据。
2. **寻找证据**：检查加密 API、常量、S-box、轮常量、块大小、循环次数、输入输出缓冲区。
3. 可用 `crypto-detector` 扫描 strings、源码或反编译文本，结果只算候选线索。
4. 将候选算法与反汇编结构、调用关系和数据流相互印证；不能仅凭 API 名称定论。
5. 如果静态证据不足且用户已授权，加载 `sandbox-dynamic-debugging`，执行一个有界实验。
6. 用明文、密钥、IV、中间值、长度、哈希或第二种实现验证结果；矛盾时回滚并改写假设。

### 阶段四：动态验证与综合（Dynamic / Synthesis）

只有在用户明确要求或授权运行时验证时，才加载动态调试 Skill，按目标平台选择
已注册的 Windows、Linux 或适用隔离通道。动态结果必须与静态地址、函数和输入输出对应起来。

最终综合所有证据，标注置信度：

- `candidate`：只有字符串、API 或启发式命中；
- `supported`：有多项静态证据相互支持；
- `validated`：被可重复的动态实验或独立实现验证。

## 正式报告结构与分析要点

统一助手先按 [任务报告规范](references/report-profiles.md) 选择 binary、malware、behavior 或 crypto。
本次运行器已提供模板时沿用；没有注入时按包内规范和用户要求组织。
已注册的报告保存/校验工具要求 `report_type` 时，按实际 schema 传入。
以下结构仅用于静态逆向 binary，不覆盖恶意软件、行为核查或加解密模板。
静态逆向正式报告使用 [报告规范](references/report-standard.md)，
按“执行摘要 → 样本身份与分析范围 → 文件结构与保护特征 → 程序架构与执行流程 →
核心功能与实现机制 → 关键数据、配置与算法 → 安全行为与风险评估 →
分析覆盖、未决问题与后续验证 → 附录 A：证据索引与复现方法”组织。

运行器已注入完整规范时直接使用；否则读取包内参考文件。
开始分析即按规范规划取证；每个核心功能写明触发条件、调用链、数据流、
成功/失败分支、副作用、证据与边界。缺少证据的分析点说明原因和影响，不补造结论。
交付前保存完整草稿，已注册且适用时用 `validate_reverse_report` 检查，另行复核事实与工件引用。
按当前任务要求交付正文或已保存报告的链接；格式仍有问题时保留可读草稿与限制。
脚本与运行结果按实验目录归档，禁止把未执行代码写成验证结果。

## 完成前检查

- 是否记录了哈希、架构和实际使用的工具？
- 关键结论是否有地址、函数或产物作为证据？
- 是否把假设与验证结果分开？
- 是否误把候选命中当成确定算法？
- 外部输入到最终操作的路径是否闭合？未解执行分支是否已在摘要限制结论？
- 动态执行是否只发生在当前任务授权的适用隔离通道？
