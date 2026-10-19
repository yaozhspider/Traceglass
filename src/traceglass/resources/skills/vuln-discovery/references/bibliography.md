# 本地摘录目录

分析时读 `sources/`。不要为了看链接去联网。标题只用于报告注明出处。

| 要查的问题 | 读这个文件 |
|------------|------------|
| 阶段、方法取舍、确认等级 | [sources/discovery-workflow.md](sources/discovery-workflow.md) |
| 2025 CWE Top 25，每类看什么、怎样算确认 | [sources/bug-classes.md](sources/bug-classes.md) |
| Fuzz 引擎、harness、sanitizer、混合执行、内核入口 | [sources/fuzzing-sanitizers.md](sources/fuzzing-sanitizers.md) |
| Semgrep 与 CodeQL、补丁 diff、变体、SCA | [sources/static-and-variants.md](sources/static-and-variants.md) |
| Web、原生、内核、移动、协议、供应链先看哪 | [sources/surfaces.md](sources/surfaces.md) |

摘录覆盖的材料：

- MITRE CWE Top 25（2025）及当年排名变化、KEV 计数
- OWASP Web Security Testing Guide：扫描器负责广度，业务逻辑靠人工，指南不是清单
- Google OSS-Fuzz / ClusterFuzz：libFuzzer、AFL++、Honggfuzz、Centipede 与 sanitizer；公开 bug 规模统计
- 混合 fuzz 与符号执行：Driller、SAVIOR；符号执行综述里对「只解卡住分支」的归纳
- SymBisect（USENIX Security 2024）：用缺陷逻辑而不是重放同一个 PoC 来定位引入提交
- FuzzRDUCC：控制流边之外补充 def-use 反馈
- CodeQL 与 Semgrep 用于变体分析时的能力差：跨过程数据流对语法传播
- LATTE：二进制污点按函数切片后再判断，输出仍须人工或动态确认

利用开发、缓解绕过和武器化没有写入摘录。
