# Traceglass Skill 整理说明

本次把任务入口、分析方法、平台知识和引擎操作分开，保留现有 Skill 名称与原始外部库。
当前配置实际选中 **52 个包：项目内 19 个、旧库补充 33 个**，没有重复的 frontmatter 名称。
项目内包从 16 个增加至 19 个：新增的 JS、扩展和报告包替代旧库里的同名入口，
因此有效总数没有增加。以下是逻辑分类，不移动旧库目录或批量加载全组。

## 入口与职责

| 职责 | Skill | 使用方式 |
| --- | --- | --- |
| 任务入口 | `traceglass-analysis` | 确认问题、目标与证据范围，先评估 REA，再选择方法 |
| 项目工具适配 | `rea-analysis` | 按原生、JS、托管、归档等格式选择入口，发现、激活、核对 schema |
| REA 上游方法 | `reverse-engineer-anything` | 保留官方包；复杂任务按需读取格式参考和 workflow |
| 通用逆向方法 | `reverse-engineering` | 分诊、控制流、数据流和实现恢复，不再规定 IDA 优先 |
| 专项方法 | malware、JS、漏洞、协议、加密、脱壳等 | 根据当前证据问题选择主要方法 |
| 平台补充 | APK、.NET、Go/Rust、macOS、扩展等 | 补充格式、入口和平台语义 |
| 引擎操作 | IDA、Ghidra、radare2 | 补当前可用引擎的操作细节，不接管任务路线 |
| 复核与交付 | 行为复核、漏洞复核、报告与图表 | 复用真实证据，明确结构检查与语义复核的区别 |

全部 52 个包的八类分组，以及常见任务组合，见
[任务路由](../src/traceglass/resources/skills/traceglass-analysis/references/task-routing.md)。
路径、哈希、原始回执、覆盖缺口和归档顺序统一见
[证据契约](../src/traceglass/resources/skills/traceglass-analysis/references/evidence-contract.md)。

## REA 默认路线

每项新任务或目标变化时，读取入口与 REA 适配，留下简短记录：
`适用能力 / 选用工具 / 前置条件或回退原因`。同一任务复用已有判断。

原生二进制核对引擎会话；提取后的 JS/扩展先查静态应用分析；托管代码与 Android
选择对应显式路径工具。JS 静态分析不需要先打开 Ghidra，也不要求先接入浏览器。
搜到未激活工具后按返回参数激活，读取实际 schema，再调用。
工具定义存在、分组已激活、服务已连接和 provider 能完成操作分别判断。

纯文档、完整源码阅读或已有沙箱报告解析通常直接用对应工具，记录适用性判断即可。
缺依赖、只读目录、不可达端点或 provider 不支持时保留具体原因，继续可行路线。
不能用一次工具搜索代替分析，也不能把未调用计作失败。
REA 的 `mcp__rea__*` 与旧 reverse 的 `mcp__reverse__*` 分别统计。

这次修改的是 Skill 指导和运行时提示，不是强制执行门禁。
静态检查与原生加载测试能验证包与工具激活流程；真实模型是否遵循路线仍需后续轨迹验证。

## 几组容易混淆的边界

- `dynamic-malware-analysis`：读取已有 CAPE/DDAN 报告，不主动运行样本。
- `sandbox-dynamic-debugging`：按实际可用通道做已授权的执行、调试、dump；Windows、Linux、浏览器分别核实前提。
- `vuln-discovery`：漏洞发现方法；`vulnerability-hunting`：路径状态与可利用性结论的复核。
- `malware-analysis`：恶意软件取证；`behavior-chain-review`：复核行为链与定性证据。
- `js-reverse`：JS 分析方法；`browser-extension-reverse`：扩展权限、入口、消息和数据流。
- `docs-generator`：组织已有证据；报告排版不触发重新取证或工具安装。

本地 DIE 入口仍是 `mcp__reverse__detect_packer`；NoVmp 与其他脱壳工具按
`unpacking` 的实际输入条件使用。工具已安装不代表所有格式和动态操作都能执行。

## 来源优先级与版本

加载时依次合并：**自动继承旧库 → 项目适配包 → 显式自定义目录**。
旧库保留未适配的平台和专题；项目包接管同名入口；用户显式指定的包仍可覆盖项目包。
两个不同显式目录提供同名包时报告冲突，不按偶然顺序选择。

14 个本次调整的入口标记为 `routing-v1`；全部有效包都可计算包含 references/scripts 的内容哈希。
导入来源、上游记录与部署文件哈希保存在
[`skill-sources.json`](../src/traceglass/resources/skill-sources.json)。
新增三个包的参考资料原样复制；入口适配在项目内维护。官方 REA 包和外部原始库未在本次修改中编辑。

## 工作区应用范围

新工作区从当前项目源复制 Skill；已有工作区的兼容同步只添加缺失的名称，保留同名内容及用户修改。
因此服务重启本身不会更新已有同名 Skill。本轮显式迁移已更新 10 个已有工作区，
每个均核验 52 个包与当前源一致。`test` 按用户选择等待整组空闲后才应用；本次没有重启服务。

先用只读清单核对实际来源与已安装副本：

```sh
PYTHONPATH=src python3 scripts/audit_skills.py
PYTHONPATH=src python3 scripts/audit_skills.py --workspace /absolute/workspace/root
```

| 对比状态 | 含义 |
| --- | --- |
| `not_checked` | 未指定工作区 |
| `not_installed` | 指定位置没有该 Skill |
| `matches_source` | 包内容与当前源一致 |
| `differs_from_source` | 内容有差异，可能包括旧版本、用户编辑或其他来源 |
| `ambiguous_name` | 多源 frontmatter 名称重复，不能按未限定名称归属 |

已有工作区通过显式的离线迁移更新。先确认工作区和团队全部空闲，准备备份和差异，
检查同路径替换没有丢弃用户修改后再应用；不要在正在运行或等待审批的分析期间执行。

```sh
PYTHONPATH=src python3 scripts/migrate_skills.py prepare --workspace /absolute/workspace/root --output /absolute/new/migration
PYTHONPATH=src python3 scripts/migrate_skills.py apply /absolute/new/migration/plan.json
PYTHONPATH=src python3 scripts/migrate_skills.py rollback /absolute/new/migration/plan.json
```

准备步骤不改工作区，输出 `backup-skills/`、`prepared-skills/`、`changes.diff` 和计划。
仅覆盖当前选中的项目适配包的同路径文件，保留额外文件、自定义包和显式用户覆盖的包。
应用和回滚均检查整棵 Skill 树的哈希；准备后有新编辑会拒绝覆盖。回滚后再准备新方案。
该脚本不接入运行锁或停止分析，操作前必须核对实际服务的空闲状态和挂载路径。

当前运行实例使用 `traceglass-data-accounts`，与工作目录默认配置的
`traceglass-data-integrated` 不同。此次按运行实例挂载路径更新，历史测试目录未批量修改。
部署详情见 [本轮迁移记录](../traceglass-data/skill-rollouts/20261019T041747Z/迁移记录.md)。

`field-journal`、`tool-index.md` 等旧库共享资源仍按需通过已注册的 `read_skill_resource` 读取；
旧库其余 33 个包及复制保留的历史参考尚未逐份改写，涉及旧工具名时以当前入口、实际 schema 和部署能力为准。
