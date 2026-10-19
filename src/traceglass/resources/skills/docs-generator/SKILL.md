---
name: docs-generator
description: Use when writing a Traceglass analysis report, README, API document or technical handoff from existing evidence, including vulnerability, malware and reverse-engineering reports.
metadata:
  traceglass_revision: routing-v1
---

# 报告与技术文档

本 Skill 组织已有证据和表达方式，不重新取证、不安装工具、不决定样本 verdict。
分析入口与证据契约由 `traceglass-analysis` 统一；纯文档任务评估 REA 不适用后直接写作。

## 选择结构

- 有当前任务/runtime 提供的报告规范：沿用该规范。
- 二进制正式报告：使用 `reverse-engineering` 的 `references/report-standard.md`；
  先读取工作区内该包的资源；未复制时再通过已注册的 `read_skill_resource` 读取。
- 恶意软件、漏洞、沙箱报告：使用对应方法 Skill 的字段及模板。
- 其他安全报告按需读 [security-report-templates.md](references/security-report-templates.md)。
- README/API 等通用文档按需读 [templates.md](references/templates.md)。

从结论及其证据范围开始，随后写目标身份、方法、关键发现、原始证据定位、反证与限制。
区分静态能力、观察到的运行行为、推断与未知；不把结构检查通过写成语义验证完成。
不要求为套模板补造未执行的命令、scope/timeline 文件或独立复核。

## 保存与核对

输出位置、文件名与语言服从当前任务；通过工作区 Write 保存，按实际回执归档。
敏感凭据脱敏；引用地址/偏移说明单位，报告哈希与实际文件一致。
`validate_reverse_report` 只在适用规范且已注册时使用，`AnalysisReview` 的路径、引用和
语义检查按公共证据契约分别报告。图表确实有帮助时再读 `diagram-generator`。
最终交付给出主要结果、报告路径和影响结论的未决项。
