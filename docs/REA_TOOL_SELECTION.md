# REA 工具选择优化（2026-10-10）

2026-10-14 当前入口为 `SearchAnalysisTools`：跨 REA/reverse/IDA 查询已注册定义，
REA 可以处于 deferred 状态，首次实际使用才连接。旧 `SearchReaTools` 保留兼容；
下文工具数、模型轨迹和测试数量是 2026-10-10 的历史验证，不能作为本次优化的质量或成本对比。

目标：让主、子 Agent 在需要二进制语义、调用/数据流或制品证据时主动选择 REA，
并能发现默认没有展开的专用工具。仍由 AgentScope 原生 ReAct 决定下一步，原生
MCPTool、reset_tools 和权限系统负责执行。不强制调用次数，不把工具调用率当分析正确率。

## 原因与修改

旧主/子 Agent 提示词要求“连接 reverse 时优先使用原有工具”，质量策略又把 REA
写成 IDA 失败后的备选。2026-10-10 抽查 accounts 数据库最近 2000 条消息时，主会话
`87f6ea37cdd84472af828e0c1d059574` 在这个窗口内有 140 次 Bash、没有 REA 调用；
其他历史成员会话确有 REA 反编译和交叉引用。该统计只说明选用偏向，并非全会话计数或
每条 Bash 都能被 REA 替代，也不能证明所有未调用情况都来自提示词。

现在的选择规则：

| 任务 | 首选方向 |
| --- | --- |
| 二进制实现、调用/数据流、间接调用、制品结构 | 已连接且支持目标的 REA |
| 哈希、身份核验、解包、规则、已有动态报告 | reverse 或对应工作区工具 |
| 完整源码阅读、报告处理、自定义计算 | 工作区工具 |
| 用户指定 IDA、独立复核、REA 不支持或失败 | 对应可用分析器，保留缺口 |

- `rea_guidance.py` 提供任务到工具的提示，特别是外部输入到执行点、解密及间接调用。
- `ReaToolsMiddleware` 在每次组装原生 Agent 时应用指引，覆盖主 Agent、现有成员及新成员。
  仅替换精确匹配的旧内置偏好句，不改写数据库中的用户提示词；质量中间件消除旧 IDA 后备句。
- 新的 `SearchReaTools` 检索**实际已连接**的工具名称/描述，只返回少量元数据、工具组和
  `reset_tools` 参数。支持精确工具名、英文关键词、空查询分页；无匹配不虚构工具。
- 工具组增加用途说明。完整上游 schema 保持原样，仍通过原生 `reset_tools` 按需展开。
  检索不会启用工具、切换权限或执行分析；reset_tools 会替换所有可选组，需要合并仍需的组。
- 新工作区的 `rea-analysis` 和 `traceglass-analysis` Skill 补齐选择策略。旧工作区有意保留
  已安装/可能编辑过的 Skill 副本；新运行时指引仍会生效，不依赖覆盖这些副本。
- REA 连接失败时明确引导使用可用工具，不声称所有工具已可执行。工具被发现也不代表当前
  provider、网络或运行时具备支持条件。

旧版 133 工具 schema 快照检查：12 个基础工具、23 个可选组，完整参数共 764,144 字符；
仅基础工具、检索工具及分组入口的 schema 为 15,352 字符（不含该快照未保存的工具描述，
也不含其他 MCP 的定义，因此不是完整模型请求 token 数）。工具数量以当前连接为准。

## 验证与后续评测

测试覆盖原生分组激活、原始 MCPTool 实例和 schema 保留、检索分页/边界、未连接降级、
旧提示词兼容，以及真实 AgentScope 主/子 Agent 的检索→启用→调用→证据归档流程。
全套测试：162 passed（78 条现有 AgentScope 弃用警告）。

真实模型 `glm-5.3-flash` 完成验证，结果保存在
`traceglass-data-integrated/rea-selection-20261010-v2/result.json`：

- 12 次 REA 调用：open_binary、binary_overview、search_procedures、procedure_pseudo_code ×2、
  procedure_callees、procedure_callers、procedure_info ×2、xrefs、read_bytes、close_binary。
- 2 次 Bash：file/sha256sum/ls 身份核验，以及 objdump -dr 独立复核；未执行样本。
- reverse 用于保存分析产物和记录证据；全部 20 个工具结果为 success。
- 报告恢复 `(x ^ 0x5a) + 7`、无符号条件分支及 main→toy_score 重定位调用关系，
  与未提供给模型的 fixture.c 一致。

本次是基础工具选择验证，没有触发 SearchReaTools；专用工具检索/激活由原生主、子 Agent
集成测试验证，尚未以真实模型覆盖所有专用工具。该轮不构成与旧版的统计 A/B 对比。

真实模型选择验证入口：

```sh
.venv/bin/python scripts/verify_rea_selection.py \
  --sample /absolute/path/to/benign-fixture.o \
  --output /absolute/path/to/new-result-directory
```

fixture 应为自建无害 `main`/`toy_score` 示例；脚本不会执行输入样本。它创建隔离的测试数据目录、
Docker 工作区和测试权限会话，加载真实 REA、reverse MCP、原生工作区工具及 Skills。
用户任务不点名 REA；共享宿主 IDA 不参与，避免影响用户的 IDA 状态。结果只导出公开文本和
工具输入输出，不导出模型私有推理或凭证。运行目录保留测试数据库和证据供复核，不要公开发布。

验收应检查成功的语义工具调用、结果是否回答证据问题、失败后是否调整和结论是否有依据。
单个无害 fixture 只验证选用链路；后门、混淆、托管/Android 等任务仍需独立样本评测。
不能据此宣称所有 133 个工具都被调用、所有 provider 都可用，或恶意判定准确率已经提升。

修改 Python 后需要重启后端，新请求和恢复执行才会使用新中间件。正在运行的 Agent 不会热替换。
