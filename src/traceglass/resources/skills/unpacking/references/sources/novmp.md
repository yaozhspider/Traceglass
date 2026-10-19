# NoVmp：VMProtect 静态去虚拟化

## 适用条件

NoVmp 是基于 VTIL 的静态去虚拟化工具。上游标注范围为 **已脱壳的 x64 PE / VMProtect 3.0–3.5**，并将项目定位为实验性 PoC。范围之外不能假定兼容；不用于 Themida、x86 或通用压缩壳。

先检查格式、架构、VMProtect 版本证据及 packing 层是否仍在。仍有 packing 层时按主 Skill 的沙箱路线 dump、重建；已经脱壳则直接做静态分析。版本不明时标为候选，工具失败不能反推样本没有虚拟化。

## Traceglass 执行通道

分析与 REA 镜像已安装 `novmp` 和别名 `NoVmp`，实际程序为 `/opt/novmp/NoVmp`。通过分析工作区现有 Bash 工具调用；没有新增专用 NoVmp MCP 方法，不要编造工具名。静态处理不启动目标 PE；如需运行产物，另按主 Skill 的动态沙箱要求处理。

先用 PrepareAnalysisInput 获取实际路径。下例路径仅为示例，替换为工具返回的工作区路径。REA 的 `/workspace` 挂载只读，`rea_file_path` 也不能直接当作分析工作区 Bash 的路径；优先在分析工作区创建可写副本。

每次运行使用新的目录，避免把旧 `vms/` 当成本次输出。示例中的 `run-001` 应替换为本次唯一编号，并给 Bash 调用设置适当超时：

```sh
novmp --help
novmp --version
mkdir -p /workspace/case-example/novmp/run-001
cp /workspace/samples/unpacked.exe /workspace/case-example/novmp/run-001/unpacked.exe
sha256sum /workspace/case-example/novmp/run-001/unpacked.exe
novmp /workspace/case-example/novmp/run-001/unpacked.exe
```

`--help` / `--version` 来自本地包装器，上游原始二进制不提供这两个开关。安装使用独立的 C/C++ 依赖，不要求改动 Python Capstone。

## 参数与地址

| 参数 | 用途与条件 |
|------|------------|
| `-base <hex>` | dump 输入需要时传入已知的原始 image base；依据 dump 记录或映像证据，不能照抄示例猜测 |
| `-vms <rva> [rva ...]` | 只处理指定 VMEnter；单位为 **RVA**，不是 VA 或文件偏移；不传时自动扫描 VM 入口 |
| `-sections <name> [...]` | 自动发现不足时补 VMProtect 代码节名；不要加入合并的 VMProtect DLL 节（上游称 `.<vmp>1`） |
| `-noopt` | 禁用优化，可用于区分提升失败与优化停滞 |
| `-opt:constant` | 启用 VMProtect Ultra 常量混淆优化 |
| `-experimental:recompile` | 实验性 x64 重编译，默认关闭；仅在任务需要且使用可丢弃副本时考虑 |

仅在基址与入口已有证据时，按实际值使用以下形式：

```sh
novmp /workspace/case-example/novmp/run-001/unpacked.exe -base 0x140000000 -vms 0x729B81
```

`RVA = VA - image base`，计算结果还需对应到 VMEnter 指令。节名只作识别信号，不证明版本或虚拟化已解除。

## 产物与验证

NoVmp 在输入文件旁建立 `vms/`。提升成功的函数可生成 `*.premature.vtil`，优化后可生成 `*.optimized.vtil`；`-noopt` 不要求有优化产物。实验性重编译还可能生成 `*.devirt.*` 映像。

1. 保存工具版本、完整参数、输入及产物 SHA-256、日志、退出状态和超时状态。原始样本保持只读，记录工作副本的来源。
2. 核对实际发现、提升、优化的 VM 例程数和对应 RVA；区分未发现、部分提升、优化失败及完整处理。
3. 检查 VTIL 中相关函数的控制流、常量与调用，和原始虚拟化函数的静态证据比对；无法确认语义时写明未验证。VTIL 是中间表示，不能直接宣称已恢复原生汇编。
4. 如启用重编译，另用 PE 解析器和反汇编器检查新映像及对应函数；可加载性、语义一致性、可运行性分别报告。动态验证仍需沙箱。
5. 写明剩余 VM 覆盖与失败原因；零退出码、空 `vms/`、PE 能打开均不能证明去虚拟化成功。

上游末尾调用 Windows `pause`，Linux 可能出现 `sh: 1: pause: not found`，同时返回 0。结合前面的提升日志和实际产物判断；该信息既不单独证明失败，也不证明成功。

已知限制包括跳转表支持、优化传播耗时和移除重定位的映像兼容性；原生编译器尤其不稳定。超时或崩溃时保留部分产物与日志，按函数报告进度，不能宣称全部解除保护。

## 来源与本地验证范围

- 上游：[can1357/NoVmp](https://github.com/can1357/NoVmp)，源码版本 `6c23c9a335f70e8d5ed6299668fd802f2314c896`（2021-05-05）。本页已摘录所需说明，离线时无需打开 URL。
- 本地安装验证（2026-10-15）：两个运行镜像的命令启动、帮助、版本、无保护自制 PE64 解析及输出目录检查通过。**尚未用实际 VMProtect 样本验证去虚拟化效果**；不能把安装检查写成目标样本处理成功。
