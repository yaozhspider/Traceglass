import asyncio
from pathlib import Path

from agentscope.app.storage import AsyncSQLAlchemyStorage
from traceglass.accounts.identity import ensure_analyst, personal_agent_id
from traceglass.agents import analyst_record, ANALYST_PROMPT


def test_existing_personal_default_is_upgraded_and_custom_prompt_is_preserved(tmp_path):
    old = (Path(__file__).parents[1] / 'fixtures/analyst_prompt_v3.txt').read_text()
    async def run():
        async with AsyncSQLAlchemyStorage(url=f'sqlite+aiosqlite:///{tmp_path}/agents.db') as storage:
            for user, prompt in [('alice', old), ('bob', '用户自己的分析角色')]:
                record = analyst_record(user, 57)
                record.id = record.data.id = personal_agent_id(user)
                record.data.name = '保留昵称'
                record.data.system_prompt = prompt
                await storage.upsert_agent(user, record)
                await ensure_analyst(storage, user, 2000)
                await ensure_analyst(storage, user, 2000)
                saved = await storage.get_agent(user, record.id)
                assert saved.data.system_prompt == (ANALYST_PROMPT if user == 'alice' else prompt)
                assert saved.data.name == '保留昵称' and saved.data.react_config.max_iters == 57
    asyncio.run(run())
