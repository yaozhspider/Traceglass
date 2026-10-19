"""Bounded metadata search over connected tools; native ResetTools activates them."""
import json
import re

from agentscope.message import TextBlock
from agentscope.tool import FunctionTool, ToolChunk


class ReaToolDiscovery:
    def __init__(self, core, groups):
        self.groups = groups
        self.entries = []
        for group, members in [(None, core), *((g, g.tools) for g in groups)]:
            for tool in members:
                self.entries.append({
                    'name': tool.name,
                    'description': (tool.description or '')[:600],
                    'group': group.name if group else None,
                    'reset_tools': {group.name: True} if group else {},
                    'input_schema_chars': len(json.dumps(tool.input_schema)),
                })
        self.entries.sort(key=lambda entry: entry['name'])

    async def search(self, query: str = '', offset: int = 0, limit: int = 8) -> ToolChunk:
        """Search all connected REA tools (including inactive groups) by exact tool name or English keywords, e.g. trace_native_values, call path, android, unknown. Empty query browses the catalog. Returns bounded descriptions and reset_tools arguments, NOT full schemas. Does not activate or execute tools. Merge needed groups when calling reset_tools: it replaces all optional groups."""
        if len(query) > 200 or offset < 0 or not 1 <= limit <= 20:
            return ToolChunk(state='error', content=[TextBlock(text='Use query <= 200 characters, offset >= 0 and limit 1..20.')])
        terms = re.findall(r'\w+', query.lower().replace('_', ' '))
        ranked = []
        for entry in self.entries:
            name = entry['name'].lower()
            words = name.replace('_', ' ')
            description = entry['description'].lower()
            if terms and not all(term in words or term in description for term in terms):
                continue
            score = sum(3 if term in words else 1 for term in terms)
            if query.strip().lower() in (name, name.removeprefix('mcp__rea__')):
                score += 100
            ranked.append((score, entry))
        ranked.sort(key=lambda item: (-item[0], item[1]['name']))
        result = {
            'connected_tools': len(self.entries), 'total_matches': len(ranked),
            'matches': [entry for _, entry in ranked[offset:offset + limit]],
            'next_offset': offset + limit if offset + limit < len(ranked) else None,
            'activation_note': 'Core tools need no activation. Merge still-needed groups into reset_tools; omitted groups are disabled. Discovery does not guarantee provider support.',
        }
        if not terms or not ranked:
            result['groups'] = [{'name': g.name, 'description': g.description[:220], 'count': len(g.tools)} for g in self.groups]
        return ToolChunk(state='success', content=[TextBlock(text=json.dumps(result, ensure_ascii=False))])

    def tool(self):
        return FunctionTool(self.search, name='SearchReaTools', is_read_only=True)
