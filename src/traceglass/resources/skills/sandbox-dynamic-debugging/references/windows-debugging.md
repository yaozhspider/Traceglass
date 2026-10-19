# Windows 动态调试

适用于当前账号已接入 Windows 虚拟机、任务允许动态验证的原生 x64/x86 EXE。
Windows 与 Linux Runtime Sandbox 是两条独立通道；使用工具目录中的实际 schema。

## 1. 确定输入与实验

用 `PrepareAnalysisInput(path="samples/demo.exe")` 核对当前工作区文件和 SHA-256，
再静态确认 PE 架构、目标函数/分支和待验证问题。x64 使用 `debug_x64`，x86 使用
`debug_x32`；不能根据宿主机架构选择。DLL、驱动、ARM64 和托管专用调试没有在
此接口中获得自动支持，不改后缀伪装成 EXE。

当前任务已有的动态授权可沿用。仅要求静态分析或解析已有日志时不启动样本。
运行前说明实验范围、超时和收尾方式；快照恢复需要单独有明确授权，不能把
“允许调试”或管理员配置了基线等同于“允许丢弃当前虚拟机改动”。

## 2. 发现工具并检查能力

1. `SearchAnalysisTools(query="Windows")`，按搜索结果调用 `reset_tools` 启用
   `windows_analysis`，保留仍需的其他组；读取实际工具 schema。
2. 先调用只读 `WindowsVmStatus()`：检查 `max_concurrent_jobs`、`available_slots`、
   `exclusive_task`、`baseline_reset_required` 和 `recovery_state`。有其他任务不等于
   不可用；存在共享槽位时，符合下一节条件的普通程序可以并发。容量满、独占任务
   占用或基线恢复中时，不启动新目标，不抢占或恢复其他工作区的任务。
   只在 `recovery_state="ready"`、`baseline_reset_required=false` 且
   `available_slots>0` 时提交共享任务；`baseline_dirty=true` 本身不表示不可加入。
   所有槽位释放后若要求恢复，先恢复才能开始下一批；模型不选择或伪造批次 ID。
3. 调用 `WindowsCapabilities()`。若返回进行中的 `job_id`，通过 `WindowsJobStatus`
   等待其结果，不能把 `running` 当作成功。
4. 检查 `guest_operations_ready`、`allow_execution`、对应位数的
   `debug_dependencies_present.x64` / `.x32`。`profiles` 或 exe 路径存在不证明调试
   插件已经可用。固定基线快照由服务在提交动态任务前校验，缺失时不绕过限制。
5. 无工具、连接失败、依赖缺失、账号未启用或没有适用槽位时，保留动态分支
   `blocked`，继续静态分析并报告具体原因。不要改用普通工作区 Bash 运行 PE。

此流程不需要 Linux `sandbox` MCP；也不上传 ESXi/Windows 密码或登录凭据给模型。

## 3. 创建并使用本任务的调试会话

例如：`WindowsRunTool(sample_path="samples/demo.exe", profile="debug_x64", timeout=120)`。
默认 `isolation="auto"`：静态任务共享，动态任务独占。恶意样本、恶意性未知目标、
会修改系统的程序使用 `isolation="exclusive"`；不能为了绕过占用而改成共享。
会遗留后台子进程、启动服务或守护进程的程序同样独占；共享 worker 的主进程退出
不能证明独立后台进程已退出。
已有动态授权、且普通程序的实验允许共享系统状态时，显式传
`isolation="shared"`，例如
`WindowsRunTool(sample_path="samples/demo.exe", profile="debug_x64", isolation="shared")`。
并发数以 `WindowsVmStatus` 为准；每任务有独立目录、TEMP 和调试器进程，注册表、
服务、网络和文件系统仍共享，不能宣称样本之间有强隔离。
这里的 `sample_path` 相对于当前工作区，保留 `samples/` 前缀；也接受实际 schema
允许的 `/workspace/...` 路径。不要传宿主机路径、Windows 来宾路径或 Linux
`sandbox_path`。上传时服务会计算并校验 SHA-256，结果应与已准备样本一致。

保存返回的 `job_id`，用 `WindowsJobStatus(job_id=...)` 查询真实结果。只有本任务的
调试会话成功创建、确有目标进程时才继续；`unknown`、超时或连接中断不代表未执行，
先查已有任务，不重新提交。服务创建新的自有会话，不附加用户的其他进程。

按要验证的问题调用 `WindowsDebug(job_id=本任务返回值, action=...)`：

| 目的 | `action` 与要求 |
|---|---|
| 查看是否正在运行/暂停 | `status` |
| 读取寄存器 | `registers`，目标应已暂停 |
| 设置断点 | `breakpoint`，`address="0x..."` 为实际数值地址 |
| 单步 | `step`，`count` 为 1–100，目标应已暂停 |
| 继续/暂停 | `continue` / `pause` |
| 读取/转储内存片段 | `memory` / `dump`，数值地址，`length` 为 1–65536 字节 |
| 关闭本任务会话 | `close` |

每次操作返回独立 `operation_id`，用 `WindowsJobStatus` 查看 `operations` 中的
结果，确认完成后才提交下一次。未完成或 `unknown` 的操作不可重放。
`continue` 是异步运行，不等于已到达断点；通过状态和原始观察确认。

先核对实际模块基址、ASLR、RVA 与运行时地址。不得把静态 VA 直接当作当前地址，
也不保证调试器初始暂停点就是 main/WinMain。接口不接受任意调试器命令。
`memory` 返回 base64；`dump` 只生成有界的内存片段，不是完整进程转储。

## 4. 回收与收尾

1. 结束本次实验后关闭本任务的调试会话，查询关闭操作完成。
2. `WindowsCollectResults(job_id=...)` 回收原始日志、操作结果和内存片段到当前
   工作区 `reports/windows/<job_id>/`。读取清单、产物哈希及实际内容后再引用；
   不用 Linux `archive_sandbox_experiment` 猜测 Windows 路径。
3. 记录样本哈希、假设、profile、job/operation ID、断点与实际地址、观察结果、
   静态对应位置、覆盖范围及局限；保留自动归档的证据 ID。
4. 读取回收结果的 `slot_released`、`capacity` 和 `recovery`。已关闭并回收的
   共享任务释放自身槽位，其他会话继续；中途回收观察不会关闭调试器或释放槽位。
   启动失败只有确认清理自有调试器后才释放；`unknown` 和待完成操作保留占用。
5. `auto_reset_on_idle=true` 表示管理员已配置在最后一个任务收尾后自动恢复固定
   基线；`recovery.state=reset_required` 表示自动恢复失败，证据已在本地，不能
   把 VM 当成干净环境。默认关闭自动恢复。
6. 自动恢复未开启且当前任务已有固定基线恢复授权时，先查询容量，等本批次的
   其他任务都关闭并回收后，再调用 `WindowsResetGuest(job_id=...)`。仍有其他任务
   时服务拒绝恢复，`discard_pending=true` 也不能覆盖它们。没有恢复授权时报告
   “结果已回收，VM 待恢复”。新批次需等待恢复；同批次其他活动共享任务可继续。

未回收结果、待完成操作或 `unknown` 状态先调查，不自动使用
`discard_pending=true`。独占动态任务默认保留租约直到成功恢复基线；管理员配置
自动恢复时，在收尾回收后恢复。共享任务关闭并回收后仅释放自身槽位，不单独恢复
整台 VM。恢复后本地证据仍可回收读取。工具失败不等于算法被反证或样本良性，
未观察到行为也不等于不存在。
