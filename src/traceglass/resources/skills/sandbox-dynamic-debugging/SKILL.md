---
name: sandbox-dynamic-debugging
description: 当用户要求运行、调试、跟踪样本，或已授权通过运行验证静态假设时使用。按目标平台选择已注册的 Windows 虚拟机 x64dbg/x32dbg 或 Linux Runtime Sandbox。纯静态分析或解读已有 CAPE/DDAN 报告时不要使用。
---

# 沙箱动态调试

样本运行只通过当前账号已注册且获授权的动态通道。禁止使用普通工作区 Bash、
静态 REA 容器、控制台容器或宿主机直接执行样本。

## 进入条件与平台选择

先做静态身份核验，记录样本哈希、格式、架构与一个需要运行验证的问题。当前任务
已明确允许动态分析或运行验证时，沿用该授权，不对每个断点或单步重复询问。
泛称“分析/全面分析”不自动等于允许运行；用户仅要求静态分析、只提供已有行为日志，
或明确禁止执行时，继续静态分析或日志解析。

| 目标与条件 | 动态通道 | 下一步 |
|---|---|---|
| Windows 原生 x64/x86 EXE，任务允许动态验证 | `windows_analysis` 的 Windows 工具 | 主动搜索并检查能力，读取 [Windows 操作流程](references/windows-debugging.md) |
| Linux ELF/脚本，任务允许动态验证 | 已绑定的 `sandbox` MCP | 使用下方 Linux Runtime Sandbox 流程 |
| DLL、驱动、ARM64、APK、托管专用调试等 | 需另有适用的注册工具 | 不套用 EXE 路线，不支持时保留动态分支 blocked |

工具组未激活时先搜索并激活。Windows 工具可用时，不因缺少 Linux `sandbox` MCP
而跳过 Windows 动调；已安装工具、注册 schema 和实际调试会话成功是不同状态。
连接失败、被其他任务占用、执行权限/基线/调试依赖缺失时记录具体缺口并继续静态分析。
恢复 Windows 快照会丢弃来宾改动，需当前任务明确允许恢复；已有恢复授权可沿用。

## 加解密/混淆路由提醒（必须检查）

动态调试过程中只要出现疑似加密、解密、编码、压缩、混淆、密钥派生或解码逻辑，
必须加载 `crypto-obfuscation-analysis` 作为辅助 Skill。不要把任务完全切换给加密
Skill：本 Skill 继续负责沙箱路径校验、执行授权、快照、超时、回滚和证据留存；
`crypto-obfuscation-analysis` 负责算法识别、假设提出、密钥/IV 和中间值分析、Python
复现代码以及解密结果验证。两者必须并行遵守，任何实验都要回到本 Skill 的沙箱流程。

触发信号包括：疑似 AES/RC4/TEA/XOR 等算法、S-box/轮常量、加密 API、密钥或 IV、
明显的编码/解码循环、熵异常、解密后再执行或解压缩行为。发现信号后，先声明已加载
辅助 Skill，再继续执行实验，不得只凭字符串命中直接认定算法。

## Linux Runtime Sandbox 的工具与路径

本节和后续 Linux 标准流程只适用于 `sandbox` MCP；Windows 使用上方参考流程，
不套用 `/samples/...`、Linux shell、GDB 或 `archive_sandbox_experiment` 的路径约定。

Runtime Sandbox 的 shell 主要用于授权的动态实验、Python 复现和调试，不保证包含
Console 镜像中的全部静态分析 CLI。`capa` 和 FLOSS 应调用已注册的
`capa_analyze`、`floss_strings` 工具，不要在 `mcp__sandbox__run_shell_command`
中直接输入 `capa` 或 `floss`。需要在沙箱中编写复现脚本时，只使用沙箱内实际可用
的 Python/编译器，并先通过能力探测确认命令存在。

路径契约必须严格区分：上传接口返回的 `sample_path` 仅供 Console 静态工具使用；
`sandbox_path` 才是 Runtime Sandbox 动态工具使用的路径，格式为
`/samples/uploads/<owner>/<hash>_<name>`。动态工具只接受这个沙箱内路径，禁止传入
宿主机路径、Console 的 `/output/...` 工作区路径或自行猜测的路径。Runtime Sandbox
只挂载 `/workspace`（可写）和 `/samples`（只读）；因此 case 目录、脚本、复制的样本
和所有动态分析产物必须写入 `/workspace/case-<id>/...`，绝不能创建或写入
`/output/workspaces/...`。先调用 `analysis_case_info` 获取当前会话路径和绑定状态，
不要把任意 `/output` 路径通过字符串替换改成 `/workspace`。静态工具仍使用挂载根目录相对路径，例如
`uploads/<owner>/<hash>_<name>`；不要把 `/samples/...` 传给静态工具。

## Linux 自主补工具与持久化

现成工具不支持目标格式、输出不足或不能验证假设时，可以自行编写解析器、
提取器、解码器或验证脚本。语言按目标语义和沙箱实际工具链选择：Python、Go、
C、Rust 等均可，不必先证明 Python 做不到。源码与输入输出放在 `/workspace`
下的独立实验目录，使用已注册的 sandbox MCP shell/IPython 工具执行；先确认
解释器/编译器存在，设定超时，不在 Console 或宿主机执行自编分析代码。

Web 服务中 `/workspace` 绑定到 `analysis_case_info` 返回的 `sandbox_export_dir`，
只共享当前会话的实验目录，不共享整个宿主机工作区。只有 `sandbox_binding.status`
为 `configured` 才能依赖此映射；失败时停止实验分支，保留静态分析并说明限制。
普通工具请求/响应自动保存；sandbox MCP 调用结束后自动生成有哈希的文件快照。
快照有限额，`partial`/`failed` 不代表完整归档，临时目录或后台仍在修改的文件也
不能声称已保存。需保留的工件在结束前写到 `/workspace`，不要只留在内存或 `/tmp`。

每次实验立即调用 `archive_sandbox_experiment`，加解密实验可用兼容入口
`record_crypto_experiment`；`artifact_dir` 直接填 `/workspace/...` 实验目录。
确认返回 `archive_status=archived`、证据 ID 和 `archive_path` 后再引用。
归档失败保留原目录；未生成目标目录时修正后重试。若返回 `partial` 且已经给出
`archive_path`，工件和 `.archive/record.json` 已保存，但索引可能不完整；保留该 ID，
恢复存储后用 `record_evidence` 登记该记录文件，不覆盖或重复归档同一 ID。
修改版另起 ID。
普通静态反编译、CFG、笔记与报告可用 `save_analysis_artifact` 保存，随后
`record_evidence(summary, artifact_path, confidence, source)` 关联实际归档文件。

## Linux 标准流程

1. 确认工具来自已绑定的 sandbox MCP；Web 可能只展示末尾的 `run_shell_command` 或
   `run_ipython_cell`，应以实际注册 schema 与 MCP 来源为准，不能把本地 Bash 当成沙箱。
   两种工具均不可用时停止动态分支并说明原因。
2. 使用上传响应中的 `sandbox_path`，不要从 `sample_path` 推导路径。执行任何动态命令前，必须先运行有界的 `ls -l -- <sandbox_path>` 和 `sha256sum -- <sandbox_path>`，确认文件存在、大小合理，并将结果与上传响应中的 `sha256` 比对；校验失败时不得继续执行。当前环境是 Linux：ELF/脚本可原生运行，支持的其他 Linux 架构可用 qemu-user。不要把 PE、驱动、APK 等声称为已动态运行。
3. 在 `/workspace` 创建 case 目录，把样本复制进去，只操作副本，不修改 `/samples`。
   `/workspace` 在 Runtime Sandbox 中是可写的；推荐使用兼容 `/bin/sh` 的命令（不要
   使用宿主机 `/output`，也不要依赖 shell 的花括号展开）：
   `case_dir=/workspace/case-<id>; mkdir -p "$case_dir/artifacts" "$case_dir/scratch" "$case_dir/scripts"`。
   如果创建失败，先执行 `pwd; id; ls -ld /workspace`，不要改写成 `/output/...`。
4. 先做有界观察（如 `strace`、`ltrace` 或 GDB batch），每条命令都设置超时，保存 stdout、stderr、退出状态和产物。
5. 只有当目标和假设确实需要时，才使用 gdb/gdb-multiarch、Frida、angr、qemu-user 或 radare2，并先探测工具是否可用。
6. 不连接样本发现的域名、IP、URL 或任何外部服务；不向沙箱写入凭证或宿主机目录。
7. 将运行时观察与静态地址/函数对应起来，把工具失败、超时和环境影响与已验证行为分开记录。

在第 4–7 步的任意阶段发现加解密/混淆信号，都必须回到上面的路由提醒，加载
`crypto-obfuscation-analysis` 后再设计相关实验。

## 加解密识别与解密实验

发现疑似加密或解密时，按可证伪的循环执行，不要凭关键词直接下结论：

1. **提出假设**：写明候选算法（AES、RC4、TEA/XTEA、XOR 或自定义算法）及依据。
2. **找证据**：检查加密 API、常量/S-box、块大小、轮数、循环结构、调用者和缓冲区。`crypto-detector` 只对源码、strings 或反编译文本生成候选线索，命中不等于二进制确实使用了该算法。
3. **执行实验**：按已选平台流程，每次只做一个有界实验。Windows 使用本任务的 x64dbg/x32dbg 会话；Linux 使用已绑定的 GDB/Frida/angr/r2。记录断点、输入、输出、超时和实际状态。
4. **验证结果**：用明文、密钥、IV、中间值、长度对齐、哈希或第二种实现交叉验证。
5. **失败处理**：保留原始失败结果，先结束本任务的调试操作并回收工件，再按当前任务已有的恢复授权回滚；未获恢复授权时记录待恢复，不自动恢复 Windows 快照。

### 编写复现代码并恢复明文

遇到疑似加密/解密逻辑时，可以编写 Python 复现程序，也可以按目标语义和
工具链选择 C、Go 或 Rust。生成的
程序也是分析工件，必须放在当前 case 的 `experiments/exp-<编号>/` 下，
不能写入宿主机或 `/samples`。

每个实验目录至少保存 `hypothesis.md`、复现源码、输入/密文副本、输出/明文
（如有）、完整命令、stdout、stderr 和包含退出状态与哈希的 `result.json`。
复现程序必须尽量逐项对应反汇编逻辑：字节序、溢出规则、轮数、旋转、分组
模式、padding、key/IV 派生和输出长度。标准密码库只能作为对照，不能单凭
库函数调用声称样本使用了该算法。

一次实验只改变一个变量。复现脚本只能通过支持该语言的已注册授权通道执行；
Windows 固定 EXE/debug profile 不能用于任意 Python 或 shell。Linux 实验完成后调用
`record_crypto_experiment`，传入 `artifact_dir`、假设、算法、命令、执行状态和
`validation_json`，复制整个实验目录并生成关联证据。只有
满足格式/魔数、长度、动态中间值或第二组输入等独立条件时，才将置信度升级为
`validated`，否则保持 `candidate` 或 `supported`。

实验失败时保留失败工件，标记为 `rejected` 或 `failed`，撤销 hook/断点/patch，
从新的实验目录重新开始；回滚只恢复运行状态和样本副本，不删除失败源码和证据。
不要覆盖旧实验，也不要把失败输出当作明文结论。

## 必须保留的证据

- 样本哈希和副本路径；
- 完整沙箱命令及超时；
- 断点、trace 过滤条件和脚本；
- 退出状态、关键输出和产物哈希；
- 静态证据与动态结果的对应关系及置信度；
- 覆盖范围、工具限制和未验证部分。
- 每次加解密实验的源码、输入/输出、命令、退出状态、哈希和验证等级。

Windows 原始结果通过 `WindowsCollectResults` 保存到 `reports/windows/<job_id>/`；
Linux 实验日志放在 `/workspace/case-<id>/`。置信度使用 `candidate`（候选）、
`supported`（有支持）或 `validated`（已验证）。Linux 命令模板见 `references/crypto-experiment-playbook.md`。

结构化实验记录写入当前 case 的 `experiments.jsonl`，关联证据写入
`evidence.jsonl`。最终报告必须引用生成的证据 ID。
