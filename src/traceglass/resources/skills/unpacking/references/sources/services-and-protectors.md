# 沙箱服务与商业保护器（本地摘录）

## Unpac.me 实际做什么

它只自动解 Windows PE。流程是：一组静态和动态 unpacker 递归处理；抽出的 child 若仍加壳，继续解；分类器给每个 child 打分，选最高分作为展示结果。Parent 是提交的原文件，child 是从它抽出的 PE。一个 parent 可以有多个 child；出现 child 才把 parent 视为加壳。Parent 和 child 还会做 PE 元数据、字符串、恶意软件配置提取。

状态词：Validating、Unpack-Queued、Unpacking、Unpack-Requeue、Analyzing、Complete、Waiting、Timeout、Fail、Invalid。Timeout 仍可能带回不完整结果。

硬限制：

- 只把完整 PE 交回用户。过程中可能看到 shellcode，但不会当结果返回。有时用回收的代码段拼一个 PE，这种文件的头和节表可能是造的，文件可能损坏、不能执行，导入可能缺或是坏的。
- 动态分析不允许出网，不下载下一阶段。child 都来自 parent 内部。
- 加壳和混淆被分开。加壳时期望恢复大部分原始二进制；混淆是变换后回不到原文件，只是让输出更好读。服务只解壳，不去混淆。这使大量 .NET 提交看起来「没解开」，因为它们主要是混淆。
- VMProtect / Themida：只接受恶意软件样本。提交游戏等商业软件会封号。不做方法级去虚拟化。保护选项阻止完全解开时，可能返回半解开 parent 的内存 dump，child 不能执行，但可以做一部分静态分析。
- 失败原因包括壳认错，或分类器选错 child。

## CAPE

从 Cuckoo 发展来的恶意软件沙箱，目标是抽出载荷和配置，而不只是跑行为。用自带调试器和 API hook。YARA 或 API 签名决定是否换 package、在何处停下。断点可以下在指令、内存区域或函数调用；到达后可以改内存并 dump，再做配置解析。

每个进程会有一份进程 dump；样本是 DLL 时，dump 的是该模块在内存中的映像。简单壳往往这一份就已经解开。

另有一个 package 专门处理被改过的 UPX：在调试器里跑到 OEP，dump，修复，并自动重建导入。这和 `upx -d` 失败后的手工路线是同一目标，只是自动化了。

家族 package 是在解开之后抽取配置，不要把它当成通用脱壳器。

## Demida（Themida / WinLicense / VMProtect）

原生 C++，在 Windows 调试器下启动目标，等到控制权到达 OEP，抓住当时的映射映像，再重建一个更干净的 PE。不是纯静态解压。

- 能识别当前逻辑覆盖的 Themida、WinLicense 2.x/3.x 布局。
- VMProtect 要有「未被覆盖的原始 `.text` 范围」，并且通过一个加壳的可执行 stub 转移控制权，才会走对应 profile。
- VMProtect profile 复用通用 OEP dump，但不用 Themida 那套导入恢复和节清理。它把输入里可见的导入收成干净的 `.idata`，并尽量保持原来的 IAT RVA。这一步失败时，非严格模式会留下运行时 dump 的目录，并报告导入恢复失败。

明确不做：不去虚拟化函数，不恢复原来的编译器控制流，不恢复符号，不保证字节级一致。

对 Themida/WinLicense，目标是让壳失效，得到头部、导入、目录、节结构都恢复过的、可分析和尽量可运行的文件。  
对 VMProtect，只去掉到达 OEP 之前的 packing 层，虚拟化函数仍是 VM 字节码。

参数：`--profile auto|themida2|themida3|vmprotect`，默认 auto。`--target-version 2|3` 是旧的 Themida/WinLicense 主版本选择。

若用空导出垫片闯过「加载时缺 DLL / 缺序号」，那些垫片是空的 x64 函数。依赖真实 API 行为的代码过了 OEP 仍会失败。垫片只说明加载器没有立刻拒绝，不说明程序逻辑正确。

## ThemidaUnpacker

Python 动态工具，覆盖 Themida/WinLicense 2.x 和 3.x，32/64 位 PE（EXE 和 DLL），以及 32/64 位 .NET EXE。会执行目标，必须在虚拟机里用。32 位目标要用 32 位 Python。

能自动找 OEP 和（可能仍混淆的）导入表。不能处理 .NET DLL。多数情况下产物不能再运行。Themida 2.x 的 32 位导入解析很慢。带许可证校验的 WinLicense 样本，没有有效许可证文件就启动不了，工具也解不开。

常用开关：`--pause_on_oep`、`--no_imports`、`--force_oep`、`--target_version`、`--timeout`（默认 10 秒，复杂样本经常不够）。

## vmpunpack

用改过的 sogen 模拟器把 x64 PE 送到 OEP，dump，再建成 IDA/Ghidra 能加载的映像。作者说明它能走过一些让 VMProtect 在在线沙箱和裸机上直接退出的反虚拟机、反调试检查。

OEP 启发式：在「不包含壳入口、RVA 最低的可执行节」上放执行钩子，第一次命中当成 OEP。这是启发式。工具会报告 OEP 落在哪一节、该节是否可执行、OEP 是否其实还等于壳入口；OEP 所在页是空的则拒绝发布映像。有警告的结果当可疑，不当成功。

同样声明：解开 packing，不去虚拟化。虚拟化函数保持 VM 字节码。

`--run-payload` 会越过 OEP 继续跑，而不是停在那里。`--oep` 可强制地址。超时默认量级是数分钟一级，超时产物不要当成完整解开。

## NoVmp（packing 层之后的静态去虚拟化）

上游标注支持已脱壳的 x64 PE / VMProtect 3.0–3.5，通过 VTIL 提升和优化虚拟化函数。它与前面的 OEP dump 工具分属不同阶段；已脱壳输入可以直接进入该阶段。原生重编译是实验功能，不默认开启。输入、命令与结果验证见 [novmp.md](novmp.md)。

## 写进报告时的固定句子

- 「Unpac.me/CAPE/Demida/vmpunpack 的 dump」只支持「packing 层被去掉或被抽出」。
- 若保护器是 VMProtect 或 Themida 的虚拟化，下一句必须是：被虚拟化的函数仍是 VM，本步骤没有还原原始汇编。
- child 不能执行时，仍可以保留它做静态分析，但要写明头、节表或导入可能是工具补出来的。
- 后续运行 NoVmp 时单独报告「哪些函数已提升为 VTIL、哪些仍为 VM、是否验证原生代码」；不能用后一阶段的产物扩大前一步 dump 的结论。
