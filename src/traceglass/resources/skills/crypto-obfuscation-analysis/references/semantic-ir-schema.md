# 语义中间表示

在编写 Python 复现前，先输出最小化的结构化表示。字段不确定时填 `unknown`，不要猜测。

```yaml
category: custom_transform  # encoding|compression|hash|mac|symmetric|asymmetric|custom_transform|obfuscation
algorithm_candidate: XTEA
confidence: candidate
input:
  length: 8
  unit_bits: 32
  endianness: little
state:
  words: [v0, v1]
key:
  source: unknown
  length: unknown
operations:
  - op: add_mod
    width: 32
    dst: v0
    args: [v0, v1]
    order: 1
  - op: xor
    width: 32
  - op: shift
    direction: right
rounds: 32
output:
  length: 8
unknowns:
  - key_schedule
  - padding
evidence_refs: []
```

复杂变换还必须记录每个状态更新的顺序、位宽、端序、输入来源和输出位置；
不能只列操作名称。简单 XOR、Base64 或 ROT 可以使用参数表和短脚本，不必强行
生成完整 IR。复现时保存第一个与样本不一致的中间值，作为下一轮假设的证据。

复现脚本必须明确实现溢出、端序、padding、key/IV/nonce 和状态更新；这些字段验证前不得省略。
