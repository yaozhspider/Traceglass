# 工具索引

没在本机跑过的不要写结果。能力边界在 `sources/`。

| 用途 | 工具 |
|------|------|
| 语法规则 | Semgrep |
| 数据流 / 变体 | CodeQL、Joern |
| 进程内 fuzz | libFuzzer |
| 通用覆盖 fuzz | AFL++、Honggfuzz、Centipede |
| 持续 fuzz | OSS-Fuzz、ClusterFuzz、ClusterFuzzLite |
| 内核 fuzz | syzkaller |
| 内存/未定义行为 | AddressSanitizer、UBSan、MemorySanitizer |
| 竞争 | ThreadSanitizer |
| 符号执行 | KLEE（源码）、angr、Triton |
| 二进制 diff | BinDiff、Ghidra 版本对比 |
| Web 广度 | ZAP 一类 DAST；人工看业务逻辑 |
| 依赖版本 | 锁文件 + 公告；再核对调用点 |
| 反编译 | Ghidra、IDA。加固样本先走 unpacking skill |

OSS-Fuzz 默认引擎是 libFuzzer、AFL++、Honggfuzz、Centipede。默认 sanitizer 配置是 address 和 undefined。MemorySanitizer 要整条依赖链一起编，需手动打开。
