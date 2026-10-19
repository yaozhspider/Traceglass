# Traceglass 任务路由

不知道选哪个专题、目标类型变化或多个 Skill 同时匹配时读取。统一入口为
`traceglass-analysis`；先评估 REA，再选一个主要方法和必要的平台补充。

## 常见组合

| 任务 | 主要方法 | 按需补充 |
| --- | --- | --- |
| 原生程序实现、关键函数 | `reverse-engineering` | `rea-analysis`；重点函数再读 `key-function-analysis` |
| JS/浏览器扩展包 | `js-reverse` | 扩展读 `browser-extension-reverse`，优先检查 REA 静态 JS 能力 |
| APK、.NET、Go/Rust、Mach-O | 对应平台 Skill | `reverse-engineering`、`rea-analysis` |
| 恶意软件行为与 IOC | `malware-analysis` | 行为结论读 `behavior-chain-review`，按格式补平台 |
| 用户提供 CAPE/DDAN 报告 | `dynamic-malware-analysis` | 仅解析已有证据；有样本再做静动态关联 |
| 用户允许的运行、调试、dump | `sandbox-dynamic-debugging` | 按 Windows、Linux、浏览器实际通道选择；算法问题补 crypto |
| 保护识别、脱壳、去虚拟化 | `unpacking` | DIE 静态识别；NoVmp 先核输入条件；动态部分走授权通道 |
| 加解密、编码、控制流混淆 | `crypto-obfuscation-analysis` | VM 补 `dsl-vm-reverse`，不把算法恢复当行为定性 |
| 漏洞发现/源码审计 | `vuln-discovery` | 源码方法补 `code-audit`，结论复核读 `vulnerability-hunting` |
| 协议或历史流量 | `protocol-reverse` | 归属/时间线补 `digital-forensics`；主动重放单独核执行前提 |
| 二进制版本比较 | `binary-diff` | 补丁定位补 `patch-diff-exploit`，不自动进入利用开发 |
| 报告与图表 | `docs-generator` | 按需要读 `diagram-generator`，不重新运行分析工具 |

## 现有包的职责分组

这是逻辑分类，保持现有包名与相对资源路径；包存在不证明依赖已安装。
外部拓展可增加包，以当前 Skill 列表为准，不默认读全组。

| 组 | 包 |
| --- | --- |
| 入口、适配、通用方法（4） | traceglass-analysis、rea-analysis、reverse-engineer-anything、reverse-engineering |
| 平台（7） | apk-reverse、mobile-reverse、dotnet-reverse、go-rust-reverse、macos-reverse、browser-extension-reverse、thick-client |
| 专题（7） | js-reverse、dsl-vm-reverse、crypto-obfuscation-analysis、unpacking、key-function-analysis、protocol-reverse、binary-diff |
| 引擎（3） | ida-reverse、ghidra-reverse、radare2 |
| 恶意软件、动态与取证（5） | malware-analysis、dynamic-malware-analysis、sandbox-dynamic-debugging、behavior-chain-review、digital-forensics |
| 漏洞与代码审计（6） | vuln-discovery、vulnerability-hunting、code-audit、patch-diff-exploit、pwn-chain、edr-bypass-re |
| 渗透、资产与检测（17） | attack-chain、pentest-tools、src-hunter、api-security、cloud-k8s、database-security、email-security、firmware-pentest、hardware-security、identity-federation、llm-security、ot-ics、radio-sdr、supply-chain-security、wifi-wireless、windows-ad、threat-hunting |
| 辅助交付（3） | browser-automation、docs-generator、diagram-generator |

REA 官方 Skill 负责上游格式方法；`rea-analysis` 负责本项目部署差异。
IDA/Ghidra/r2 Skill 是操作指南，不决定主要分析路线，不要求先失败再使用 REA。
已有报告与主动运行属于不同任务，读取报告不授予新执行权限。
