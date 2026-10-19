# 模拟、插桩、混合执行（本地摘录）

## 谱系里各自解决什么

- PolyUnpack（2006）：比较「静态能看到的代码」和「实际执行到的代码」，抽出隐藏指令。核心观察是写完再执行。
- Renovo：在模拟器里盯新生成并被执行的代码，用来判断解包结束。
- OmniUnpack：更粗粒度，追求快，适合大规模，精度换速度。
- Pandora's Bochs：改 Bochs，监视 stub 执行并抽出原始代码。全系统，比用户态模拟更能碰到真实内核行为。
- 这些系统默认样本接近 Type I–III。对 Type V/VI，结束条件会过早或过晚。

## Unipacker（Unicorn，用户态）

目标是在非 Windows 分析机上自动脱 PE，并尽量得到能运行的文件。从入口模拟，直到它认为解包结束（节区跳转、出现写后执行区域，或某个壳的专用逻辑），再把映像基址起、长度为 SizeOfImage 的内存写成新 PE。

对 UPX、ASPack、FSG、MEW、MPRESS、PeTite、YZPack 一类有专门策略。未知壳只要它用到的 API 已在模拟层实现，也可能走出 stub；否则要手工指定模拟的起止地址。

32 位是它的主场。Themida/VMProtect 往往只是「模拟到启发式触发再 dump」，没有壳专用逻辑，产物经常是半解开。缺 API、反模拟、需要真实设备时会停在 stub 中途。

## Qiling

64 位、Unipacker 覆盖不到时的退路。用 Windows rootfs 提供模拟 DLL，跑一段带超时的模拟，结束或超时后读已加载映像。没有「解开完成」的专用检测，重保护器经常只解开一部分。超时 dump 必须标注为不完整。

## Pinicorn（Pin + Unicorn，2024）

对象是 32 位 PE。要解决的不是「找不到任何代码」，而是 OEP 和 IAT 被 trampoline 骗了：

- 壳把入口改到 bootstrap。bootstrap 在内存里暂时恢复原程序。OEP 混淆会让解包器停在跳板，而不是原程序第一条指令。
- 正常程序的 IAT 由加载器填。壳删掉导入表，自己填。新壳在 IAT 槽里放跳板，解包器把跳板地址当成 API 地址，重建失败。

做法分两段：

1. Pin 插桩执行，避开一部分反分析，把暂时解开的进程 dump 下来，同时收集 OEP 候选。
2. 对 dump 做静态分析，找出跳板；再用 Unicorn 只执行这些跳板，取出它们最终指向的真实 OEP 或 API，用来重建结构和导入表。

论文在三种壳上与原程序对比过，并在 Themida、VMProtect 样本上验证跳板分析。这仍是去混淆入口和导入，不是把 VM 字节码还原成编译器汇编。

## GUARD（Pin，SAC 2025）

全称 Generic Unpacking with API Restoration and De-obfuscation，用 sIAT 恢复被混淆的 API。公开实现配合 Pin 3.22。

- OEP 搜索借鉴 Lee 等人 2021 年用 DBI 绕过商业保护器反分析的做法。
- VMProtect 3.6 之前默认的 Heaven's Gate，用同一作者的黑盒绕过实现处理。
- IAT 以 VMPDump 为底，把原来的 VTIL 路径换成 Unicorn，并加上 sIAT。

使用方式是两段：先用 Pin 跑到 OEP；再对已经运行的进程调用 `GUARD.exe <PID> "" -ep=0xOEP ""`。没有 OEP 就不要空跑 GUARD。

## x64Unpack（混合执行）

问题：纯模拟慢且缺 OS/设备；纯本地执行快，但不好做指令级观察。x64Unpack 和目标共享虚拟内存，细粒度部分用 Bochs 模拟，复杂的 OS/硬件块直接执行。

因此可以看主要 API，也可以下到指令级；能标出反调试块、dump 内存。论文对默认配置的 UPX 3.95、MPRESS 2.19、Themida 2.4.6、VMProtect 3.4 做了实验，报告这些默认保护被解开。对 VMProtect 3.4 的结果是「模拟并产出 unpacked 版本」，后续分析描述的是混淆后的文件，不要理解成每个虚拟化函数都变回了原汇编。

## Xunpack（QEMU 全系统，RAID 2023）

对象是 Linux IoT 恶意软件，设计成不依赖具体 CPU：钩在 QEMU TCG 的架构无关层。

两个触发 dump 的事件：

1. 执行了自修改代码。
2. 从内核返回到目标用户态代码。

用系统级而不是进程级模拟，是因为 Unicorn、Zelos 一类用户态工具会被真实 ELF 壳里的反分析躲开。作者用同平台的对照实现说明：那些工具效率高，但抗反分析弱。

SelectiveDump：从多次 dump 里为每一段代码挑选最合适的副本再拼。目标包括 Type VI（函数执行前解开、执行后重新加壳）。实验声称覆盖 14 种架构的加壳 ELF，并超过当时的主要 ELF unpacker；也包括当时恶意软件里少见的 SPARC、RISC-V。

ELF 产物通常还要人看 GOT/PLT 和程序头。没有 PE 那种 Scylla 式标准修复器。

## 反模拟

用户态模拟失败，不表示样本没有解开逻辑。常见原因是缺 API、缺设备、时间检测、CPUID/虚拟化痕迹。下一步是换全系统或混合执行，或在隔离的真实系统里用 Pin/调试器观察。不要在分析机宿主机上取消隔离重试。
