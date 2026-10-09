---
name: crypto-obfuscation-analysis
description: 用于识别二进制中的加密、哈希、编码、压缩和代码混淆，并通过多证据评分、语义表示和受控实验验证候选结论。
metadata:
  short-description: 识别并验证二进制加密与混淆
---

# 加密与混淆分析

## 适用范围

当静态分析发现疑似加密、解密、哈希、编码、压缩、字符串加密、控制流混淆、API 哈希、虚拟机或自定义变换时加载本 Skill。它负责识别和编排，不替代 `reverse-engineering` 的分诊，也不替代 `sandbox-dynamic-debugging` 的执行。

## 核心原则

- `crypto-detector`、字符串、API 或单个常量只能产生候选，不能单独证明算法。
- 先区分编码、压缩、哈希/MAC、对称加密、非对称加密和代码混淆，再判断具体算法。
- 结论必须同时记录支持证据、反证、缺口、来源位置和置信度。
- 优先确认调用可达性和数据流：输入、key/IV/nonce、状态、输出是否属于同一条真实调用链。
- 不要把“有 XOR”直接解释成加密；它也可能是字符串解码、校验、API 哈希或指针混淆。

## 工作流

如果加解密位于网络接收、消息分派或代码加载路径，先加载 `reverse-engineering` 并读取
`references/external-input-execution.md`，核对变换前后的数据最终是显示、存储还是进入代码执行。
输出中包含“输入来源 → 变换 → 消费者/分派 → 最终操作 → 证据与缺口”；未解的高影响分支
优先于进一步还原轮表、测试向量和普通辅助函数。仅做算法任务时注明范围，不外推整个程序安全。
正确的 AES/DH、认证握手或可读明文不证明程序良性；字符串池恢复后还要核对引用、参数和分支。

1. **候选发现**：结合 capa/FLOSS/`crypto_detector_scan`、导入导出、字符串、常量、CFG 和调用者生成候选。
2. **语义提取**：记录位宽、端序、块/字长、轮数、旋转方向、查表、算术操作、内存读写和输入输出依赖。需要时使用 IDA MCP、Ghidra P-code、angr 或 Triton。
3. **候选评分**：使用 [evidence-scoring.md](references/evidence-scoring.md)，把文本命中与真实调用、结构指纹、数据流和动态吻合分开计分。
4. **生成 IR**：按照 [semantic-ir-schema.md](references/semantic-ir-schema.md) 输出结构化变换描述，再据此编写复现代码；不要从长段伪代码直接猜结论。
5. **混淆分类**：按照 [obfuscation-patterns.md](references/obfuscation-patterns.md) 区分控制流平坦化、不透明谓词、MBA、间接跳转、字符串加密、API 哈希、VM 和运行时解包。
6. **验证编排**：需要执行样本或复现脚本时，加载 `sandbox-dynamic-debugging`。比较多组输入和中间状态；实验结果只能把 `candidate` 升为 `supported` 或 `validated`，不能凭可读输出升级。

## 输出契约

输出一份候选表，至少包含：类别、算法/混淆类型、置信度、支持证据、反证、缺失证据、函数/地址/文件偏移、使用的工具和下一步实验。推荐状态：`candidate`、`supported`、`validated`、`rejected`。

每次候选还要单独写 `execution_status`（`not_run`、`completed`、`failed`、
`timeout` 或 `blocked`）以及三个验证对象：`algorithm`（样本是否确实使用该算法）、
`implementation`（复现代码是否正确）和 `recovery`（当前样本数据是否恢复）。
缺依赖、路径错误、架构不兼容或超时只能标记执行失败，不能直接否定算法。
脚本、输入、输出、日志和哈希应归档到实验目录，并通过 `record_crypto_experiment`
登记；不能只在最终报告里粘贴一段输出。

简单 XOR、Base64、ROT 等可直接记录参数表并编写短 Python 复现脚本；复杂算法或
混淆必须先生成带操作数、目标状态、顺序、位宽和端序的 IR。每次实验只改一个变量，
并保存第一个不一致的中间值。

## 按需读取的参考资料

- 常量、轮函数和输入长度线索：读取 [algorithm-fingerprints.md](references/algorithm-fingerprints.md)。
- 需要常见方法、反证和验证模板：读取 [common-methods.md](references/common-methods.md)。
- 混淆形态和工具选择：读取 [obfuscation-patterns.md](references/obfuscation-patterns.md)。
- 评分和状态升级：读取 [evidence-scoring.md](references/evidence-scoring.md)。
- 需要生成 Python/IR 复现时：读取 [semantic-ir-schema.md](references/semantic-ir-schema.md)，执行实验再读取 `sandbox-dynamic-debugging/references/crypto-experiment-playbook.md`。
