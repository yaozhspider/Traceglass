---
name: js-reverse
description: Use for static analysis of shipped JavaScript bundles, Electron or extension source trees, obfuscated scripts, or authorized browser evidence needed to recover application logic, signatures and data flow.
metadata:
  traceglass_revision: routing-v1
---

# JavaScript 分析

先按 `traceglass-analysis` 评估 REA，再用 `rea-analysis` 选择静态制品或运行时路线。
本 Skill 负责 JS 方法，不预设 js-reverse-mcp、jshookmcp 或浏览器已安装。
静态 JS 分析不需要 Ghidra/Hopper，也不需要先创建浏览器会话。

## 静态包与脚本

1. 固定原始包与提取成员哈希，识别 HTML/manifest、入口脚本、模块、source map、依赖库与业务区。
2. 目录目标先检索 `analyze_javascript_application`；需要恢复压缩源码再查
   `recover_javascript_sources`，按实际前提和只读部署限制决定可用性。
3. 用返回的应用图/证据，按问题检索 `trace_application_feature` 或
   `trace_javascript_semantics`。工具未激活就先激活、读 schema；不从名字猜参数。
4. 沿输入、消息/事件注册、分派、转换和消费者分析。区分库代码、自研逻辑与未解动态边；
   对 JS 语法、模板字符串、正则字面量和 Unicode 使用解析器或有明确限制的扫描。
   正则零命中不证明不存在动态加载或隐藏载荷。
5. REA 不支持、前置条件不足或实际失败时，保留原因，再用可用 AST 工具/有界脚本补证。
   同一文本统计报告单位与区间约定，避免把字符位置、UTF-16 code unit 与 UTF-8 byte 混用。

## 浏览器与运行时

需要运行时证据时，先确认当前任务允许的动作、隔离通道和实际端点。
REA 可检索 `list_browser_targets`、`list_javascript_runtime_targets`、
`observe_javascript_runtime`、`capture_browser_scenario` 等；工具能力与依赖按 schema 核实。
静态 REA 容器无网络，容器本地端点不等于宿主机端点；存在工具定义不证明可以附加。

使用已注册的替代 CDP/Hook 工具时再读相关 references，并把旧工具名视为映射线索。
按观察请求/脚本 → 最小采样 → 依据证据补环境 → 对照首个分歧验证的顺序推进。
被动脚本/上下文元数据不等于函数已执行；执行样本、hook 和主动场景均遵守
`sandbox-dynamic-debugging` 的通道与范围约束，不在普通工作区执行未知 JS。

## 按需资料与交付

- AST 和混淆：读 [ast-deobfuscation.md](references/ast-deobfuscation.md)，复杂变换补 `crypto-obfuscation-analysis`。
- 有运行时采样且需要复现：读 [local-rebuild.md](references/local-rebuild.md)、[env-patching.md](references/env-patching.md)。
- 已注册相关工具但失败：读 [fallbacks.md](references/fallbacks.md)，以当前工具契约替代旧部署示例。

输出关键入口、触发条件、输入到副作用的路径、原始文件定位、证据 ID 和未解动态边。
公共引用/归档规则由 `traceglass-analysis` 的证据契约统一；浏览器扩展补读
`browser-extension-reverse`，自定义 opcode VM 补读 `dsl-vm-reverse`。
