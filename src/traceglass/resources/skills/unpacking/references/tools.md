# 工具对照

先选一类，再开具体程序。命令、开关和能力边界在 `sources/`，不要打开文中的 URL。没在本机跑过的不要写结果。

## 查壳 / 分诊

| 工具 | 对象 | 作用 |
|------|------|------|
| [Detect It Easy](https://github.com/horsicq/Detect-It-Easy) | PE/ELF/Mach-O 等 | 签名 + 熵，第一选择 |
| PEiD / YARA 再实现 | PE | 旧签名库；单独命中只是候选 |
| [APKiD](https://github.com/rednaga/APKiD) | APK | Android 壳/混淆/编译器 |
| [capa](https://github.com/mandiant/capa) | PE/ELF/.NET | 能力标签，辅助判断是否还在 stub |
| [LIEF](https://github.com/lief-project/LIEF) | PE/ELF/Mach-O/DEX | 解析、改写、重建 |
| [pefile](https://github.com/erocarrera/pefile) | PE | Python 解析 |
| [Bintropy](https://github.com/packing-box/bintropy) | 通用 | 压缩/加密字节估计 |
| `file` / `readelf` / `objdump` | ELF | 基线 |

### Traceglass DIE 调用

本地主沙箱的 DIE 已通过 reverse MCP 注册，可在 Linux 中静态识别 PE/ELF/Mach-O，
无需为查壳启动 Windows 调试器。

1. 用 `PrepareAnalysisInput(path="samples/example.exe")` 核对当前工作区文件及 SHA-256。
2. 用 `SearchAnalysisTools(query="mcp__reverse__detect_packer")` 找到本地工具。
   按搜索结果的 `reset_tools` 参数启用 `reverse_malware`，保留已启用且仍需的组；
   读取实际 schema 后调用。
3. 调用 `mcp__reverse__detect_packer(sample_path="example.exe")`。
   底层执行 `diec <样本>`，保留实际返回的文本和归档回执，不假定返回 JSON。
4. 对照节区、熵、导入和入口验证签名线索；未命中、超时和工具错误分别记录。

两个后端的路径规则不同：

| 入口 | 样本参数 | 使用条件 |
|------|----------|----------|
| 本地 `mcp__reverse__detect_packer` | `sample_path="example.exe"`，相对 `/workspace/samples` | 常规静态查壳首选 |
| Windows `WindowsRunTool` | `sample_path="samples/example.exe", profile="die"`，相对工作区 | 用户指定 Windows 或本地入口不可用，且 Windows 能力已确认 |

输入在 `uploads/` 等其他目录时，在当前工作区把只读内容复制到 `samples/`，用
`PrepareAnalysisInput` 核对副本与原件 SHA-256 一致后，传相对 samples 的路径。
不要更改原件，也不要把 `rea_file_path` 或宿主机路径直接传给本地工具。

Windows 入口需先搜索并启用 `windows_analysis`，调用 `WindowsCapabilities` 确认
`profiles.die`，提交后用 `WindowsJobStatus` 查询并通过 `WindowsCollectResults` 回收结果。
主沙箱安装了 DIE 不代表 REA 容器也安装了它。若本地工具未注册或执行失败，记录实际
缺口；仅在当前工作区确认 `diec` 可用时，才用 Bash 做静态扫描并保留命令与原始输出。
DIE 只识别签名，不会自动脱壳或还原虚拟化函数。

## 静态可逆 / 捆绑抽取

| 工具 | 对象 | 作用 |
|------|------|------|
| [UPX](https://github.com/upx/upx) `upx -d` | PE/ELF/Mach-O | 原版压缩器官方解压 |
| [pyinstxtractor](https://github.com/extremecoders-re/pyinstxtractor) | PyInstaller | 抽 PYZ/pyc |
| [pyinstxtractor-ng](https://github.com/pyinstxtractor/pyinstxtractor-ng) | PyInstaller | 独立二进制，支持加密包 |
| `npx asar extract` | Electron | 展开 ASAR |
| unzip / apktool / jadx | APK/JAR | 资源与清单；加固包的 DEX 可能无意义 |

## PE 动态 dump 与导入重建

| 工具 | 作用 |
|------|------|
| [Scylla](https://github.com/x64dbg/Scylla) | 停在 OEP 后：IAT Autosearch → Get Imports → Dump → Fix Dump |
| ImpREC | 旧版导入重建； scattered IAT 弱于 Scylla |
| [PE-sieve](https://github.com/hasherezade/pe-sieve) | 扫单进程：植入、补丁、hollow；`/imp` 重建导入 |
| [HollowsHunter](https://github.com/hasherezade/hollows-hunter) | 按进程名/系统范围扫；unpack 时加 `/hooks /imp A` |
| [MalUnpack](https://github.com/hasherezade/mal_unpack) | 启动样本并反复扫，出现新映像就 dump，然后结束进程 |
| [TinyTracer](https://github.com/hasherezade/tiny_tracer) | 执行轨迹，用来定位候选 OEP，避免手写 stub 断点 |
| [LordPE](https://www.aldeid.com/wiki/LordPE) | 旧工具：看节区属性、手工改头 |
| CFF Explorer / PE-bear / StudPE | 检查 dump 头、节区、目录 |

2025 年通用 PE 路线参考：hasherezade [TinyTracer + PE-sieve](https://hshrzd.wordpress.com/2025/03/22/unpacking-executables-with-tinytracer-pe-sieve/)。

## 模拟 / DBI / 混合

| 工具 | 类型 | 备注 |
|------|------|------|
| [Unipacker](https://github.com/unipacker/unipacker) | Unicorn 用户态 | 跨平台脱 32 位 PE；ASPack/FSG 等有策略 |
| [Qiling](https://github.com/qilingframework/qiling) | 用户态模拟 | 常作 64 位 fallback，需 rootfs |
| [Pinicorn](https://www.mdpi.com/2079-9292/13/11/2081) | Pin + Unicorn | 处理 OEP/IAT trampoline |
| [GUARD](https://github.com/unlockable/GUARD) | Pin + sIAT | SAC 2025；要 PID 与 OEP |
| [PINdemonium](https://github.com/enkomio/PinDemonium) | Pin | 通用 PE unpacker |
| Intel Pin / DynamoRIO | DBI 框架 | 自己写观察逻辑时用 |
| [x64Unpack](https://repository.hanyang.ac.kr/bitstream/20.500.11754/168823/1/x64Unpack%20Hybrid%20Emulation%20Unpacker%20for%2064-bit%20Windows%20Environments%20and%20Detailed%20Analysis%20Results%20on%20VMProtect%203.4.pdf) | 混合模拟 | 论文系统，Bochs + 本地执行 |
| [Xunpack](https://dl.acm.org/doi/10.1145/3607199.3607214) | QEMU 全系统 | 跨架构 ELF，多次 dump 拼接 |
| PANDA | 全系统 | 架构中立动态分析，可做 packing 检测 |

## 商业保护（只脱 packing 层）

| 工具 | 对象 | 明确不做 |
|------|------|----------|
| [Demida](https://github.com/ck0i/Demida) | Themida/WinLicense/VMProtect | 不去虚拟化 |
| [ThemidaUnpacker](https://github.com/TopSoftdeveloper/UnpackThemida) | Themida 2.x/3.x | 多数 dump 不可再跑 |
| [VMPDump](https://github.com/0xnobody/vmpdump) | VMProtect 导入 | 重建可见 IAT |
| [vmpunpack](https://github.com/milk-analyzer/vmpunpack) | 模拟到 OEP 后重建 PE | 不去虚拟化 |

## 去虚拟化（单独阶段）

| 工具 | 输入与作用 | 边界 |
|------|------------|------|
| [NoVmp](https://github.com/can1357/NoVmp) / `novmp` | 已脱壳 x64 PE，VMProtect 3.0–3.5；静态提升并优化为 VTIL | 上游为实验性 PoC；不解 packing 层，不覆盖 Themida/x86，原生重编译默认关闭 |

Traceglass 分析与 REA 镜像已安装；通过分析工作区 Bash 操作可写副本，没有专用 NoVmp MCP 方法。详见 [参数、输出与验证](sources/novmp.md)。

## 沙箱 / 服务

| 工具 | 作用 | 约束 |
|------|------|------|
| [CAPE](https://github.com/kevoreilly/CAPEv2) | 沙箱 + 调试 unpack + 家族配置提取 | 自建；改 UPX 有专用包 |
| [Unpac.me](https://www.unpac.me/) | 递归解 Windows PE | 断网；不脱商业软件；不保证去虚拟化 |
| any.run 等在线沙箱 | 行为分诊 | 不是专用 unpacker |

## Android

| 工具 | 作用 |
|------|------|
| APKiD | 查壳 |
| [frida-dexdump](https://github.com/hluwa/frida-dexdump) | 从 ART 导出已加载 DEX |
| [frida-packing-detector](https://github.com/poping520/frida-packing-detector) | 运行时判断「是否加固 / 是否已解开」 |
| BlackDex / DexHunter / Youpk / PackerGrind / AppSpear | 历史或学术 unpacker；对现代商业壳经常失效 |
| jadx / apktool | 分析 dump 出的 DEX、重打包资源（分析用） |

## .NET

| 工具 | 作用 |
|------|------|
| [dnSpyEx](https://github.com/dnSpyEx/dnSpy) | 看 IL、调试、dump 模块、补入口 |
| [de4dot](https://github.com/de4dot/de4dot) | 解包 + 去混淆；Confuser 用 `-p crx` 等 profile |
| [AsmResolver](https://github.com/Washi1337/AsmResolver) / dnlib | 写修复/解密工具时的库 |
| [de4dot-cex / ProxyCall-Remover 等社区叉](https://github.com/NotPrab/.NET-Deobfuscator) | 新版本 ConfuserEx 家族 |

## 分析器（脱壳之后）

IDA、Ghidra、Binary Ninja、radare2、x64dbg/x32dbg、jadx、dnSpyEx、Ghidra ELF loader。脱壳 skill 默认在「映像可打开」处结束；任务要求去虚拟化时可接 NoVmp 阶段，后续算法恢复交给逆向 skill。

## 工具箱发行版

| 项目 | 作用 |
|------|------|
| [awesome-executable-packing](https://github.com/packing-box/awesome-executable-packing) | 文献、壳、检测器、unpacker 总表 |
| [packing-box](https://github.com/packing-box/packing-box) | 研究用 Docker，内置大量 packing 工具 |
| [REMnux](https://remnux.org/) | 恶意软件分析 Linux 发行 |

## 选用默认

1. 查壳：DIE；APK 再加 APKiD。
2. 已知 UPX：`upx -d`。
3. 未知 PE 且允许动态：MalUnpack 或 TinyTracer + PE-sieve。
4. 需要可加载 PE：Scylla 或 PE-sieve `/imp`。
5. 分析机不是 Windows：Unipacker（32 位）或 Unpac.me / CAPE。
6. Android：APKiD → 运行时 DEX dump → jadx。
7. .NET：dnSpyEx → 必要时 dump → de4dot。
8. PyInstaller：pyinstxtractor-ng。
9. 已脱壳 x64 PE / VMProtect 3.0–3.5，且任务包含去虚拟化：NoVmp → 检查 VTIL 与覆盖范围。
