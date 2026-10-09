"""Expose REA through native ToolGroups without eagerly sending huge schemas."""
import json

from agentscope.middleware import MiddlewareBase
from agentscope.tool import ToolGroup


CORE = frozenset({
    'binary_session', 'open_binary', 'close_binary', 'binary_overview',
    'search_procedures', 'search_strings', 'procedure_pseudo_code',
    'procedure_info', 'procedure_callers', 'procedure_callees', 'xrefs', 'read_bytes',
})


def rea_tool_groups(tools):
    """Keep provider schemas intact; isolate large contracts into opt-in groups."""
    core, grouped = [], {}
    for tool in sorted(tools, key=lambda t: t.name):
        name = tool.name.removeprefix('mcp__rea__')
        if name in CORE:
            core.append(tool)
            continue
        if len(json.dumps(tool.input_schema)) > 12000:
            group = name  # Large reconstruction schemas should activate individually.
        elif any(word in name for word in ('web', 'browser', 'javascript', 'electron')):
            group = 'web'
        elif any(word in name for word in ('android', 'managed', 'firmware', 'artifact',
                                         'objc', 'swift', 'macho', 'plist', 'apple',
                                         'signature', 'architecture', 'asset', 'archive', 'interface_builder')):
            group = 'formats'
        elif any(word in name for word in ('evidence', 'unknown', 'reconstruction', 'compare',
                                         'process', 'scenario', 'observe', 'build_call_path')):
            group = 'validation'
        else:
            group = 'native'
        grouped.setdefault('rea_' + group, []).append(tool)
    groups = [ToolGroup(name=name,
        description='REA: ' + ', '.join(t.name.removeprefix('mcp__rea__') for t in members),
        instructions='Use only on supported providers. Keep only currently needed groups active; '
                     'reset_tools replaces the entire group selection. Save evidence in the workspace.',
        tools=members) for name, members in sorted(grouped.items())]
    return core, groups


class ReaToolsMiddleware(MiddlewareBase):
    def __init__(self, tools):
        self.tools = tools

    async def on_reply(self, agent, input_kwargs, next_handler):
        core, groups = rea_tool_groups(self.tools)
        await agent.toolkit.add_tool(core)
        existing = {g.name for g in agent.toolkit.tool_groups}
        # This public list is also referenced by native ResetTools; mutate it in
        # place so native schema discovery and persisted activation state agree.
        agent.toolkit.tool_groups.extend(g for g in groups if g.name not in existing)
        async for event in next_handler(**input_kwargs):
            yield event
