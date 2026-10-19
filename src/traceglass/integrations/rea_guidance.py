"""Task-oriented discovery guidance, shared by new and persisted agents."""

LEGACY_TOOL_PREFERENCE = '连接了 reverse MCP 时优先使用其原有分析工具，并以工具列表中的真实名称和schema为准。'
TOOL_SELECTION = '每项任务先评估 REA 相关能力，按证据问题与目标格式选择工具；REA 与 reverse MCP 分别记录，工具名称和参数以当前 schema 为准。'

REA_ROUTING = '''
[Traceglass REA tool selection]
遵守用户和自定义任务的工具约束；以下为未指定工具时的默认选择。
每项新任务或目标变化时先读取 traceglass-analysis、rea-analysis，评估 REA 相关能力；
在计划或分析记录中写明适用能力、选用工具及前置条件/回退原因，同一任务复用已有判断。
主 Agent 和子 Agent：二进制语义分析优先使用 REA，不需要等 IDA 或 reverse 失败。
复杂调查再读取 reverse-engineer-anything，按需获取 REA workflow；本项目部署差异以 rea-analysis 为准。
原生目标用 binary_session 确认当前目标再 open_binary；binary_overview 仅用于支持它的 provider。
提取后的 JS/扩展目录检索 analyze_javascript_application；托管程序检索 inspect_managed_artifact，
Android 检索 inspect_android_package；这些显式路径工具不先套用原生引擎打开流程。
首次或目标改变才打开文件；同一会话已有匹配目标时复用，不在每步反复探测或打开。
纯源码阅读、文档、哈希、规则扫描或已有动态报告解析也先做适用性判断；不适用时使用对应工具，
记录理由即可，不启动 REA 引擎或运行无关诊断。REA 与 reverse MCP 的调用不能混算。

12 个常用 REA 工具直接可见，其余已连接工具通过 SearchAnalysisTools 检索，再用原生 reset_tools 启用。
调用前以本轮实际可用的工具定义为准；Skill、历史调用和检索结果不代表工具当前已激活。
工具未出现在当前列表时，先 SearchAnalysisTools（完整工具名或简短关键词），再按结果 reset_tools；
等待激活成功，读取更新后的 schema 并补齐必填参数，再单独调用目标工具，不与激活并行。
发现适用工具后明确执行或记录前置条件不足的原因；搜索成功不是分析完成，未尝试不写成工具失败。
遇到 tool_group_inactive，按错误中的 required_group 和 reset_tools 参数恢复；
遇到 ToolNotFoundError/tool_not_found，先检查工具发现与激活状态，不原样重试，不直接断言 REA 没有此能力。
遇到未知能力、要编写 Bash/Python 重做反编译/交叉引用/调用图/数据流或 JS 语义前，先检索是否有合适 REA 工具；
缺失、当前 provider 不支持或已有明确失败时可以直接换用 IDA、reverse 或脚本，并说明缺口。
不要求每步检索或重复失败调用，也不为了工具调用率制造无用工作。根据返回证据决定下一步。
检索词优先用工具名或英文关键词，例如：
- 函数实现：analyze_function / batch_decompile；汇编确认：procedure_assembly。
- 远程命令、后门触发、加解密：trace_native_values / trace_call_path / build_call_path；
  沿网络输入→解析/分派→执行点追踪，记录条件及权限边界；API 或字符串命中不等于路径已证实。
- 间接调用/虚表：resolve_native_call_targets / inspect_native_dispatch_metadata。
- Android、托管程序、固件、JS 制品：inspect_android / inspect_managed / firmware / javascript。
- 交叉验证、版本差异和未解问题：compare_functions / correlate_static_and_runtime / unknown。
检索仅发现工具，不证明当前 provider 支持；工具失败需保留局限，不能推导样本良性。
reset_tools 替换全部可选组，合并本步仍需的其他组，完成后关闭大型组；绝不一次加载全部。
委派时传入样本路径、哈希、证据问题和相关 REA 工具线索；成员必须独立打开自己的目标。
已有证据先按需 QueryReaEvidence/ReadReaEvidence 复用；不能沿用其他成员的引擎句柄。
'''

GROUP_PURPOSES = {
    'rea_native': '原生二进制：函数反编译、汇编、调用图、值流追踪、间接调用解析、标注',
    'rea_formats': '专用制品：Android/JADX、.NET/CLR 托管代码、固件、Apple/Swift、归档结构；'
                   '.NET 身份与成员分析需启用本组（inspect_managed_artifact、inspect_managed_members）',
    'rea_web': 'Web/JavaScript/Electron：包结构、源码映射、事件/运行时证据；需要对应环境',
    'rea_validation': '验证：调用路径、证据账本、未知项、版本比较、静态与运行时证据关联',
}
