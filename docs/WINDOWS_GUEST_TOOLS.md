# Windows 虚拟机工具接入

Traceglass 后端通过 ESXi API 和 VMware Tools 上传文件、启动固定工具并下载结果。Windows 无需开放 SSH、RDP、WinRM 或 MCP 端口。模型通过原生工具 schema 获知参数，凭据只由后端读取。

## 管理员配置

1. 在后端环境安装可选依赖：`python -m pip install -e '.[windows]'`。
2. 复制 `config/windows.example.json` 到后端管理员的私有目录。填写 ESXi 地址、虚拟机 ID 和名称、Windows 用户以及实际工具路径。`allowed_users` 必须填写 Traceglass 账号 ID；本地单用户模式通常为 `local`，多用户模式使用 `/auth/session` 返回的 `user.id`，不使用显示名称或 `*`。
3. 通过已经验证主机密钥的 SSH 或可信管理通道取得 ESXi `/etc/vmware/ssl/rui.crt`，保存为配置中指定的 `esxi.pem`。仅信任这个证书，不关闭 TLS 验证。ESXi 的默认主机名可能不匹配 IP，因此适配器验证显式证书链，不验证主机名；证书轮换后需通过同样可信的通道更新。
4. 通过服务管理器或私有环境设置 `TRACEGLASS_ESXI_PASSWORD`、`TRACEGLASS_WINDOWS_PASSWORD`，以及 `TRACEGLASS_WINDOWS_CONFIG=/absolute/private/windows.json`。JSON 只保存环境变量名。不要把密码放入仓库、模型提示词、命令行参数或 Docker 环境。
5. 后端下次启动时按配置注册工具。未配置时完全关闭；创建应用不会连接 ESXi。当前运行的服务不会热加载此设置。

单个 VM 使用一个后端进程和同一份 `windows-jobs/jobs.sqlite3`。SQLite 持久化每项任务的共享/独占租约、批次及恢复屏障，进程重启保留。不要让不同部署或维护脚本使用不同状态库操作同一台分析 VM。旧版单租约会原样迁移成独占任务，即使旧任务失败也不会静默释放。

`max_concurrent_jobs` 配置并发槽位，示例及本机配置为 **8**，取值为 1–64 的整数；旧配置缺省仍为 1。`auto_reset_on_idle` 默认 `false`；管理员显式设为 `true` 表示批准在全部任务关闭并回收后自动恢复固定基线，必须配置 `baseline_snapshot`。这是整个 Windows 的并发数，与 REA 连接数独立。

静态分析默认共享。动态任务默认独占；已授权、普通且允许共享系统状态的程序可显式选择 `isolation="shared"`。恶意样本、恶意性未知的目标及会修改系统的任务使用 `exclusive`。各任务目录、调试器 TEMP、PID 和操作 ID 独立，Windows 注册表、服务、网络及文件系统仍然共享，不能视为强隔离。第 9 项任务返回结构化 `busy` 和容量信息，不启动目标；本版不创建后台等待队列。

共享执行只适用于受控且不会遗留后台进程的普通程序。当前 worker 在超时或取消时终止自有子进程树，但主进程正常退出不证明派生的独立后台进程也已退出；会启动后台子进程、服务或守护进程的程序使用独占模式。静态工具及 execute 也继承任务专属 `task-temp`，调试器及后续操作使用独立且持久化的 `debugger-temp`；两者均不作为分析产物下载。

任务记录保存带版本检查的更新，避免并发查询覆盖刚提交的操作。每次来宾进程启动都持有持久化的启动标记，确认当前租约、批次和任务版本；即使 `discard_pending=true`，恢复也必须等待正在发送的启动请求结束。若后端在恢复中退出，下次启动保持待恢复状态，允许原任务重试固定基线恢复，不直接接收新任务。

这是专用分析虚拟机接入：账号和工作区隔离由后端任务校验实现，共用 Windows 账号和 VM 本身不构成强租户隔离。只将可信的明确账号加入列表。运行不可信样本前，应由管理员准备隔离网络、确认干净基线；当前接入不会修改虚拟网络或默认开放样本执行。

## 模型接口

先 `SearchAnalysisTools(query="Windows")`，按返回参数调用 `reset_tools` 激活 `windows_analysis`，再读取实际 schema。

| 工具 | 用法 |
| --- | --- |
| `WindowsCapabilities` | 验证 Guest Operations、Python 和配置的工具文件；路径存在与调试插件可用分别判断 |
| `WindowsVmStatus` | 只读查询并发上限、占用和可用槽位、独占与基线恢复状态；不连接 ESXi、不启动任务、不暴露其他工作区任务 ID |
| `WindowsRunTool` | 上传当前工作区文件，计算并复核 SHA-256，启动固定 profile，返回 `job_id` |
| `WindowsJobStatus` | 查询任务状态、退出码和已保存的调试操作结果 |
| `WindowsCollectResults` | 下载有总大小限制的日志和产物，保存到 `reports/windows/<job_id>`，返回清单与哈希 |
| `WindowsCancelJob` | 写入取消标记，由 worker 终止其子进程树；继续查询确认结果 |
| `WindowsDebug` | 提交当前工作区已创建会话的状态、寄存器、数值断点、单步、继续、暂停、内存读取、范围转储或关闭操作 |
| `WindowsResetGuest` | 其他任务全部收尾后，恢复管理员固定基线；禁止覆盖其他活动任务或未回收证据 |

固定静态 profile：`die`（DIE JSON）、`rizin`（分析并列出函数）、`upx_test`（检测压缩流，不解包）、`wasm2wat`、`jadx`。`jadx` 通过固定 Java 和 jar 启动，不调用 `.bat`。adb、Ghidra、dnSpy、ZAP、VMPDump 的安装不会自动生成此适配器中的 API；本次没有为它们提供完整控制接口。

`WindowsCapabilities.profiles` 仅表示所需文件存在；是否允许执行看 `allow_execution`，调试依赖看 `debug_dependencies_present`，实际会话仍需验证。

## 分析助手的动态路由

逆向、恶意软件和脱壳 Skill 共用 `sandbox-dynamic-debugging`：先静态核验样本身份、
格式和架构；当任务已有动态授权且需要运行验证 Windows 原生 x64/x86 EXE 时，
模型主动搜索并激活 `windows_analysis`，检查 Windows 能力，再分别使用
`debug_x64` / `debug_x32`。缺少 Linux sandbox MCP 不影响这条 Windows 路线。
仅要求静态分析或解析已有日志时不启动样本；DLL、驱动、ARM64、托管专用调试
不视为已支持。当前接口不提供完整进程树、注册表或网络监控，也不提供完整进程转储。

工具提交后查询真实 job/operation 结果，不重放 `unknown` 或未完成操作。Windows
样本路径相对当前工作区，如 `samples/demo.exe`；回收结果到 `reports/windows/<job_id>/`。
模型操作流程随 Skill 离线分发，见
[Windows 调试参考](../src/traceglass/resources/skills/sandbox-dynamic-debugging/references/windows-debugging.md)。
同一任务已有的动态授权可沿用，不必逐个断点确认。快照恢复会丢弃来宾改动，需明确
的恢复授权，或管理员已启用 `auto_reset_on_idle`。共享任务关闭自有会话并回收终态结果后释放自身槽位，其他会话继续；最后一项结束后，按上述恢复策略处理。动态独占任务未配置自动恢复时保留租约，直至明确授权恢复。

`execute`、`debug_x64`、`debug_x32` 执行目标代码，默认禁用，只接受带 MZ 头的 `.exe`/`.com` 文件。启用必须同时设置 `allow_execution: true` 和唯一的、管理员确认过的 `baseline_snapshot`；提交前验证快照存在。任务必须回收有效终态结果后才释放共享槽位；调试器仍打开、启动响应不确定或操作未确认时继续占槽。启动调试失败只有 worker 确认清理自有进程后才允许释放。恢复快照会结束全部来宾进程，服务使用事务屏障，确保没有其他占槽任务；恢复期间拒绝新任务。

可先调用 `WindowsVmStatus()` 查看 `available_slots`。普通程序示例：`WindowsRunTool(sample_path="samples/demo.exe", profile="debug_x64", isolation="shared")`。结束时 `WindowsDebug(action="close", job_id=...)`，查询关闭操作成功，再 `WindowsCollectResults(job_id=...)`；返回 `slot_released`、`capacity` 和 `recovery`。收集中的调试观察可以回收，但打开的会话不释放槽位。静态任务同样需要回收，避免快照覆盖尚未保存的报告。

最后一个共享任务回收后，若本批次执行过目标，新的任务等待固定基线恢复。自动恢复未开启时，本批次的任务所有者在已有恢复授权下调用 `WindowsResetGuest`；仍有兄弟任务时会拒绝，`discard_pending=true` 也不能覆盖它们。自动恢复失败时本地产物已保留，容量保持为 0，可由本批次任务所有者排查后重试固定基线恢复。恢复完成或恢复失败后再次回收返回已保存并校验过的本地结果，无需来宾就绪；返回实际恢复状态、租约是否释放，以及此前未采集的操作数 `uncollected_operations`，不把旧报告当作这些操作的结果。

恢复磁盘基线后，如果虚拟机处于关机状态，服务会开机并等待 Guest Operations 和来宾凭据验证通过，才释放租约。开机或就绪检查失败时保留租约。新任务在申请租约前也检查来宾就绪；目录创建、上传等明确发生在进程启动前的失败会释放该任务租约。已经发出启动请求但未收到确定响应的任务仍保留 `unknown` 和租约，不能自动重放。

所有任务都属于账号和工作区。`running`、`starting`、`unknown` 不等于成功。若启动请求可能已送达而响应丢失，保留任务及租约，禁止自动重放。只有进程结束并读到匹配样本哈希的结果文件才能确认终态。未知任务由管理员排查或恢复已配置基线；不要直接删除 SQLite 租约后重新执行。若调试操作一直无法确认，结果回收会拒绝该未完成操作；`WindowsResetGuest(discard_pending=true)` 可明确放弃未回收结果并恢复基线，返回 `discarded_pending_results`。

调试操作异步提交，每次有独立 `operation_id`；先用 `WindowsJobStatus` 查看 `operations`，完成后再提交下一次。`memory` 返回最多 64 KiB base64，`dump` 将该范围写入 `memory.bin`，通过 `WindowsCollectResults` 回收；它不是完整进程转储。没有任意调试器命令或附加到其他进程的入口。来宾输出属于观察证据，不能单凭退出码推断样本安全。

## x64dbg 自动化依赖

需要 [x64dbg Automate 插件](https://github.com/dariushoule/x64dbg-automate/releases) 和[匹配的 Python 客户端](https://dariushoule.github.io/x64dbg-automate-pyclient/installation/)。将发布包 `Release` 的完整内容分别放到相应位数的 `plugins` 目录，在 guest worker 使用的 Python 中安装 `x64dbg_automate`。保持插件默认的 localhost 模式，不启用远程监听。

建议使用独立 Python venv，再把配置的 `tools.python` 指向其 `Scripts/python.exe`，避免修改现有分析工具的依赖。系统断点、入口点暂停设置需要按官方说明配置。客户端存在、exe 存在都不等于会话可用；首次创建任务才验证匹配、启动及会话通道。VMware Tools 的非交互式会话也不等于当前可见的远程桌面。

每个新建调试任务使用自己的 `debugger-temp` 目录作为 `TEMP`/`TMP`。主机将此路径持久化在任务记录中，后续独立操作进程使用同一路径发现该任务的调试器；不信任来宾结果提供的目录。旧任务保持原来的临时目录策略。运行器结束后恢复自己的环境变量，临时会话文件不列入分析产物。

2026-10-19 定位到默认系统临时目录的权限问题：VMware Tools 在 Session 0 启动进程，`TEMP=C:\Windows\TEMP`。当前账号能按完整路径读取插件生成的 `xauto_session.<pid>.lock`，但 `os.scandir` 返回 WinError 5；客户端 0.9.2 的 `glob` 隐藏该枚举错误，返回空列表，内部约 10 秒后报 `Session did not appear in a reasonable amount of time`。外层任务超时增加到 180/240/300 秒或恢复相同基线均不能解决目录权限问题。不加载样本的空会话也能复现，延长到 60 秒仍无法枚举。使用任务专属临时目录后，x64/x32 空会话分别约 1.0/3.7 秒创建成功，并由另一个进程重新连接及关闭。无需扩大系统临时目录 ACL 或重装插件。

调试失败结果现在包含 `failure_stage` 和 `memory_dump_attempted`，区分会话建立、样本加载、调试器状态、内存读取和文件写入。会话建立失败不能作为内存转储失败的证据。这次真实验证只创建空调试器，未加载或执行样本；目标样本的断点、单步及内存转储仍需后续验证。

提供离线安装维护命令 `scripts/install_windows_debugger.py`：下载匹配的官方插件 ZIP 和 Windows Python 3.12 wheels 后，传入 `--config`、`--wheels`、`--plugin64`、`--plugin32`。默认仅检查包；加 `--install` 才上传、校验 SHA-256、创建独立 venv 并安装插件。不同内容的现有插件不会被覆盖。安装只验证新建空会话并关闭，不加载目标，不恢复快照；维护前确认没有活动 Windows 任务。首次安装时，配置中 `tools.python` 应先指向已有的 `C:\Tools\Python312\python.exe`，安装完成后再切换到独立环境。

## 检查命令与本次验证

在独立的测试状态目录和测试工作区运行，避免与正在使用的分析 VM 任务重叠：

```sh
python scripts/verify_windows_tools.py \
  --config /absolute/private/windows.json --user YOUR_TRACEGLASS_USER_ID \
  --state-dir /absolute/private/windows-smoke-state \
  --workspace /absolute/private/windows-smoke-workspace \
  --discover-paths --static-smoke
```

缺少密码环境变量时只在终端隐式输入。该检查验证 Guest Operations，读取固定工具名称所在路径，再生成八字节空 WASM，由 wasm2wat 转换并回收验证 `(module)`。不执行用户样本、不恢复快照。重复运行使用新的测试工作区。

2026-10-15 实测：ESXi `192.168.113.37`，VM `48/yaozh`，Windows IP `192.168.113.77`；Python 3.12.10 和 VMware Tools 正常。wasm2wat 完成上传、哈希复核、执行、终态查询、产物下载与内容检查。实际目录已用于示例配置。

随后已安装客户端 `0.9.2` 到 `C:\Traceglass\python-xauto-0.9.2`，插件为官方 `v0.8.1-ghost_fungus`；64 位和 32 位空会话均创建、确认兼容版本 `ghost_fungus` 并关闭成功。配置 `tools.python` 可指向 `C:\Traceglass\python-xauto-0.9.2\Scripts\python.exe`。样本加载、断点、单步、内存读取和快照恢复尚未做真实目标验证。

本机接入已于 2026-10-15 启用并重启后端，允许当前登录账号使用 Windows 工具。私有配置位于 `~/.config/traceglass/windows/windows.json`，密码单独存放于该目录下权限为 `0600` 的 `credentials.env`，由后端启动环境读取，不写入仓库、模型 schema 或 Docker 环境。

已创建磁盘基线 `traceglass-tools-ready-20261015` 并验证名称唯一；启用了 `allow_execution`，未执行快照恢复。基线建立前重新验证了 Guest Operations、全部配置工具路径，以及 64/32 位空调试会话的创建与关闭；未运行用户样本。Windows 接通电源时的自动睡眠与休眠超时已关闭，避免 Guest Operations 因睡眠失联。真实样本加载、断点、单步、内存读取和快照恢复仍需后续验证。

后续手动启动本机后端时，在项目目录使用私有启动器加载凭据与账号部署参数（先确认 8002 没有正在运行的服务）：

```sh
.venv/bin/python /Users/yaozh/.config/traceglass/windows/start_backend.py
```

私有 `backend-profile.json` 的 `environment.TRACEGLASS_WINDOWS_CONFIG` 必须指向上述
`windows.json`。专用启动器会在该值为空或文件不存在时退出，避免静默禁用 Windows。
凭据仍从 `credentials.env` 注入宿主机进程；不写入 Docker 环境。修改启动配置后，
现有进程不会自动获得 Windows 工具，需要另行安排后端重启；无需为此重建分析镜像。

2026-10-16：已补齐 Windows 动态路由与私有启动配置，按用户要求未重启服务或容器，
未加载样本或恢复 Windows 快照。此次验证只覆盖离线加载、工具契约和启动参数传递。
