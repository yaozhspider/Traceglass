# 方法索引

细节在 `sources/`。不要打开 URL。

| 方法 | 何时用 | 读 |
|------|--------|----|
| 攻击面与阶段 | 每次开始 | [sources/discovery-workflow.md](sources/discovery-workflow.md) |
| 人工审计 + 身份矩阵 | 鉴权、业务逻辑 | [sources/bug-classes.md](sources/bug-classes.md)、[sources/surfaces.md](sources/surfaces.md) |
| Semgrep / CodeQL | 模式清楚或要做变体 | [sources/static-and-variants.md](sources/static-and-variants.md) |
| 补丁 diff | 有两个版本 | [sources/static-and-variants.md](sources/static-and-variants.md) |
| 覆盖引导 fuzz + sanitizer | 解析器、内存安全 | [sources/fuzzing-sanitizers.md](sources/fuzzing-sanitizers.md) |
| 符号执行 / 混合 | fuzzer 被少量分支卡住 | [sources/fuzzing-sanitizers.md](sources/fuzzing-sanitizers.md) |
| 依赖版本核对 | SBOM 或锁文件里有已知漏洞 | [sources/static-and-variants.md](sources/static-and-variants.md) |

确认等级只有 observation、hypothesis、confirmed。静态命中默认是 hypothesis。
