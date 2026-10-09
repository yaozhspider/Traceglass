# Traceglass

Traceglass is a reverse-analysis workbench built on **AgentScope 2.0.6**.
AgentScope is the single execution runtime: ReAct, task planning, teams,
permissions, sessions, storage, tools, skills, MCP and streaming all use its
native implementation.

The former custom Planner, PlanExecutor, SubagentManager, TraceService and
TraceStore have been removed. There is no fixed five-stage execution loop.
The analyst decides whether to answer directly, use Task tools, or delegate
an independent question with Team/Agent tools.

## Run locally

Python 3.11+ and Node 22.18+ are required. Install into a virtual environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
```

Configure an OpenAI-compatible model with environment variables. API keys are
kept out of source control; the opt-in legacy profile below reads the old
project's settings and seeds native credentials in the local data directory:

```sh
export LLM_MODEL='your-model-name'
export LLM_API_KEY='your-key'
export LLM_BASE_URL='https://your-model-endpoint/v1'
traceglass
```

`LLM_BASE_URL` is optional for the provider's default endpoint. `.env.example`
is a reference; this application does not automatically source shell files.
The server binds to `127.0.0.1:8000`. Without model configuration it still
starts and can browse saved sessions; the UI explains what is missing.

In a second terminal:

```sh
cd web
npm ci
npm run dev
```

Open the Vite address printed in the terminal. The `/api` dev proxy forwards
to the backend, including SSE, so no cross-origin setup is needed locally.

To run with the existing reverse-analysis implementation, point Traceglass at
the old project and build the isolated analysis image once:

```sh
export TRACEGLASS_LEGACY_PROJECT=/Users/yaozh/llms/reverse
export TRACEGLASS_DATA_DIR=/absolute/path/to/traceglass-data
python scripts/build_analysis_image.py \
  --legacy-project "$TRACEGLASS_LEGACY_PROJECT"
export IDA_ENABLED=true
export IDA_MCP_URL=http://host.docker.internal:8745/mcp
export TRACEGLASS_REA_KIT=/Users/yaozh/llms/reverse/dist/rea-container-kit
traceglass
```

The old project is read-only. Traceglass loads its model settings, discovers 45
legacy Skill packages plus the built-in Traceglass and REA Skills (49 effective packages, including the official REA Skill), exposes 41
legacy reverse tools through a native AgentScope MCP client, and copies samples,
rules and reports into its own Docker workspace. IDA contributes its tools only
when the separately started IDA MCP service is reachable. If IDA is offline,
the session can still use the static reverse MCP tools and the UI should report
IDA as unavailable.

REA is optional. The pinned 6.1.0 kit supplies 138 tools, including Ghidra and
Hopper integration, through a separate native MCP connection for each Agent.
Twelve common tools are equipped initially; native `reset_tools` activates the
remaining tool groups on demand to keep large schemas out of routine requests.
The source package now includes 13 core analysis Skills with references/scripts,
so its main analysis workflows are discoverable without the legacy checkout.
External tool availability still depends on the deployment. See
[Skills and tools](docs/SKILLS_AND_TOOLS.md) for paths, runtime limits and validation.

The REA 6.1 integration also exposes six native workflows and archives evidence
for shared access by the leader, workers and workbench. See
[REA upgrade and testing](docs/REA_INTEGRATION.md) for building, the isolated test
entry point, supported backends and rollback. The original kit stays usable.

## What is implemented

- Native AgentScope service composition with SQLAlchemy/SQLite persistence.
- Workspace home, search and creation; sample upload/selection; workspace-bound
  native conversations; file browsing, UTF-8/hex preview and original download.
  Uploading selects the sample, and switching samples opens a new analysis.
  Workspaces and sample associations survive refresh and server restart.
- New local and Docker workspaces use distinct host directories. Historical
  sessions retain their original layout and import a bounded sample catalog.
  File endpoints reject traversal and symlinks, limit uploads to 100 MiB,
  and bound text previews to 256 KiB and binary previews to 4 KiB.
- One analyst dynamically creates specialist workers through the native
  `default` template; `static-reverse`, `dynamic-evidence`, and `verifier` remain
  optional shortcuts. Framework Team tools control execution and communication.
- The leader writes a visible plan with `TodoWrite` before delegation, assigns
  owners and reviews evidence before completing tasks. This bulk adapter writes
  AgentScope's native Task state, preserving Task IDs, dependencies and history.
  Each team can contain at most **8 workers, excluding its leader**. The service
  serializes native team mutations and rejects a ninth Create/Invite; existing
  workers can be reused through TeamSay. Model-initiated TeamDelete is rejected
  to preserve child-session evidence/history and prevent resetting this limit.
- Environment-configured model credentials, Skill directories and native MCP
  configurations. Standard workspace tools come from AgentScope.
- Read-only integration with the existing reverse project: its OpenAI-compatible
  model, 45 Skills, 41 reverse MCP tools, analysis archive functions and Docker
  image are available through native AgentScope workspace bindings.
- Native `FunctionTool` extensions: `inspect_file`, `extract_strings`, and
  `write_report`. These inspect workspace bytes or create an immutable report;
  they never execute a sample.
- Real session list and task submission; native stop and permission confirmation.
  Setup status comes from the native run registry, so container/MCP preparation
  is visible and cancellable. Pending questions remain visible across reloads
  until the native user message is saved; the UI never automatically resends.
- IDA is optional: static MCP starts first, then the native add-MCP API probes
  IDA. Connection failures are shown in the workspace and model instructions,
  while available static tools continue to work.
- Streaming text/available thinking blocks, expandable tool inputs/results,
  live Todo state from native state-change events, raw received events and
  expandable child-session history.
- Native persisted message history after restart. Live event delivery uses
  AgentScope's in-memory bus and reconnect replay buffer.
- Each reply defaults to **2000 ReAct iterations**. The last iteration is
  reserved for a model-written evidence summary and remaining work; Continue
  starts a fresh budget in the same session. Native context compression remains
  enabled. See [pause, continuation and budget behavior](docs/2026-10-08-pause-and-continuation.md).
- Pause All interrupts the leader, its owned workers and registered background
  tools. A leader can explicitly reassign a paused worker with named TeamSay,
  retaining its history; late reports and broadcasts do not resume paused runs.
- An app-local compatibility adapter re-enqueues a native wake when a background
  result arrives during the final model call. It keeps native scheduling, skips
  duplicate empty wakes, and preserves explicit continuation and stop semantics.

All displayable content the provider/framework emits can be inspected. A model
that does not return reasoning text cannot have that text reconstructed. The
UI does not invent analysis events. Per-token event buffers are not a separate
permanent audit log; native message blocks and tool results are persisted.

## Structure

```text
src/traceglass/
  api/               # Native service composition and product workspace routes
  config.py          # Deployment settings, Skill paths, native MCP records
  agents.py          # Analyst prompt and native subagent templates
  delegation.py      # Todo prerequisite and team-size policy; no scheduler
  closeout.py        # Last-step model summary and native worker reporting
  workspaces/        # Workspace catalog, sample files and native path adapters
  integrations/      # Legacy MCP bridge and pinned AgentScope compatibility
  tools/             # Reverse-analysis FunctionTools
  resources/skills/  # Packaged analysis Skill
  domain/            # Evidence/finding/artifact records, no execution state
web/src/features/
  execution/         # Native event projection, SSE, session timeline
  workspaces/        # Workspace navigation, sample upload, files and chat input
```

Evidence domain records are validation contracts; a database-backed evidence
review/report-versioning product has not been implemented yet.

## Skills and MCP

See [SKILLS_AND_TOOLS.md](docs/SKILLS_AND_TOOLS.md). For example, from this
project directory, reuse the old project's Skill packages without modifying it:

```sh
export TRACEGLASS_SKILL_ROOTS='../reverse/skills'
export TRACEGLASS_MCP_CONFIG='/absolute/path/to/mcp.json'
```

Loading a Skill does not install external binaries that the Skill merely
describes. The legacy integration does provide the old reverse domain tools and
Skill reference resources through the workspace MCP client; external backends
such as IDA still need their own service. The old `re_agents` factory and
execution engine are not imported.

## Verification

```sh
python -m pytest
python -m compileall -q src
cd web
npm test
npm run build
```

Integration tests run the real AgentScope service, Task tools, domain tools and
Team workers against a scripted provider transport. They check execution and
persistence, including child history. They do not prove external LLM availability
or acceptance on real reverse-engineering samples.

## Deployment limits

This revision is a **local, single-process development workbench**, not a
completed multi-tenant production deployment. The native `X-User-ID` header
is identity routing, not authentication. Do not expose this service publicly
without an authenticated gateway and a suitable sandbox configuration.

New product workspaces adapt the native host-directory mapping for both local
and Docker backends, so each workspace has its own files while team members
share the leader's files. Legacy workspace IDs retain the original shared
per-agent layout. Local directories do not isolate shell processes; use the
Docker profile for analysis execution. Live bus state is process-local;
deployment across workers requires the native Redis message bus and testing.
Queued inbox messages and replay buffers do not survive a process restart.
The wakeup compatibility adapter depends on AgentScope 2.0.6's queue format and
task naming; rerun its regression tests before upgrading the framework.
The delegation policy's lock is also process-local and must become shared for
a multi-process service. The 8-worker limit is per team, not a global model
request limit across all user sessions. Models cannot delete and recreate a
team to reset the member budget.

The integrated profile has passed a browser-driven upload, real model, Docker,
reverse MCP, two-worker delegation and leader-review test. Optional IDA was
offline during this test; static analysis continued and the UI showed that
status. Product release still requires authentication and
deployment setup, policy review for write/dynamic tools, and long-session UI
and load testing.

Analysis coverage, same-host IDA input staging and badcase regression commands: [Analysis quality](docs/ANALYSIS_QUALITY.md).
