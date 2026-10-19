# PE：OEP、dump、IAT（本地摘录）

综合公开教程里的同一套流程：经典调试器路线，以及 hasherezade 2025 年用 TinyTracer + HollowsHunter 避开调试器的路线。命令和判断标准都在这里。

## 为什么 dump 之后还要修导入

壳通常清掉或加密原来的导入目录，自己调用 `LoadLibrary` + `GetProcAddress`（或更底层的等价函数），把本次运行的函数地址填进 IAT。

内存里的 IAT 是绝对地址。直接把这块内存存成文件，下次加载时 ASLR 把 DLL 放到别的基址，这些地址全部失效，程序一调用就崩。

修复的目标是让文件重新带有「DLL 名 + 函数名（或序号）」，由系统加载器再填地址。Scylla 的 Fix Dump 会加一节（常见名 `.scy`），把入口改成你填的 OEP，并把导入目录指到新表。

同一 DLL 的函数地址在 IAT 里连续存放，不同 DLL 之间用 0 隔开。扫描器找的就是这种指针串。

## 经典四步

1. 找到 OEP：stub 已把原始代码写回内存，下一条将执行的原始指令。
2. 在这个时刻 dump 模块映像。FSG 一类破坏了 IAT 的壳，dump 时不要勾「重建输入表」，交给后面的导入工具。
3. 用 Scylla 或 ImpREC：填 OEP 的 RVA → IAT Autosearch → Get Imports → 去掉无效项 → Fix Dump。
4. 检查节区属性。内存里节可能被改成可写；dump 落盘后若缺写权限，一写数据就异常。对照原样本把特征改回去。

OEP 的三种启发式（都要和「代码看起来像编译器产物」一起看）：

- **ESP 定律**：入口有 `pushad`/`pushfd` 时，对保存现场后的栈设观察，`popad` 附近再出现远跳，跳过去的地方是候选 OEP。没有这对指令就不要用。
- **节区转移**：空的、文件 Raw Size 为 0 但标记可执行的节，通常是待填充的原始代码。日志里第一次从 stub 节进入该节的地址，就是候选 OEP。多层来回跳时，关注最后一次进入原始节，而不是第一次。
- **导入风暴之后**：tracelog 里连续出现 `LoadLibrary` + `GetProcAddress`，随后 `VirtualProtect`，再出现节区转移。这是 stub 填完 IAT、准备交棒。

单步跟踪是兜底：从入口一步步走，直到 jmp/ret 进入一块新的、低熵的代码。慢，但是小众壳往往只有这条路。

## hasherezade 2025：不挂调试器

适用：经典单 stub（Type I 一类），包括改过、`upx -d` 报 `CantUnpackException: file is modified/hacked/protected` 的 UPX。  
不适用：VMProtect、Themida 等虚拟化。这条路线仍可能给出有用的静态材料，但产物通常不能当「已还原、可再运行的原程序」。

工具：PE-bear 看头，TinyTracer（Intel Pin）记轨迹，HollowsHunter 或 PE-sieve 负责 dump 和导入重建。

### 先看头

节名异常、入口落在第二个壳节、第一个节 Raw Size 为 0 却可执行：第一个节就是预期的原始代码落点。入口节是 stub。

样本若把可选头里的系统版本标成很老的 Windows（例如 XP），现代系统会套兼容性 shim。shim 经 `apphelp` 转发 API，导入重建会指错模块。

### 用轨迹认 OEP，并去掉 shim

TinyTracer 的好处：Pin 不是调试器，多数反调试打不中；轨迹里能看到 API 是经 `apphelp` 还是经真实 DLL；可以在指定 RVA 暂停，不必下断点。

轨迹里若出现 `apphelp.[SE_GetProcAddressForCaller…]` 而不是 `user32.GetDC` 这种真实导出，说明 shim 还在。把可选头的 OS Version 改成 Windows 10（主版本 0xA）后再跟踪。同一偏移若变成 `user32` / `gdi32` 的真实函数，shim 已去掉。

有的程序离开 shim 就无法启动。那时只能在对应旧系统上跑再 dump，不要在新系统上硬改。

OEP 读法：在轨迹里搜 stub 节到空节的转移，例如 `[1111] -> [0000]`，下一行 `section: [0000]` 的地址就是候选 RVA。示例里是 `1d14b0`。

### 在 OEP 暂停再扫

把该 RVA 写入 TinyTracer 目录的 `stop_offsets.txt`。默认暂停约 30 秒，不够就改 `TinyTracer.ini`。轨迹出现：

```
# Stop offset reached: RVA = 0x1d14b0. Sleeping 60 s.
```

立刻执行：

```
hollows_hunter.exe /pname packed.exe /hooks /imp A
```

`/pname` 按进程名扫（PE-sieve 本身只认 PID）。  
`/hooks`：HollowsHunter 默认不扫钩子和补丁；壳把某节整段覆写，必须打开这项才会当成补丁 dump。PE-sieve 默认会扫钩子，两边默认值不同。  
`/imp A`：自动重建导入。

### dump 之后仍要改两处

1. 入口仍指向 stub。用 PE-bear 改 Optional Header 的 AddressOfEntryPoint，或在反汇编里把 OEP 那条指令设为新入口。
2. 节特征在内存里被改过。若运行报错，把节属性改回原样本上的值（教程样本里原节是 rwx）。

改完应能运行。能运行仍要再看导入是否完整、有没有只解开一层。

## Scylla 的按钮顺序

进程必须活着，并且停在 OEP，不能让它继续跑或退出。

1. 选中该进程。
2. OEP 填 RVA（虚拟地址减去映像基址）。
3. IAT Autosearch：在代码附近找指向系统 DLL 的指针串，给出起始和大小。
4. Get Imports：把每个指针解析成 DLL + 函数。无效项删掉或手工改范围，直到 IAT 起止把整张表包住、不把表外数据包进来。
5. Dump 得到 `*_dump.exe`。
6. Fix Dump 选这个文件，得到 `*_dump_SCY.exe`。

Autosearch 扫不全时，在内存里顺着 IAT 槽的赋值往回看，手工填起始 RVA 和大小。现代壳会把槽填成 trampoline 而不是 API 本体，Get Imports 会解析失败或解析到壳代码。那种情况不要硬修，改走下面的跳板处理。

## 三个工具怎么分工

- PE-sieve：扫一个 PID。认替换/注入的 PE、shellcode、钩子、内存补丁、Process Hollowing、Doppelgänging、反射 DLL。
- HollowsHunter：多进程或全系统；钩子扫描要手动加 `/hooks`。
- MalUnpack：输入是样本路径。它自己启动，反复扫描，发现内存里出现解开的映像就 dump，子进程也跟，然后结束主进程和子进程。适合不想先手找 OEP 的分诊。暂停窗口和导入质量通常不如「先定位 OEP 再扫」。
