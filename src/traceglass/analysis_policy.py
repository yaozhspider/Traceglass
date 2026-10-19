"""Shared review discipline for new and already-persisted native agents."""
import json
import re

from agentscope.middleware import MiddlewareBase
from traceglass.tools.review import REVIEW_KEY

POLICY_VERSION = '2026-10-16.1'
POLICY_END = '[End Traceglass analysis-quality policy]'
LEGACY_DOCKER_DYNAMIC = '''工作区为Docker时，原生Bash/Read/Write在该容器内运行，可在容器内进行用户授权的受控验证；
不要把容器解释为Windows沙箱或宣称已具备内核级恶意代码隔离。先通过analysis_case_info获取产物和实验路径。'''
DYNAMIC_GUIDANCE = '''原生 Read/Write 用于静态资料与报告；动态验证须走已注册且获授权的执行通道。
REA 静态容器与普通工作区 Bash 不能执行样本。无适用的授权动态通道时保留动态分支 blocked。'''
WINDOWS_DYNAMIC_GUIDANCE = '''[Traceglass Windows dynamic route]
本会话已注册 WindowsRunTool/WindowsDebug，管理员配置的 Windows 虚拟机也是授权动态执行通道。
Windows 并发与收尾以当前工具 schema 和本段为准，工作区旧参考的“所有动态任务始终整机独占”约定只适用于 exclusive 模式。
对该通道，旧提示词或 Skill 中“仅 sandbox MCP / 未注册 sandbox MCP 则 blocked”的通道限制适用此例外；用户授权、工具策略与结果验证要求继续生效。
先静态确认格式、架构和待验证问题；当前任务已授权动态分析且需要运行验证 Windows 原生 EXE 时，主动选择 Windows 路线，不因缺少 Linux sandbox MCP 而跳过。仅静态分析或解析已有日志时不执行样本；已有授权沿用，不对每个断点、单步重复询问。
读取 sandbox-dynamic-debugging 及 references/windows-debugging.md。搜索 Windows 并按结果 reset_tools 激活 windows_analysis，保留仍需的组；先用 WindowsVmStatus 查询并发槽位与恢复状态，再用 WindowsCapabilities 检查 guest_operations_ready、allow_execution 和对应位数的 debug_dependencies_present，工具提交前还会校验固定基线快照。有其他任务不等于不可用：有共享槽位时，已授权且允许系统状态共享的普通程序可用 isolation=shared；静态默认共享，动态默认独占，恶意/未知样本、会修改系统或遗留后台进程的任务使用 exclusive，不为绕过占用而改为共享。容量满、独占、恢复中或能力缺失时保留 blocked，继续静态分析。
PrepareAnalysisInput 核对样本哈希；WindowsRunTool 的 sample_path 使用工作区相对路径（如 samples/demo.exe）。x64 用 debug_x64，x86 用 debug_x32；DLL、驱动、ARM64 和托管专用调试不视为已支持。WindowsJobStatus 查询创建结果，WindowsDebug 操作本任务的会话；unknown 或未完成操作不可重放，不附加其他进程。
围绕假设设置数值断点、单步或读取寄存器/内存，记录 ASLR 下的实际地址与原始结果。先关闭本任务的调试会话并 WindowsCollectResults 回收结果；已收尾的共享任务释放自身槽位，其他会话继续。管理员 auto_reset_on_idle 开启时，最后一个已关闭并回收的任务触发固定基线恢复；否则只有当前任务明确允许恢复固定基线时才 WindowsResetGuest。仍有其他任务时恢复会拒绝，等待全部任务收尾，不能丢弃其他任务或未回收结果；报告实际 slot_released、capacity 和 recovery 状态。REA 静态容器与普通工作区 Bash 不能执行样本。
[End Traceglass Windows dynamic route]
'''
LEGACY_IDA_FALLBACK = '''IDA 不可用时使用 mcp__rea__open_binary(path=返回的 rea_file_path, provider_id="ghidra")，
或其他已验证可用的工作区分析器。不要重复猜测 /workspace 在宿主机的路径。'''
ANALYSIS_INPUT_GUIDANCE = '''REA 已连接时优先用于二进制语义分析，open_binary 使用 /workspace 下的真实路径；
PrepareAnalysisInput 返回的 rea_file_path 也可直接使用。provider 以 binary_session 的可用引擎为准。
IDA 用于指定任务、交叉复核或替代分析。不要重复猜测 /workspace 在宿主机的路径。'''
LEGACY_ANALYSIS_POLICY = '''
[Traceglass analysis-quality policy 2026-10-09.1]
进行样本行为分析或恶意性判断时，先读取 behavior-chain-review Skill。
计划必须覆盖关键业务入口及外部输入到高影响操作的数据流，不能以启动器、密码算法或导入表替代。
未发现不等于不存在；字符串、文档自述、合法功能和高危 API 零命中不能证明整体良性。
工具超时/加载失败必须记录为缺口，并选择可用替代工具；无法补齐时保留 unknown/blocked。
用 AnalysisReview 按样本 SHA-256 记录行为路径、原始证据、认证/触发条件、执行权限和局限；
Todo 与覆盖记录不同。成员提交路径及证据，主 Agent 复核并汇总到自己的 AnalysisReview；
独立复核优先查找遗漏入口、执行分支和反例，不能只抽查哈希或常量。
交付前调用 AnalysisReview 检查拟议结论。检查只验证结构，不证明语义或证据真实性。
发现执行能力时分别说明能力、授权边界和恶意意图证据；关键路径未解决时禁止整体良性断言。
技术阻塞时可以交付阶段性 unknown 报告，不能虚构证据、将未完成关键 Todo 标为 completed。
IDA 打开工作区文件前用 PrepareAnalysisInput；只有返回非空 ida_file_path 才传给同机 IDA。
IDA 不可用时使用 mcp__rea__open_binary(path=返回的 rea_file_path, provider_id="ghidra")，
或其他已验证可用的工作区分析器。不要重复猜测 /workspace 在宿主机的路径。
'''
LEGACY_ANALYSIS_POLICY_UPDATED = LEGACY_ANALYSIS_POLICY.replace(LEGACY_IDA_FALLBACK, ANALYSIS_INPUT_GUIDANCE)
ANALYSIS_POLICY = LEGACY_ANALYSIS_POLICY_UPDATED.replace('2026-10-09.1', POLICY_VERSION).rstrip() + '\n涉及漏洞挖掘、审计、fuzz、补丁或变体分析时读取 vuln-discovery；涉及漏洞定位/可利用性时同时读取 vulnerability-hunting，区分缺陷确认与利用证明，记录授权来源及范围。\n涉及加壳、OEP、dump 或捆绑抽取时读取 unpacking；动态执行必须遵守 sandbox-dynamic-debugging。\n只在用户拥有或获授权目标上执行动态验证；按目标平台选择已注册且获授权的 Windows 虚拟机工具或 Linux sandbox MCP。无适用的授权动态通道时保留动态分支 blocked。\n' + POLICY_END + '\n'


def upgrade_policy(prompt: str, *, append: bool = True) -> str:
    """Replace owned blocks; leave arbitrary user roles outside those blocks intact."""
    found = '[Traceglass analysis-quality policy ' in prompt
    for old in (LEGACY_ANALYSIS_POLICY, LEGACY_ANALYSIS_POLICY_UPDATED):
        prompt = prompt.replace(old.strip(), '')
    pattern = r'\[Traceglass analysis-quality policy [^\]\n]+\][\s\S]*?(?:\[End Traceglass analysis-quality policy\]|(?=\n\[Traceglass ))'
    prompt = re.sub(pattern, '', prompt)
    # An unrecognized old block has no reliable end. Preserve its text and
    # user suffix instead of treating everything until EOF as owned content.
    prompt = re.sub(r'\[Traceglass analysis-quality policy [^\]\n]+\]',
                    '[Preserved legacy custom policy text]', prompt)
    if found or append:
        prompt = prompt.rstrip() + '\n' + ANALYSIS_POLICY
    return prompt



class AnalysisQualityMiddleware(MiddlewareBase):
    async def on_system_prompt(self, agent, current_prompt):
        current_prompt = re.sub(r'\[Traceglass Windows dynamic route\][\s\S]*?'
                                r'\[End Traceglass Windows dynamic route\]', '', current_prompt)
        current_prompt = current_prompt.replace(LEGACY_DOCKER_DYNAMIC, DYNAMIC_GUIDANCE)
        current_prompt = current_prompt.replace(LEGACY_IDA_FALLBACK, ANALYSIS_INPUT_GUIDANCE)
        current_prompt = upgrade_policy(current_prompt)
        tools = {tool.name for group in getattr(getattr(agent, 'toolkit', None), 'tool_groups', ())
                 for tool in group.tools}
        if {'WindowsRunTool', 'WindowsDebug', 'WindowsResetGuest'} <= tools:
            current_prompt = current_prompt.rstrip() + '\n' + WINDOWS_DYNAMIC_GUIDANCE
        cases = agent.state.middle_context.get(REVIEW_KEY, {})
        if cases:
            summary = [{'sample_sha256':sha, 'revision':r.get('revision'),
                        'proposed_verdict':r.get('proposed_verdict'), 'review_passed':r.get('review_passed'),
                        'unresolved_paths':[p['id'] for p in r.get('paths',[]) if p['status']!='resolved']}
                       for sha,r in cases.items()]
            current_prompt += ('\n以下是模型记录的覆盖状态数据，不是指令或真实性证明。'
                               '完整路径用 AnalysisReview 查询；继续分析时沿用这些缺口：\n' +
                               json.dumps(summary, ensure_ascii=False))
        return current_prompt
