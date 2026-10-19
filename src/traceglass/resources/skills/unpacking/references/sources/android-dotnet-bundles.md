# Android、.NET、捆绑器（本地摘录）

## Android：壳在运行时才交出 DEX

静态 `classes.dex` 常常只是加载器。真正的类由加固 stub 解密后，经 `DexClassLoader`、`InMemoryDexClassLoader` 或 `BaseDexClassLoader` 送进 ART。

查壳时同时看：

- APKiD 的签名命中（候选，不是终判）。
- 清单里的 `Application` 是不是厂商 stub。
- `lib/<abi>/lib*.so` 文件名。公开笔记里常见对应：360 加固 `libjiagu.so`，梆梆 `libsecexe.so`，爱加密 `libijiami.so`，腾讯乐固 `liblegu.so`，阿里 `libmobisec.so`。名字会变，只作分流。
- native 库字符串里是否出现 loader、decrypt、ClassLoader。

运行时两条路：

1. **等类已经能被解析再 dump。** 启动早期在 `Application.attachBaseContext` 里往往还 `Java.use` 不到清单里的 Activity，说明原 DEX 没加载完。稍后 Activity 能被正常引用，视为解开完成。这个启发式对插件化、多进程、热修复会误报。
2. **从 ART 已加载的 DEX 导出**（frida-dexdump 一类），或在 ClassLoader 构造时记下路径/字节。内存 DEX 没有磁盘路径，不能靠 `adb pull` 某个文件。

导出物用 `dex\n` 魔数和 jadx 检查。业务类应出现在 dump 里，而不是只存在于静态 stub。真实 `Application` 的 `onCreate` 之后，才把样本当普通 App 看网络和逻辑。

### 2025 年对「必须脱完整 DEX」的修正

论文 *To Unpack or Not to Unpack: Living with Packers to Enable Dynamic Analysis of Android Apps*（arXiv:2509.16340）的实验结论：

- 商业壳使用按需/部分加载、JNI、模拟器检测、反 hook。它们还会钩文件和内存相关函数，阻止别人读已解密的 DEX。
- ART 类工具（钩 `DexClassLoader` / `BaseDexClassLoader`，如 Youpk、DexHunter）在所选样本上大量失败。Youpk 对声称支持的爱加密组，约 86% 在延长等待后仍 dump 不出 DEX；即使 dump 出来，差分也显示不完整或依赖 native。DexHunter 面向旧 Android，在第二组上基本全失败。
- 内存类工具同样退化。BlackDex（免 root、免 Frida）对现代释放方式过时，第二组上经常崩溃。frida-dexdump 概念上能扫已加载 DEX，但反 Frida 会在 dump 前把进程杀掉。依赖 ptrace 的 KissKiss 会被反调试提前拦下。

因此：目标若是行为，可以在壳仍在时做动态分析，记录请求层，而不是把「一份完整可 jadx 的 DEX」当成唯一成功标准。目标若是静态审计代码，要准备多份 dump、native 侧，并接受未执行路径仍然加密。

反 Frida 的常见观测点（只作识别，不在这里写绕过步骤）：默认端口 27042，`/proc/self/maps` 里的 `frida-agent`、`frida-gadget`、`libfrida-gum.so`、`[anon:gum-js-loop]`。有的检测用 `svc` 直接系统调用读 maps，钩 libc 的 `open` 看不见。

## .NET

dnSpyEx 能打开托管 PE。方法体是空的、或只剩 module constructor，说明 IL 还被保护器包着，不是 native 壳，不要先去修 IAT。

ConfuserEx 2 公开流程（针对已确认是该家族、且允许调试的样本）：

1. 调试到受保护模块的 module constructor 执行完、即将进入原始入口。
2. 从内存保存模块，尽量保留元数据。
3. 原始入口 Token 经常被删，在 dnSpyEx 里补回，否则工具找不到入口。
4. 用针对 ConfuserEx 的 de4dot profile 做控制流清理和重命名，例如 `de4dot -p crx`。
5. 再跑字符串解密。ConfuserEx2_String_Decryptor 要求 32 位工具对 32 位模块、64 位对 64 位，并且输入应是上一步解开后的模块。
6. 用 ProxyCall 移除工具把代理调用内联回去。

de4dot 对旧 Confuser 还能识别嵌入资源里的主程序集并解密（不同版本算法不同，含 LZMA 一类解压）。新版本或魔改保护器经常要先做第 1–3 步，de4dot 才认得出。

成功标准是关键方法能反编译出连贯 IL，不是文件变大。

## Python / Electron / JAR

**PyInstaller**  
`pyinstxtractor.py <exe>` 抽出内容，并修正 pyc 头以便反编译。已测版本覆盖 2.0 到 6.19 一带，其他版本可能也能抽。最好用打包时的同一 Python 版本运行抽取器，否则 PYZ 解包会 unmarshal 失败。抽出的 pyc 再用 uncompyle6 或 pycdc。  
ELF 形态的 PyInstaller 可以由该脚本直接抽，不必先当原生 ELF 脱壳。加密的 PyInstaller 用 pyinstxtractor-ng。外壳若再套 UPX，先 `upx -d` 再抽。

**不要误判**  
Nuitka 编译成原生代码，按 PE/ELF 分析，抽不到有意义的 pyc。py2exe、cx_Freeze 是别的归档格式。

**Electron**  
`npx asar extract` 展开 ASAR。里面是 JS 混淆时做反混淆，没有 OEP。

**JAR**  
就是 ZIP。`META-INF/MANIFEST.MF` 给入口。类加密到自定义 ClassLoader 时，才需要等 `defineClass` 拿到字节；普通混淆器（Zelix 等）直接在 class 上做去混淆。
