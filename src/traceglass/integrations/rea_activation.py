"""Actionable REA availability errors without changing activation or permissions."""
from contextlib import contextmanager
from functools import wraps
import json

from agentscope.exception import ToolGroupInactiveError, ToolNotFoundError


@contextmanager
def rea_activation_hints(toolkit, *, connected: bool, prefixes=('mcp__rea__',), discovery_tool='SearchAnalysisTools'):
    """Adapt one reply's toolkit, leaving native validation/execution intact.

    AgentScope 2.0.6 filters inactive tools before checking their existence.
    Its on_acting hook runs AFTER that check, so enrich the availability error
    on this instance only. Restore even when a reply is interrupted or fails.
    """
    original = toolkit.check_tool_available
    previous = vars(toolkit).get('check_tool_available')

    @wraps(original)
    async def check(tool_name, activated_groups):
        try:
            return await original(tool_name, activated_groups)
        except (ToolNotFoundError, ToolGroupInactiveError) as exc:
            if not tool_name.startswith(prefixes):
                raise
            groups = toolkit.tool_groups
            group = next((g for g in groups if g.name != 'basic'
                          and g.name not in activated_groups
                          and any(t.name == tool_name for t in g.tools)), None)
            if group is not None:
                keep = {g.name: True for g in groups
                        if g.name != 'basic' and g.name in activated_groups}
                payload = {
                    'error': 'tool_group_inactive', 'tool': tool_name,
                    'required_group': group.name,
                    'reset_tools': {**keep, group.name: True},
                    'next_step': '该工具已注册，但所属组未激活。先调用 reset_tools，使用返回的参数保留当前组并启用所需组；'
                                 '等激活成功后读取更新后的工具 schema，补齐必填参数再重试。不要原样重试或判断工具不存在。',
                }
                raise ToolGroupInactiveError(json.dumps(payload, ensure_ascii=False)) from exc
            payload = {
                'error': 'tool_not_found', 'tool': tool_name,
                'next_step': (f'当前没有注册该工具。使用 {discovery_tool} 按完整工具名或简短关键词查询实际工具，'
                              '以返回的工具名、组和 schema 为准，不要猜测名称或原样重试。' if connected else
                              '本会话未连接 REA。使用已连接的其他分析工具并说明限制，不要猜测工具名或反复重试。'),
            }
            raise ToolNotFoundError(json.dumps(payload, ensure_ascii=False)) from exc

    toolkit.check_tool_available = check
    try:
        yield
    finally:
        if previous is None:
            del toolkit.check_tool_available
        else:
            toolkit.check_tool_available = previous
