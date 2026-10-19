"""Stable, globally unique AgentScope IDs for each account."""
import hashlib
from traceglass.agents import AGENT_ID, analyst_record, upgrade_agent_prompt, upgrade_shipped_iteration_limits


def personal_agent_id(user_id: str) -> str:
    return AGENT_ID + '-' + hashlib.sha256(user_id.encode()).hexdigest()[:32]


async def ensure_analyst(storage, user_id: str, max_iters: int):
    agent_id = personal_agent_id(user_id)
    if await storage.get_agent(user_id, agent_id) is None:
        record = analyst_record(user_id, max_iters)
        record.id = record.data.id = agent_id
        await storage.upsert_agent(user_id, record)
    await upgrade_agent_prompt(storage, user_id, agent_id)
    await upgrade_shipped_iteration_limits(storage, user_id, max_iters, agent_id)
    return agent_id
