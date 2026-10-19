"""Explicit exposure policy; overlapping engines remain distinct callable tools."""

REVERSE_GROUPS = {
    'reverse_native': ('binary_metadata', 'list_sections', 'list_imports', 'list_exports',
                       'list_functions', 'rank_key_functions', 'disassemble_function',
                       'decompile_function', 'ghidra_summary'),
    'reverse_dynamic': ('identify_dynamic_report', 'dynamic_report_overview', 'dynamic_process_tree',
                        'query_dynamic_events', 'extract_dynamic_iocs', 'rank_dynamic_behaviors',
                        'correlate_static_dynamic_evidence'),
    'reverse_malware': ('detect_packer', 'yara_scan', 'extract_ioc_candidates', 'floss_strings',
                        'capa_analyze', 'pe_anomaly_scan', 'pe_header_report'),
    'reverse_crypto': ('crypto_detector_scan', 'record_crypto_experiment'),
    'reverse_rules': ('validate_sigma_rule', 'convert_sigma_rule'),
    'reverse_execution': ('reverse_tool_call', 'archive_sandbox_experiment'),
    'reverse_ida_bridge': ('ida_headless_call',),
}

GROUP_DESCRIPTIONS = {
    'windows_analysis': 'Windows 虚拟机 ESXi VMware Tools：工具探测、静态分析、样本执行、x64dbg 动态调试、寄存器、断点、单步、内存转储和任务结果回收；需管理员按账号启用',
    'reverse_native': '备用静态逆向：radare2/r2ghidra/Ghidra 元数据、函数、汇编、反编译；REA 不支持、失败或需要独立复核时启用',
    'reverse_dynamic': '动态报告：格式识别、进程树、事件、IOC、行为排序及静动态关联；解析已有报告，不等于执行样本',
    'reverse_malware': '恶意代码专项：DIE、YARA、FLOSS、capa、PE 异常与 IOC 提取',
    'reverse_crypto': '加解密与混淆：算法线索扫描、密码学实验归档',
    'reverse_rules': 'Sigma 规则验证与转换',
    'reverse_execution': '高级工具入口及沙箱实验归档；按任务启用，仍需原生权限检查',
    'reverse_ida_bridge': '原 reverse 的 IDA 桥接入口；与直接 IDA 的路径、权限和会话语义不同，按需使用',
    'analysis_ida': '直接 IDA 工具：用户指定 IDA、其他引擎不支持或独立复核时启用；勿假定与 REA 共享当前样本',
}

# Ordered recommendations, NOT aliases. Arguments, engine state and outputs
# can differ; search returns each original schema's name without translating it.
CAPABILITIES = {
    'decompile': ('反编译 decompile pseudocode', (
        'mcp__rea__procedure_pseudo_code', 'mcp__reverse__decompile_function')),
    'disassemble': ('汇编 反汇编 disassemble assembly', (
        'mcp__rea__procedure_assembly', 'mcp__reverse__disassemble_function')),
    'functions': ('函数 查找 functions search', (
        'mcp__rea__search_procedures', 'mcp__rea__list_procedures', 'mcp__reverse__list_functions')),
    'metadata': ('概览 元数据 metadata overview', (
        'mcp__rea__binary_overview', 'mcp__reverse__binary_metadata', 'mcp__reverse__ghidra_summary')),
    'raw_strings': ('原始 字符串 strings extract', (
        'mcp__reverse__extract_strings', 'extract_strings')),
    'indexed_strings': ('索引 字符串 strings search', (
        'mcp__rea__search_strings', 'mcp__rea__list_strings')),
    'hash': ('身份 哈希 hash sha256', ('mcp__reverse__hash_file', 'inspect_file')),
    'format': ('格式 架构 format identify', ('mcp__reverse__identify_file',)),
}


def optional_group(name: str, *, rea_connected: bool) -> str | None:
    if name in {'WindowsCapabilities', 'WindowsVmStatus', 'WindowsRunTool', 'WindowsJobStatus', 'WindowsCollectResults',
                'WindowsCancelJob', 'WindowsDebug', 'WindowsResetGuest'}:
        return 'windows_analysis'
    if name.startswith('mcp__idapro__'):
        return 'analysis_ida'
    if name.startswith('mcp__reverse__'):
        short = name.removeprefix('mcp__reverse__')
        for group, names in REVERSE_GROUPS.items():
            if short in names:
                return None if group == 'reverse_native' and not rea_connected else group
    return None
