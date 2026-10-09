"""Reserve the last native ReAct step for an evidence-grounded closeout."""
import logging

from agentscope.app._tool import TeamSay
from agentscope.event import HintBlockEvent, ReplyEndEvent
from agentscope.message import HintBlock, Msg
from agentscope.middleware import MiddlewareBase
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import ToolChoice

CLOSEOUT_SOURCE = 'traceglass.iteration_closeout'
logger = logging.getLogger(__name__)
CLOSEOUT_PROMPT = '''本次回复只剩最后一次模型调用，现在根据已有状态输出阶段性结论。
只对本次回复生效：不再调用工具，不新建或重新委派任务，不为凑结论补造证据。
用中文交代：已确认的结论及证据/地址/产物路径；尚未证实的推断；当前待办和各成员未完成事项；
限制与阻塞；如果继续分析，优先做什么。没有足够证据时明确写无法确认。
说明本次因执行步数预算而收尾，不代表整个任务已完成；不要把未完成 Todo 标成完成。
输出清晰可读的正文。用户之后选择继续执行时，可在新回复中恢复正常分析与工具调用。'''


class BudgetCloseoutMiddleware(MiddlewareBase):
    def __init__(self, report_summary=None):
        self._closing_reply = None
        self._report_summary = report_summary

    async def on_reply(self, agent, input_kwargs, next_handler):
        completed = False
        async for event in next_handler(**input_kwargs):
            if isinstance(event, ReplyEndEvent):
                completed = event.finished_reason == 'completed'
            if (isinstance(event, Msg) and completed and self._report_summary is not None
                    and self._closing_reply == agent.state.reply_id):
                # The final step is text-only, so it cannot call TeamSay itself.
                # Forward only that model-written conclusion through the native
                # inbox; do not invent a result or mark any task completed.
                try:
                    await self._report_summary(event.get_text_content() or '')
                except Exception:
                    logger.exception('Could not deliver worker budget summary for %s', agent.state.session_id)
                    hint = HintBlock(source='traceglass.closeout_delivery',
                        hint='阶段性结论已生成，但发送给主 Agent 失败；请从本会话查看结论。')
                    agent.state.append_context(agent.name, [hint])
                    yield HintBlockEvent(reply_id=agent.state.reply_id, block_id=hint.id,
                                         source=hint.source, hint=hint.hint)
            yield event

    async def on_reasoning(self, agent, input_kwargs, next_handler):
        # Structured replies already have AgentScope's own forced-output grace
        # policy. Traceglass's interactive analysis uses normal text replies.
        if (agent.state.reply_context.structured_schema is None
                and agent.state.cur_iter >= agent.react_config.max_iters - 1):
            self._closing_reply = agent.state.reply_id
            hint = HintBlock(hint=CLOSEOUT_PROMPT, source=CLOSEOUT_SOURCE)
            agent.state.append_context(agent.name, [hint])
            yield HintBlockEvent(reply_id=agent.state.reply_id, block_id=hint.id,
                                 source=hint.source, hint=hint.hint)
            input_kwargs = {**input_kwargs, 'tool_choice': ToolChoice(mode='none')}
        async for event in next_handler(**input_kwargs):
            yield event

    async def on_check_permission(self, agent, input_kwargs, next_handler):
        if self._closing_reply == agent.state.reply_id:
            # Also enforce locally if a provider ignores tool_choice=none.
            return PermissionDecision(behavior=PermissionBehavior.DENY,
                                      message='本轮进入阶段性收尾，不再执行新的工具操作。')
        return await next_handler(**input_kwargs)


def worker_closeout_reporter(storage, bus, workspaces, user_id, agent_id, session_id):
    async def report(summary: str):
        if not summary.strip():
            return
        session = await storage.get_session(user_id, agent_id, session_id)
        if session is None or not session.team_id:
            return
        team = await storage.get_team(user_id, session.team_id)
        if team is None or team.session_id == session_id:
            return
        if not any(m.owner_id == user_id and m.agent_id == agent_id and m.session_id == session_id
                   and m.role == 'created' for m in team.data.members):
            return
        leader = await storage.get_session(user_id, '', team.session_id)
        if leader is None or leader.team_id != team.id:
            return
        leader_agent = await storage.get_agent(user_id, leader.agent_id)
        tool = TeamSay(storage=storage, message_bus=bus, workspace_manager=workspaces,
                       user_id=user_id, agent_id=agent_id, session_id=session_id, role='worker')
        result = await tool(to=leader_agent.data.name if leader_agent else leader.agent_id,
            content='[执行预算阶段性汇报；不表示任务已完成]\n' + summary)
        if result.state == 'error':
            raise RuntimeError('Native TeamSay could not deliver the budget summary')
    return report
