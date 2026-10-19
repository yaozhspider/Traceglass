# 脱壳方法分类

先读 [SKILL.md](../SKILL.md) 的决策树。具体定义和步骤在 `sources/`，不要打开文中的 URL。

## 壳的两套分类

### 产品功能

| 类型 | 做什么 | 典型 | 单次 dump 的上限 |
|------|--------|------|------------------|
| Compressor | 压缩，运行时解压到内存 | UPX、MPRESS、FSG、ASPack | 通常可得到完整原始映像 |
| Crypter | 加密载荷 + 解密 stub | 大量自制/恶意软件壳 | 解密完成后可 dump |
| Protector | 反调试、反 dump、偷字节、混淆导入 | Themida、Enigma、Armadillo | dump 后仍要修头/导入 |
| Virtualizer | 把原生指令译成自定义 VM | VMProtect、Themida VM、Code Virtualizer | **不会**还原被虚拟化的函数 |
| Bundler | 把多个文件打成一个可执行 | PyInstaller、Electron、Enigma Virtual Box | 应做格式抽取，不是找 OEP |
| Runtime loader | 启动后再解密并加载真正代码 | Android 加固、.NET module cctor | 在加载器交棒后抓托管映像 |

### 运行时复杂度（Ugarte-Pedrero Type I–VI）

来源：[SoK: Deep Packer Inspection](https://www.ieee-security.org/TC/SP2015/papers-archived/6949a659.pdf)（IEEE S&P 2015）。

| Type | 行为 | 对脱壳的含义 |
|------|------|----------------|
| I | 一层 stub，解完后 tail jump 到原始程序。UPX 是典型 | 在 OEP 一次 dump 即可 |
| II | 多层顺序解开，最后一层交还控制权 | 可能要等最后一层；或对 child 循环脱 |
| III | 解包例程有循环/复杂拓扑，原始代码不一定在最深层 | 不要假设「最后一次写内存就是 OEP」 |
| IV | 原始程序跑起来后，仍会跳回壳的保护逻辑 | 存在「全部已解开」的时刻，但 tail jump 更难认 |
| V | 按 frame 边跑边解；跑完后内存里才凑齐已执行过的代码 | 需要多次快照或跑完再拼 |
| VI | 同一时刻只暴露极小片段（可到单条指令），用完再收走 | 单次 dump 必然不完整；要选择性拼接多次 dump |

Xunpack 把「函数级解开、用完再加回」的 IoT 壳也归到 Type VI。见 [RAID 2023](https://dl.acm.org/doi/10.1145/3607199.3607214)。

## 方法族

下面按「谁来解、在哪观察、产物如何修」分组。同一案例常要组合使用。

### A. 静态官方 / 格式可逆

适用：原版压缩器、未改格式的捆绑器。

- 官方解压：`upx -d`。改过 stub 的 UPX 变种常失败。
- 静态重放 stub：读出压缩算法（aPLib、LZMA、NRV）和密钥，在分析机上解，不运行样本。
- 捆绑抽取：按格式切 PE overlay、PYZ、ASAR、ZIP/APK。

优点：不执行样本。失败信号：魔数不对、校验失败、入口已被改写。

### B. 经典动态四步（OEP → dump → IAT → 修头）

适用：Type I–II 的 PE 压缩/加密壳。这是 Windows 上最常见的人工路线。

概念顺序：

1. **找 OEP**：stub 把原始代码写回内存后，第一条原始指令的地址。
2. **Dump**：把已映射映像从进程内存写到文件。
3. **重建导入**：内存里的 IAT 已是绝对地址；磁盘文件需要名字/提示名，供下次加载解析。
4. **修 PE**：入口改回 OEP，补节区属性、镜像大小、可选的重定位。

OEP 线索（启发式，需交叉验证）：

- 控制流从 stub 节跳到像编译器产出的代码节（常伴随 `jmp` / `ret`）。
- 入口附近的 `pushad`/`popad` 成对出现后，栈平衡并远跳（所谓 ESP 定律，只适用于这类 stub）。
- 对疑似原始 `.text` 设执行观察，第一次命中是候选 OEP。
- TinyTracer 一类执行日志里，第一次进入未改写前的代码节。

IAT 为什么要修：

- 壳常清掉或加密 IMAGE_IMPORT_DESCRIPTOR，自己用 `LoadLibrary`/`GetProcAddress` 填表。
- 生 dump 里保存的是本次运行的 VA；下次 ASLR 后这些地址失效。
- 现代壳还会在 IAT 槽里放 **trampoline**（跳板），直接当 API 地址会修错。见 Pinicorn、GUARD。

经典工具链：调试器 + Scylla/ImpREC。通用替代：TinyTracer + PE-sieve/HollowsHunter `/imp`。详见 [tools.md](tools.md)。

### C. 通用动态：写后执行 / 内存扫描

适用：不想依赖具体 stub 实现；Type I–IV 优先。

核心观察：原始代码几乎总是「先被写入，再被执行」。

- W+X / write-then-execute：监控页权限或写集合与执行集合的交集。
- 进程扫描：PE-sieve 认植入、补丁、hollow、反射加载；MalUnpack 反复扫直到出现新映像。
- 熵/节区跳变：执行从高熵 stub 进入低熵区。

限制：Type V–VI 会反复收走代码；反 dump 会钩内存读；OEP 用 trampoline 伪装。

### D. DBI（动态二进制插桩）

适用：要指令级或 API 级观察，且目标反调试主要针对调试器。

代表：Intel Pin（Pinicorn、GUARD、PINdemonium）、DynamoRIO、Frida（更多用于 Android/托管）。

常见能力：绕过一部分反调试、记写后执行、在候选 OEP 停住、导出内存。

限制：商业壳检测 DBI/Pin/Frida；Heaven's Gate 等过渡技术要额外处理。只记录「用了哪个公开工具」，不要编造未运行的插桩脚本细节。

### E. 用户态模拟

适用：分析机不是 Windows，或希望少暴露真实系统。

代表：Unicorn（Unipacker）、Qiling、sogen 一类用户态模拟器。

做法：从入口模拟，直到节区跳跃 / 写后执行 / 超时，然后把映像 dump 出来。

限制：API 覆盖不全、反模拟、64 位商业壳、需要真实内核对象时容易半解开。Unipacker 对 ASPack 等有专用策略，对 Themida/VMProtect 多是 generic dump。

### F. 混合执行 / 全系统模拟

适用：反调试强、要看内核交互、跨架构 ELF。

- 混合：细粒度模拟 + 把复杂 API/驱动丢回本地执行（x64Unpack：Bochs 共享地址空间）。
- 全系统：QEMU/PANDA/Bochs 抓「自修改」和「内核→用户」转换，再从多次 dump 里挑代码（Xunpack 的 SelectiveDump）。

代价高，但比纯用户态更抗反模拟。

### G. 沙箱 / 云服务

适用：分诊、多层递归、只要 child 哈希和配置，而不是手修每一个 IAT。

- CAPE：调试器 + YARA/API 触发，到 OEP 后 dump 并尝试修导入；对改过的 UPX 有专用包。
- Unpac.me：静态+动态递归解 PE，分类器选最佳 child；默认断网，不拉二阶段；不保证去虚拟化，也可能只返回半解开 dump。

提交前确认授权与服务条款：Unpac.me 明确拒绝用它脱商业软件/游戏。

### H. 托管运行时提取

适用：Android / .NET / JVM / Python 字节码。原生 IAT 流程通常用不上。

- Android：等加固 stub 把 DEX 解密进 ART 后再抓；钩 ClassLoader 或扫内存里的 `dex\n` 魔数。
- .NET：等 module constructor 跑完，dump 模块，补入口 Token，再交给 de4dot 一类去混淆。
- Python：先抽 PYZ/pyc，再反编译；加密 PyInstaller 用支持密文的抽取器。

现代加固会反 Frida、反内存 dump、只解密当前方法。见 [platforms.md](platforms.md)。

### I. 去虚拟化（单独阶段）

不是脱壳的默认步骤。VMProtect/Themida 的 VM 在 OEP dump 之后仍然在。

要声明：当前产物是「packing 层已去掉的映像，虚拟化函数仍是 VM bytecode」。只有用户明确要求、且有授权与专用工具时，才进入 handler 识别 / VM 轨迹分析。

对已脱壳的 x64 PE，若 VMProtect 版本落在上游标注的 3.0–3.5 范围，可通过分析工作区 Bash 使用 NoVmp 静态提升到 VTIL；无需为已脱壳输入再执行一次动态 dump。它不覆盖 Themida、x86 或任意新版 VMProtect。命令、地址单位和成功标准见 [NoVmp](sources/novmp.md)。原生重编译为实验功能，默认不启用。

## 选方法时的对照

| 信号 | 优先方法 | 不要先做 |
|------|----------|----------|
| DIE/节区名=UPX 且 `upx -d` 成功 | A | 调试 |
| 高熵 + 极少导入 + PE | C 或 B | 当未加壳反编译 |
| 节区 `.vmp*` / Themida 且 packing 层仍在 | B/C 只脱到 OEP，并标明虚拟化残留 | 宣称已还原全部代码 |
| 已脱壳 x64 PE，VMProtect 3.0–3.5，任务要求去虚拟化 | I：NoVmp → 检查各函数 VTIL 与剩余 VM | 重复动态 dump，或把 VTIL 当成已恢复原生代码 |
| APK 里 Application 是 stub、有 `libjiagu.so` 等 | H | 当普通 DEX 反编译结束 |
| dnSpy 里方法体空白 | H（先 dump 再去混淆） | 当 native PE 修 IAT |
| `pyinstxtractor` 能认出 PYZ | A 捆绑抽取 | 当 UPX 动态脱 |
| 单次 dump 只有部分函数 | Type V–VI → 多次快照 / F | 再找一次 OEP 幻想完整 |

## 验证与失败模式

| 现象 | 可能原因 |
|------|----------|
| dump 能打开但一跑就崩 | IAT 未重建，或节区缺可写/可执行 |
| 导入扫出来全是无效项 | trampoline IAT、扫描范围错、转表加密未解 |
| 入口仍在高熵节 | 还停在 stub，不是 OEP |
| 文件变大但 IDA 仍无意义 | 只解了外壳，核心在 VM 或按需解密 |
| 沙箱里进程秒退 | 反调试/反模拟；换观察通道，而不是在宿主机硬跑 |
| child 仍高熵 | 还有下一层，重新查壳 |
