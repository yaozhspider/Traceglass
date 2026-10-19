---
name: browser-extension-reverse
description: Use for Chrome/Edge/Firefox extension packages or extracted directories when analyzing manifest permissions, background workers, content scripts, extension messaging, credential access or network behavior.
metadata:
  traceglass_revision: routing-v1
---

# 浏览器扩展分析

本 Skill 补充扩展平台特征；JS 方法使用 `js-reverse`，工具选择使用 `rea-analysis`。
包体提取后先检查 REA 的 `analyze_javascript_application` 等静态能力，
无需因为目标是扩展而跳过 REA，也不把包清单当完整逻辑分析。

## 分析顺序

1. 核验包与成员哈希，读取 manifest：版本、permissions、host_permissions、
   background/service_worker、content_scripts、popup、devtools、外部通信与更新配置。
2. 逐项定位入口及其执行世界：后台 worker、isolated/main-world content script、
   popup、DevTools 页面与面板。核对匹配 origin、frame、注入时机和实际加载关系。
3. 追踪 runtime 消息、tabs/scripting、存储、网络与剪贴板等副作用；
   验证消息发送方、监听注册、分派条件、来源验证及数据去向。
   存在监听器不等于当前包里有可达发送者；权限声明不等于实际执行过。
4. 对凭据区分读取、页面显示、剪贴板、持久化及每条网络目的地。
   API 命中或域名清单只能产生线索，结论依赖连接的数据流与条件。
5. 静态未解问题才提出动态验证方案。加载扩展或附加 worker 前确认任务执行范围、
   隔离浏览器与可达 CDP；REA/Windows 工具已注册不表示该扩展运行通道已就绪。
   仅静态任务继续保留运行时未知项。

细节按需读 [extension-analysis.md](references/extension-analysis.md)。
报告保留权限与实际使用的差异、每个入口、关键消息/数据流、位置和动态覆盖限制。
恶意性复核使用 `behavior-chain-review`；引用与归档遵守 `traceglass-analysis` 公共证据契约。
