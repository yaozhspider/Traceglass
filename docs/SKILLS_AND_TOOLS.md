# Native Skills, Tools and MCP

AgentScope owns discovery, schema generation, permissions, execution and events.
Traceglass does not have a parallel Skill registry or Tool policy engine.

## Skills

The project bundles **12 Skills**, including dynamic-malware-analysis,
malware-analysis, crypto-obfuscation-analysis, sandbox-dynamic-debugging,
reverse-engineering and its nested dsl-vm-reverse, ghidra-reverse,
key-function-analysis, protocol-reverse, rea-analysis, traceglass-analysis and behavior-chain-review.
References and scripts are included in the Python package, not just SKILL.md.
Imported sources and SHA-256 records are in `resources/skill-sources.json`.
`TRACEGLASS_SKILL_ROOTS` accepts a path-separated list of package roots; each may
be a directory containing `SKILL.md` or a tree containing Skill packages.
`config.skill_paths()` checks collisions; the native LocalWorkspaceManager
copies the packages, and Toolkit supplies the Skill Viewer.

```sh
export TRACEGLASS_SKILL_ROOTS='../reverse/skills'
```

Packages are copied to the new project's workspace; the original repository is
not changed. Skill instructions that reference sibling resources or tools must
have those resources/tools installed in the workspace. Merely exposing a Skill
is not proof that every workflow it describes is available. Existing workspaces
are managed through native `/workspace/skill` endpoints, not silently overwritten
on every startup. A narrow compatibility adapter uses native `add_skill` to add
newly configured names when an existing workspace starts, because the Docker
runtime only seeds an empty skills directory. Existing names and edits are kept.

When `TRACEGLASS_LEGACY_PROJECT` points at the existing reverse project, the
native loader discovers all 45 legacy packages (including the nested
`pentest-tools/src-hunter` and `reverse-engineering/dsl-vm-reverse` packages) and
the Traceglass, REA and behavior-chain-review Skills: **48 effective packages** with the current tree.
Explicit external packages override bundled fallbacks of the same directory
name; collisions between different external roots remain an error.
A staging copy qualifies duplicate frontmatter
names for AgentScope's index while preserving the original names for legacy
resource lookup. The source Skill tree remains read-only.

## Python tools

`api/app.py` passes an `extra_agent_tools` factory to native `create_app`.
The factory resolves the current session's native workspace and returns
`FunctionTool` instances from `tools/analysis.py`. AgentScope builds JSON schemas
and performs the ReAct dispatch, permission check and result streaming.

- `inspect_file`: SHA-256, file size and signature hints.
- `extract_strings`: bounded ASCII strings with offsets and truncation markers.
- `write_report`: create a report under `artifacts/`, reject overwrite and return
  the content hash. This is a write tool and uses native permission confirmation.

Filesystem paths are checked against the workspace. Blocking reads run in a
thread. General filesystem/shell capabilities remain the framework's tools.
A local workspace and path checks are not a sandbox against hostile execution.

## MCP

REA container-kit is configured separately from in-sandbox MCP endpoints:

```sh
export TRACEGLASS_REA_KIT=/Users/yaozh/llms/reverse/dist/rea-container-kit
```

Its image must already be loaded. The adapter uses the kit's `bin/docker-cli`
and immutable image ID in `MANIFEST.json`. It keeps the image unchanged and
adapts the launcher mounts so the workspace is read-only at `/workspace`, with
network disabled, no Docker socket in the analysis container, and a private
state volume for each user/session/workspace. Main and worker agents get separate
REA processes and current-binary state. Connections survive approvals and pauses;
service shutdown closes them, and the next analysis can reopen its binary.
Cache volumes persist. Use `close_binary` when no longer analyzing a binary.

Native `MCPClient` and `MCPTool` supply all 133 discovered tools, permission
checks, events and execution. A middleware attaches them to both leader and worker
toolkits: 12 common binary tools are immediately equipped; other tools use native
`ToolGroup` / `reset_tools` activation. Large individual schemas have separate
groups so ordinary analysis does not pay their context cost. Tool schemas remain
unchanged, and AgentScope persists group selection in its native session state. This is a host-managed sidecar because putting the
kit's Docker launcher in the normal sandbox would require Docker access there.
No custom ReAct runtime, tool gateway protocol or subagent scheduler is added.
REA startup failure leaves the existing reverse/native tools usable and appears
in `/product/runtime` integration status. Reconnection is attempted on a later
analysis setup, after a 30-second cooldown. Startup timeout is 120 seconds;
tool timeout is 600 seconds.

Confirmed with an authored ELF object: 133-tool discovery, `open_binary`,
Ghidra 12.1.4 `binary_overview`, `search_procedures`, `procedure_pseudo_code`,
and `close_binary`. The fixture was never executed. Hopper remains its demo
edition; advertised tools can have host/provider limitations, and this change
does not provide a Windows malware execution environment.

Set `TRACEGLASS_MCP_CONFIG` to a JSON file containing native `MCPClient` records:

```json
[
  {
    "name": "analysis-service",
    "is_stateful": false,
    "mcp_config": {
      "type": "http_mcp",
      "url": "http://127.0.0.1:13337/mcp"
    }
  }
]
```

For stdio, use `is_stateful: true` and a `mcp_config` with
`type: "stdio_mcp"`, `command`, and `args`. Native AgentScope workspace lifecycle
owns connect/close. `enable_tools` and `disable_tools` are native MCPClient fields.
`${ENV_NAME}` placeholders are expanded server-side; missing variables fail
configuration before connection. Keep real credential values out of committed
JSON. Only configure trusted MCP servers and commands.

The legacy profile automatically adds a stateful `reverse` MCP client backed by
the read-only domain modules from `reverse/src/re_agents`. It currently exposes
41 tools, including static analysis, dynamic-report review, Skill resource lookup,
evidence recording and case archiving. The adapter intentionally does not load
the old factory, runtime, API service or scheduler. It receives a workspace path
inside the Traceglass Docker container and never writes to the old project.

If `IDA_ENABLED=true`, the profile also adds an HTTP `idapro` client using
`IDA_MCP_URL`. The Docker workspace must be able to resolve that URL (for a
local Docker Desktop host, `host.docker.internal` is the usual address). IDA
connectivity is optional at startup; a failed IDA service should be surfaced as
unavailable while the static `reverse` client remains usable.

## Planning and subagents

The service attaches native TaskCreate/TaskGet/TaskList/TaskUpdate and leader
TeamCreate/AgentCreate/TeamSay tools. Leaders also get `TodoWrite`, a batch
create/update adapter over the native Task tools. It does not replace the
Task store: returned `task_id` values work with TaskGet and TaskUpdate, and the
native service persists and broadcasts the same task state. Omitted tasks and
fields are preserved; invalid batches do not partially update the plan.

The leader writes the plan first, assigns `owner` to the member's actual name,
sets dependencies through TaskUpdate, and delegates ready work. `agents.py`
overrides the native `default` template so the model can invent specialists
for the current task with common evidence, progress and sandbox requirements.
Three optional shortcuts remain: static-reverse, dynamic-evidence, and verifier.
Workers report their Todo ID, results, sources and blockers through TeamSay;
the leader verifies evidence and marks the shared Todo complete.

`delegation.py` is an AgentScope middleware policy, not an execution engine.
It rejects Create/Invite without an unfinished native task and enforces at most
8 team members excluding the leader. A shared in-process lock covers the count
check and the native mutation, including parallel Create/Invite calls. TeamSay
is not blocked when the roster is full, allowing members to take further work.
The cap reads persisted team membership, so it survives a service restart.
Worker tool restrictions are handled by AgentScope's team role; no custom
recursion manager or step scheduler is involved. Multi-process deployment needs
a shared mutation lock alongside a shared native message bus.

The default templates inherit the leader's native permission context. They
express analysis roles, not hard security partitions. Strong isolation belongs
in the native workspace/sandbox configuration and its acceptance tests.

## Analysis quality

`AnalysisReview` records model-authored behavior coverage in native session state;
`PrepareAnalysisInput` prepares scoped paths and read-only snapshots for local IDA.
See [Analysis quality](ANALYSIS_QUALITY.md) for structural review limits, configuration
and repeatable baseline/candidate evaluations.
