# Native AgentScope correction

The user has directed that AgentScope's existing runtime be reused. This
supersedes the custom Planner/Executor and TraceService design from September 24.

## Boundary

AgentScope 2.0.6 owns ReAct, Task tools, Team tools, permissions, context,
session persistence, interrupt/resume, workspace tool/skill/MCP discovery and
SSE. Traceglass owns analysis prompts, native subagent templates, domain tools,
configuration and the workbench. There is no second execution loop or event store.

## Implementation

1. Use `agentscope.app.create_app` in `src/traceglass/api/app.py`, with native
   SQLAlchemy storage, message bus and local workspace manager. Seed a single
   analyst and an environment-configured credential via native storage.
2. Add `config.py` for validated environment settings, Skill directory discovery
   and native MCPClient configuration. Add `agents.py` for prompts/templates.
3. Delete custom agent/execution/trace implementations and superseded tests.
   Keep passive evidence/finding/artifact domain contracts.
4. Wire native read-only sample tools via `extra_agent_tools`; use framework
   workspace Skill and MCP loading. Never write to the old reverse project.
5. Connect the workbench to native session/chat/SSE APIs, with task submission,
   stop/permission controls, persisted history and expandable child sessions.
   Keep streaming text grouped while preserving inspectable provider events.
6. Verify configuration rejection, workspace-scoped tools, service startup and
   persistence, native Task/Team behavior and a scripted-provider execution.
   Run Python tests and frontend build. A provider fixture does not count as
   a real external-model acceptance test.

## Deployment boundary

The default is a local single-process workbench, not a public multi-tenant SaaS.
SQLite is the native durable store; the in-memory native message bus provides
live local delivery. Authentication, distributed live delivery and hardened
sandbox deployment require deployment configuration and separate acceptance.
