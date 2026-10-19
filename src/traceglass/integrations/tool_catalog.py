"""A per-agent catalog and native opt-in groups for overlapping analysis tools."""
import asyncio
from collections import Counter
import json
import re

from agentscope.message import TextBlock
from agentscope.middleware import MiddlewareBase
from agentscope.tool import FunctionTool, ToolChunk, ToolGroup

from traceglass.integrations.rea_activation import rea_activation_hints
from traceglass.integrations.tool_policy import CAPABILITIES, GROUP_DESCRIPTIONS, optional_group

SEARCH_TOOLS = {'SearchAnalysisTools', 'SearchReaTools'}
CATALOG_GUIDANCE = '''
[Traceglass unified tool catalog]
用 SearchAnalysisTools 查询本会话已注册的全部工具（包含未激活组），支持完整名称、英文关键词或能力中文词。
REA deferred 表示仅已注册定义，首次实际使用才连接；注册、激活、连接与引擎可用性分别判断。
同类能力按首选入口和备用入口展示，但不同工具不是可互换的别名；路径参数、编码覆盖、引擎状态可能不同。
工具未出现在本轮 schema 时，按搜索结果调用 reset_tools，等待成功后读取 schema 再调用。
reset_tools 替换全部可选组；保留仍需的组，不一次启用所有组。搜索不会激活或执行工具。
每项新任务先按 traceglass-analysis/rea-analysis 评估 REA 相关能力，复用任务内已有判断。
常规二进制语义首选 REA；JS/扩展、托管等按格式检索入口，不强套原生引擎流程。
搜到适用工具后完成激活、读 schema 和调用，或记录前置条件不足/不适用原因。
纯文档、源码阅读或已有报告解析可记录不适用理由直接处理，不为此启动引擎。
其他引擎用于格式不支持、失败、独立复核或用户明确指定。
原始 ASCII/UTF-16 字符串、FLOSS 解密字符串与 REA 索引字符串不是同一种证据，不相互替代。
reverse 专项工具和直接 IDA 按需启用；激活不代表引擎可用或获得执行权限。
若已注册 Windows 工具，搜索 Windows/x64dbg 并启用 windows_analysis；先查询 WindowsVmStatus 容量，再检查 WindowsCapabilities，按实际 schema 调用。
Windows 工具路径已安装不代表调试插件可用。任务提交后用 WindowsJobStatus 查询；unknown 或待完成的调试操作不可重放。
Windows 静态任务默认共享，动态默认独占；仅普通且允许系统状态共享的已授权程序显式 isolation=shared，恶意/未知样本、系统修改或遗留后台进程的任务用 exclusive。关闭自有调试器并 WindowsCollectResults 回收后释放共享槽位；恢复快照必须等其他任务收尾，auto_reset_on_idle 由管理员配置，否则沿用固定基线恢复授权。用 WindowsVmStatus 查看占用与恢复状态，不因存在其他任务就直接放弃可用共享槽位。
SearchReaTools 保留为旧会话的 REA 专用查询入口；跨服务选择优先使用 SearchAnalysisTools。
'''


class ToolCatalog:
    def __init__(self, agent, entries, unavailable, removed):
        self.agent = agent
        self.entries = entries
        self.unavailable = unavailable
        names = [tool.name for _, tool in entries]
        self.names = set(names)
        self.audit = {
            'duplicate_references_removed': removed,
            'name_conflicts': sorted(name for name, count in Counter(names).items() if count > 1),
            'default_tools': len({t.name for g, t in entries if g is None}),
            'optional_tools': len({t.name for g, t in entries if g is not None}),
            'overlapping_capabilities': {key: [n for n in names if n in self.names]
                for key, (_, names) in CAPABILITIES.items()
                if sum(n in self.names for n in names) > 1},
        }

    async def search(self, query: str = '', offset: int = 0, limit: int = 8) -> ToolChunk:
        """Find registered tools across REA, reverse, IDA and native tools, including inactive groups. Deferred REA tools connect only on use; registration does not prove provider readiness. Use an exact name, short English keywords or Chinese capability words (反编译/汇编/字符串/哈希). Returns original names, activation arguments and overlapping alternatives; does NOT activate or execute. Preserve other needed groups in reset_tools and read the activated schema before calling. Empty query browses the catalog."""
        if len(query) > 200 or offset < 0 or not 1 <= limit <= 20:
            return ToolChunk(state='error', content=[TextBlock(text='Use query <= 200 characters, offset >= 0 and limit 1..20.')])
        active = set(self.agent.state.tool_context.activated_groups)
        groups = {g.name for g in self.agent.toolkit.tool_groups if g.name != 'basic'}
        keep = {name: True for name in sorted(active & groups)}
        terms = re.findall(r'\w+', query.lower().replace('_', ' '))
        rows = {}
        for group, tool in self.entries:
            capability, keywords, related = None, '', []
            for key, (words, names) in CAPABILITIES.items():
                if tool.name in names:
                    capability, keywords = key, words
                    related = [n for n in names if n in self.names]
                    break
            description = (tool.description or '')[:600]
            short = tool.name.split('__', 2)[-1]
            exact = bool(query.strip()) and query.strip().lower() in (tool.name.lower(), short.lower())
            haystack = (tool.name.replace('_', ' ') + ' ' + description + ' ' + keywords + ' '
                        + GROUP_DESCRIPTIONS.get(group, '')).lower()
            hits = sum(term in haystack for term in terms)
            if terms and not hits and not exact:
                continue
            preferred = related[0] if related else None
            row = {
                'name': tool.name, 'description': description, 'group': group,
                'server': tool.name.split('__', 2)[1] if tool.name.startswith('mcp__') else 'native',
                'active': group is None or group in active,
                'capability': capability, 'preferred_tool': preferred,
                'alternatives': [n for n in related if n != tool.name],
                'reset_tools': {**keep, group: True} if group is not None else {},
                'input_schema_chars': len(json.dumps(tool.input_schema)),
                'read_only': tool.is_read_only,
                'connection_status': getattr(getattr(tool, '_session', None), 'connection_status', 'registered'),
            }
            score = (int(exact), hits, int(preferred == tool.name))
            rows[tool.name] = (score, row)
        ranked = sorted(rows.values(), key=lambda item: (tuple(-x for x in item[0]), item[1]['name']))
        result = {
            'registered_tools': len(self.names),
            'connected_tools': len({t.name for _, t in self.entries
                if getattr(getattr(t, '_session', None), 'connection_status', 'registered') in {'registered', 'connected'}}),
            'total_matches': len(ranked),
            'matches': [row for _, row in ranked[offset:offset + limit]],
            'next_offset': offset + limit if offset + limit < len(ranked) else None,
            'unavailable_servers': self.unavailable, 'audit': self.audit,
            'activation_note': 'Suggestions reflect registered definitions; deferred tools are not connected. Alternatives are not aliases. '
                               'reset_tools replaces optional groups; returned arguments preserve current groups. Read the actual schema after activation.',
        }
        if not terms or not ranked:
            result['groups'] = [{'name': g.name, 'description': g.description[:220],
                                 'active': g.name in active} for g in self.agent.toolkit.tool_groups if g.name != 'basic']
        return ToolChunk(content=[TextBlock(text=json.dumps(result, ensure_ascii=False))])


class AnalysisToolsMiddleware(MiddlewareBase):
    async def on_reasoning(self, agent, input_kwargs, next_handler):
        if getattr(agent.toolkit, '_traceglass_catalog_dirty', False):
            agent.toolkit._traceglass_catalog_dirty = False
            async for event in self.on_reply(agent, input_kwargs, next_handler):
                yield event
        else:
            async for event in next_handler(**input_kwargs):
                yield event

    async def on_system_prompt(self, agent, current_prompt):
        return current_prompt + CATALOG_GUIDANCE

    async def on_reply(self, agent, input_kwargs, next_handler):
        toolkit = agent.toolkit
        # Resolve metadata once per reply. Keep original tool objects (including
        # gateway dispatch, timeouts and permissions), never reconstruct proxies.
        clients = {id(c): c for g in toolkit.tool_groups for c in g.mcps}

        async def discover(client):
            try:
                return await asyncio.wait_for(client.list_tools(), timeout=8)
            except Exception:
                return None

        results = await asyncio.gather(*(discover(c) for c in clients.values()))
        discovered = dict(zip(clients, results))
        unavailable = sorted({clients[key].name for key, value in discovered.items() if value is None})
        removed = 0
        for group in toolkit.tool_groups:
            retained = []
            seen_clients = set()
            for client in group.mcps:
                if id(client) in seen_clients:
                    removed += 1
                    continue
                seen_clients.add(id(client))
                tools = discovered[id(client)]
                if group.name == 'basic' and client.name in {'reverse', 'idapro'} and tools is not None:
                    group.tools.extend(tools)
                else:
                    retained.append(client)
            group.mcps[:] = retained
            unique = {}
            for tool in group.tools:
                if id(tool) in unique:
                    removed += 1
                unique[id(tool)] = tool
            group.tools[:] = unique.values()

        basic = next(g for g in toolkit.tool_groups if g.name == 'basic')
        rea_connected = any(t.name.startswith('mcp__rea__') for g in toolkit.tool_groups for t in g.tools)
        rea_connected |= any(c.name == 'rea' and bool(discovered[id(c)]) for c in clients.values())
        grouped = {g.name: g for g in toolkit.tool_groups}
        retained = []
        for tool in basic.tools:
            target = optional_group(tool.name, rea_connected=rea_connected)
            if target is None:
                retained.append(tool)
                continue
            if target not in grouped:
                grouped[target] = ToolGroup(name=target, description=GROUP_DESCRIPTIONS[target],
                    instructions='Read the activated tool schema; preserve sample paths and engine state. Native permissions still apply.')
                toolkit.tool_groups.append(grouped[target])
            grouped[target].tools.append(tool)
        basic.tools[:] = retained

        entries = []
        for group in toolkit.tool_groups:
            tools = list(group.tools)
            for client in group.mcps:
                tools.extend(discovered[id(client)] or [])
            entries.extend((None if group.name == 'basic' else group.name, t)
                           for t in tools if t.name not in SEARCH_TOOLS)
        catalog = ToolCatalog(agent, entries, unavailable, removed)
        await toolkit.add_tool(FunctionTool(catalog.search, name='SearchAnalysisTools', is_read_only=True))
        with rea_activation_hints(toolkit, connected=True,
                prefixes=('mcp__reverse__', 'mcp__idapro__'), discovery_tool='SearchAnalysisTools'):
            async for event in next_handler(**input_kwargs):
                yield event
