# Fuzz 与 sanitizer（本地摘录）

## 覆盖引导 fuzz 在做什么

维护一组种子。每次变异一个输入，跑目标，若碰到新的边（或别的反馈）就留下。目标是用执行反馈代替纯随机。解析器、图片、压缩、字体、协议解码是主要受益者。

反馈传统上是控制流边。只看边会漏掉「边相同但数据流不同」的缺陷。研究系统（如 FuzzRDUCC）用符号执行从二进制重建 def-use 链，把数据流也放进反馈。这是增强，不是替换 sanitizer。

## 引擎怎么选

| 引擎 | 适合 | 备注 |
|------|------|------|
| libFuzzer | 进程内、一个函数一个 harness | OSS-Fuzz 要求能链上它 |
| AFL++ | 需要进程外、持久模式、比较日志 | 交叉平台；CMPLOG 帮助过魔法字节比较 |
| Honggfuzz | 另一路反馈，常和前两者一起跑 | OSS-Fuzz 默认引擎之一 |
| Centipede | 与上面并行的另一引擎 | OSS-Fuzz 默认会一起编 |
| syzkaller | 内核系统调用序列 | 不是用户态解析器 fuzzer |
| go-fuzz / 语言自带 | Go、Rust、JVM、Python | 按语言选，不要用 AFL 硬包脚本 |

OSS-Fuzz 同时支持 libFuzzer、AFL++、Honggfuzz、Centipede，配合 sanitizer，由 ClusterFuzz 分布式跑并去重。闭源项目用 ClusterFuzz 或 ClusterFuzzLite 自建，不把闭源代码提交到 Google 的 OSS-Fuzz。支持过的语言包括 C/C++、Rust、Go、Python、Java/JVM、JavaScript、Lua；架构是 x86_64 和 i386。到 2024 年 9 月，公开统计是超过 12000 个 bug、一千个以上项目。平均每个项目约 22 个，个别项目（如 ffmpeg）超过 400。这些数字说明持续 fuzz 的产量，不说明每个崩溃都可利用。

## Harness 规则

- 只喂不可信输入会到达的函数。不要 fuzz `main` 里要交互或要联网的部分。
- 种子用真实文件的最小合法样本。没有种子时，多数结构化格式跑不深。
- 目标进程里不要做网络、写用户目录、读环境里的密钥。
- OSS-Fuzz 的构建不要假定引擎一定是 libFuzzer，链接 `$LIB_FUZZING_ENGINE`。目标二进制名只含字母、数字、下划线和连字符。不要删源文件，否则没有覆盖率。
- 每种 sanitizer 配置都要能编过并跑过一次：`build_fuzzers` → `run_fuzzer` → coverage。

## Sanitizer 报告什么

| 检测器 | 能钉住的缺陷 | 限制 |
|--------|----------------|------|
| AddressSanitizer | 堆/栈/全局越界，use-after-free，双重释放 | 要重编；内存占用更高 |
| UndefinedBehaviorSanitizer | 有符号溢出、移位越界、未对齐等未定义行为 | 很多发现是稳定性问题，要人判断是否影响安全检查 |
| MemorySanitizer | 未初始化读 | 所有依赖都得用 MSan 编，OSS-Fuzz 默认不打开 |
| ThreadSanitizer | 数据竞争 | 并发目标；开销大，和 ASan 不要混在同一次构建里 |

Fuzz 的动态执行让 sanitizer 命中成为真阳性：这条输入确实走到了错误状态。静态告警没有这条保证。

崩溃分诊做到这三步即可：

1. 用栈和 sanitizer 类型去重（同一分配点 + 同一错误类）。
2. 去掉需要特定地址随机化才能撞上的偶发崩溃，留下稳定复现的输入。
3. 标成越界读、越界写、释放后使用、未初始化、竞争或空指针。影响按这个类别写。

不要做：把崩溃输入改成控制指令指针、绕过 canary/ASLR、或生成 shellcode。

## 什么时候 fuzz 会失败

- 输入要先过校验和、加密或签名。先在授权前提下 fuzz 校验之后的明文接口，或让 harness 跳过密码学验证只测解析。不要为了挖洞去破坏产品的鉴权。
- 状态要很多步才建立。用更小的 API harness，或快照 fuzz（Nyx、QEMU 持久快照）从已建立的状态开始。
- 魔法值比较。优先用 AFL++ 的比较日志或编译器插桩把比较拆开，而不是纯随机撞 32 位常数。
- 覆盖率长期不动。看是 harness 没调用到目标，还是种子不合法。先修这个，再加机器。

## 混合执行

Fuzzer 负责数量，符号执行负责「过不了的那一个分支」。Driller 在 fuzzer 停滞时把卡住的输入交给符号执行，求出能走到新边的具体字节。SAVIOR 先用 UBSan 标出可疑区域，引导 fuzzer 靠近，再用选择性符号执行生成能触发检测器的具体输入。

不要对整个二进制做无引导的符号执行。路径数量会在循环和指针里爆炸。KLEE、angr、Triton 适合短路径、单函数或「已有静态告警的那几条路径」。

## 内核

syzkaller 生成系统调用序列，而不是文件字节。关注点是：驱动的 ioctl、用户拷贝到内核的长度、生命周期（打开/关闭/并发释放）。确认同样以 KASAN 或等价内核检测器的报告为准。

列出攻击面时可以包括：设备驱动、io_uring、userfaultfd、eBPF 验证器和 JIT、容器运行时、hypervisor 调用。这些是入口类别。发现具体缺陷后仍停在检测器报告，不把缺陷写成读写原语。
