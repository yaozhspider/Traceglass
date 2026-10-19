# 平台路线

只在 [SKILL.md](../SKILL.md) 已完成查壳分流后阅读。操作细节在 `sources/`，不要打开文中的 URL。

## Windows PE

最成熟的脱壳生态。目标通常是：可被 IDA/Ghidra 打开的 PE，最好还能修到可加载。

1. 查壳：Detect It Easy、节区名、熵、导入。
2. 原版 UPX / MPRESS：先官方或专用解压。
3. 通用 Type I–III：
   - 自动：MalUnpack、Unipacker（32 位）、CAPE、Unpac.me。
   - 半自动：TinyTracer 找候选 OEP → HollowsHunter/PE-sieve `/imp` dump。
   - 人工：调试器停在 OEP → Scylla：IAT Autosearch → Get Imports → Dump → Fix Dump。
4. Themida / WinLicense / VMProtect：
   - 先只要 packing 层：动态跑到 OEP，dump，尽量重建可见导入。
   - Demida、VMPDump、vmpunpack 一类工具都明确：**不去虚拟化**。
   - IAT 里的跳板按 Pinicorn/GUARD 的思路处理：不要把 trampoline 当成最终 API。
   - 若输入已经脱壳，不重复动态 dump；任务要求去虚拟化且为 x64 PE / VMProtect 3.0–3.5 时，可接 [NoVmp 静态路线](sources/novmp.md)，分别验证 VTIL 与原生代码恢复情况。
5. 验证：节区与入口、导入名、能否被分析器加载；「能运行」另算一项。

DLL 与 EXE 相同，但入口是 `DllMain`；dump 时对准模块基址，不要扫错模块。

## Linux / IoT ELF

工具链比 PE 少，反模拟更常见。

1. 识别：`file`、`readelf`、节区/段、UPX 魔数、是否静态链接。
2. 原版 UPX：`upx -d` 对 ELF 同样适用。
3. 自制 stub：观察 `mprotect`/`mmap` 把数据段改成可执行，或自修改后跳到新代码。
4. 跨架构（ARM/MIPS/RISCV 等）：优先全系统 QEMU 观察自修改与内核返回用户态，再从多次 dump 拼接（Xunpack）。纯 Unicorn 用户态容易被反模拟打死。
5. 重建：ELF 没有 PE 那种标准 IAT 工具；要修的是动态重定位、GOT/PLT、以及 dump 后的程序头/节头一致性。很多时候「能被 IDA 打开的 raw dump」就够分析。

## Android APK / DEX

静态包里的 `classes.dex` 经常只是壳。真正逻辑在运行时解密后由 ClassLoader 加载。

1. 查壳：APKiD；看 `Application` 是否厂商 stub；扫 `lib/*/lib*.so` 名称（jiagu、ijiami、legu、mobisec、secexe）。
2. 先不要当加固失败就结束静态分析：资源、清单、native 导出仍有信息。
3. 运行时提取（需授权设备/模拟器）：
   - 等启动 Activity 的类已经可以被运行时解析，再 dump（「解开完成」的启发式）。
   - 从 ART 已加载 DEX 列表导出（frida-dexdump 一类）。
   - 钩 `BaseDexClassLoader` / `DexClassLoader` / `InMemoryDexClassLoader`：有路径就拉文件，内存 DEX 没有落盘副本。
4. 验证：`dex\n` 魔数、jadx 能出类、清单里的真实 `Application` 出现在 dump 中。
5. 现实上限（2025）：商业加固普遍反 Frida、钩内存/文件 API、按需解密、JNI 里藏逻辑。学术/开源 unpacker 对现代样本经常 dump 不全。论文 [To Unpack or Not to Unpack](https://arxiv.org/abs/2509.16340) 主张：分析目标若是行为，可在壳仍在时做动态分析，而不是死磕完整 DEX。

厂商库名只作候选，不要当唯一判决。

## .NET

「看不见 IL」多半是打包/反篡改，不是 native 壳。

1. 识别：DIE / 是否 Managed PE；dnSpyEx 打开后方法体是否空、是否只有 cctor。
2. 若 IL 可见但混乱：直接去混淆（de4dot、控制流/代理调用清理），不必 dump。
3. 若方法体不可见：在授权调试器里等 **module constructor 结束**、即将进入原始入口时 dump 模块；尽量保留元数据。
4. 入口 Token 常被抹掉，dump 后要在 dnSpyEx 里补回。
5. 再按保护家族清理：Confuser/ConfuserEx 用对应 de4dot profile；ConfuserEx2 还常要单独的字符串解密与 ProxyCall 内联。
6. 成功标准：关键方法能反编译出合理 IL/C#，而不是「文件变大」。

## Python 捆绑

PyInstaller、py2exe、cx_Freeze、Nuitka 不是同一类问题。

- **PyInstaller**：`pyinstxtractor` / `pyinstxtractor-ng` 抽 overlay 与 PYZ；pyc 头需与 Python 版本匹配；然后 uncompyle6 / pycdc。加密包用 ng 一类支持密文的版本。
- **py2exe / cx_Freeze**：按各自归档格式抽 `.pyc`。
- **Nuitka**：生成原生代码，应按 PE/ELF 分析，不能当 pyc 抽。
- 外壳若再套了 UPX：先解 UPX 再抽 PYZ。

## Java / JS / Electron

- JAR 本质是 ZIP。先解包再看混淆器（Allatori、Zelix、Allatori、ClassGuard）。那是去混淆，不是内存 dump。
- 商业 Java protector 若把字节码加密到自定义加载器：等自定义 ClassLoader.defineClass 之后抓字节，再当普通 class 分析。
- Electron：先 `npx asar extract`；再分析打包的 JS。JS 混淆走 deobfuscator，不要走 PE OEP。

## Mach-O / Apple

生态比 PE/Android 小。

- UPX 也能打 Mach-O，先试官方解压。
- App Store / FairPlay 是 DRM，不是本 skill 的脱壳对象。
- 自制加密段：观察 `mprotect` 与 `__TEXT` 写权限变化，再 dump 对应 segment。
- 静态检测论文较少，特征仍是高熵节 + 异常入口 + 少导入。

## 多层与混合包

常见组合：

1. UPX + 自制 XOR。
2. PyInstaller + UPX。
3. APK 加固 + 资源加密 + native 反调试。
4. PE 外壳 drop 第二个 PE。

对每一个 child 重新跑查壳。Unpac.me / CAPE 的递归解适合 PE 分诊；Android/.NET 仍要按托管路线收尾。

## 结束条件（按平台）

| 平台 | 可以停止 |
|------|----------|
| PE 压缩壳 | 分析器能见原始入口与真实导入 |
| PE 虚拟化壳 | packing 层去掉，并写明 VM 残留 |
| ELF | dump 中出现可识别的原始函数/字符串，或 GOT 合理 |
| Android | dump 出的 DEX 含真实 Activity/业务类 |
| .NET | 关键方法 IL 可见 |
| PyInstaller | 得到可反编译的主 pyc |
| Electron | ASAR 内 JS 已展开 |
