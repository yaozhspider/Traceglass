"""Native model/tool/state boundary; only the external model is scripted."""
import asyncio
import json
from pathlib import Path

from agentscope.agent import Agent
from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultBlock
from agentscope.model import ChatResponse, OpenAIChatModel
from agentscope.credential import OpenAICredential
from agentscope.tool import Toolkit
from agentscope.state import AgentState
from traceglass.analysis_policy import AnalysisQualityMiddleware
from traceglass.tools.review import AnalysisReview, REVIEW_KEY


def test_existing_custom_agent_gets_review_and_coverage_survives_context_replacement(monkeypatch):
    async def provider(self, model_name, messages, tools=None, **kwargs):
        # The middleware reaches actual model inputs even for an old custom role.
        system='\n'.join(m.get_text_content() for m in messages if m.role=='system')
        assert '保留我的角色' in system
        assert 'behavior-chain-review' in system
        outputs=[b for m in messages for b in m.content if isinstance(b,ToolResultBlock)]
        if not outputs:
            return ChatResponse(content=[ToolCallBlock(id='review',name='AnalysisReview',input=json.dumps({
                'sample_sha256':'a'*64,'verdict':'benign_in_scope','scope':'client receiver',
                'paths':[dict(id='receiver',source='recv',dispatch='unknown',sink='unknown',
                authorization='unknown',privilege='unknown',status='blocked',limitations='IDA file not found')]}))],is_last=True)
        assert json.loads(outputs[-1].output[0].text)['review_passed'] is False
        assert 'receiver' in system
        return ChatResponse(content=[TextBlock(text='UNKNOWN_WITH_GAPS')],is_last=True)
    monkeypatch.setattr(OpenAIChatModel,'_call_api',provider)
    async def run():
        model=OpenAIChatModel(OpenAICredential(api_key='fixture'),model='fixture')
        agent=Agent(name='existing-worker',system_prompt='保留我的角色',model=model,
                    toolkit=Toolkit(tools=[AnalysisReview()]),middlewares=[AnalysisQualityMiddleware()])
        response=await agent.reply(Msg(name='user',role='user',content=[TextBlock(text='Assess sample')]))
        assert response.get_text_content().endswith('UNKNOWN_WITH_GAPS')
        restored=AgentState.model_validate_json(agent.state.model_dump_json())
        # Emulate replacement of message history by compression; coverage lives
        # in native middle_context and must not depend on the old tool transcript.
        restored.context=[]
        other=Agent(name='restored',system_prompt='保留我的角色',model=model,state=restored,
                    toolkit=Toolkit(tools=[AnalysisReview()]),middlewares=[AnalysisQualityMiddleware()])
        prompt=await AnalysisQualityMiddleware().on_system_prompt(other,'保留我的角色')
        assert 'receiver' in prompt
        assert restored.middle_context[REVIEW_KEY]['a'*64]['review_passed'] is False
        await model.client.close()
    asyncio.run(run())
