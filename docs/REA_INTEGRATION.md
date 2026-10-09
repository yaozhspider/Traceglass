# REA 6.1.0 integration

The baseline is commit `2b1f301`, tagged
`checkpoint-before-rea-6.1.0-20261009`. Upgrade work is on
`codex/rea-6.1-integration`. No credentials or user analysis data are committed.

## Included

- Pinned `rea-agents@6.1.0` from the official npm package, verified against its
  SHA-512 integrity. `resources/rea-release.json` records the upstream source,
  package integrity and SHA-256 hashes of the unmodified Skill, five references
  and MIT license.
- 138 discovered MCP tools; 12 common tools start enabled and remaining groups
  activate through AgentScope `reset_tools`. These include upstream composed
  analysis services, provider adapters and evidence operations. Discovery is
  not a guarantee that every optional backend is available.
- `ListReaWorkflows` and `GetReaWorkflow` expose six native MCP prompts from the
  same live connection: `investigate_feature`, `compare_application_versions`,
  `verify_reconstruction`, `trace_crash`, `audit_residual_unknowns`, and
  `prepare_bounded_process_capture`. Fetching guidance never executes a tool.
- The official `reverse-engineer-anything` Skill is installed into both leader
  and worker workspaces. Existing user-edited Skills are not overwritten.
- Successful evidence-bearing tool results and observed unknown revisions are
  archived outside the analysis sandbox in `rea-evidence.sqlite3`. Copies retain
  raw JSON, original identifiers, sample digest when provided, source session,
  engine, call identity, confidence and limitations. Each archive receipt travels
  in native tool-result metadata for live display and persisted history, and in
  final tool content for the model.
- `QueryReaEvidence` and `ReadReaEvidence` expose scoped, paged reads across
  members of the same workspace. Workbench **证据 → 共享证据** displays records,
  origin, limitations and full raw-result download. HTTP reads verify workspace
  ownership. Reads do not start an engine.

The execution loop, permissions, task/team lifecycle, schemas and streaming
remain AgentScope's. No second agent orchestrator is introduced. The only
private SDK compatibility boundary added is the MCP client's `_session`, needed
for prompts in AgentScope 2.0.6; integration tests cover it.

## Build and activate

From the repository root, with the original engine kit image already loaded:

```sh
python3 scripts/build_rea_image.py \
  --base-kit /Users/yaozh/llms/reverse/dist/rea-container-kit \
  --output traceglass-data/rea-kits/6.1.0
```

The script verifies the original image ID, package integrity and built version,
then writes a new immutable image ID to a separate kit manifest. The original
image and kit are retained. Hash-guarded container compatibility patches are
reapplied after the npm upgrade; their upstream module hashes still match.

To run an isolated preview without changing `.env` or interrupting the old API:

```sh
TRACEGLASS_DATA_DIR="$PWD/traceglass-data-rea6" \
TRACEGLASS_REA_KIT="$PWD/traceglass-data/rea-kits/6.1.0" \
  .venv/bin/python -m uvicorn traceglass.api.app:create_app \
  --factory --host 127.0.0.1 --port 8001
```

In a separate terminal, from `web/`:

```sh
TRACEGLASS_API_TARGET=http://127.0.0.1:8001 \
  npm run dev -- --host 127.0.0.1 --port 5176 --strictPort
```

Open <http://127.0.0.1:5176/>. Existing model configuration is inherited;
workspaces and conversations use the isolated data directory. Old histories
are not copied. The original 5175/8000 services were not restarted during this
upgrade because analyses were running there.

## Validation and limits

Verified on 2026-10-09:

- 140 Python tests, 45 web tests and the production web build pass.
- Native leader/worker execution reaches workflows and shared archive tools;
  permission handling, separate MCP state, durable receipts, ownership and
  restart reads are covered by tests using a real stdio MCP fixture.
- Real container discovery returns 138 tools and six prompts. A benign ELF was
  opened, overviewed, searched, decompiled and closed with Ghidra 12.1.4. Three
  evidence records were archived and read back after restarting the preview API.
  The workbench displays these as `integration-validation`, not a model analysis.
- The ELF SHA-256 is
  `dec4475c8204b025231dfca1e0f26036f87fbe265cbb5d1f6a19b3314425a542`.
  It was never executed. The real-container report is in the ignored preview
  data directory at `traceglass-data-rea6/rea-verification.json`.

Repeat the real smoke with `scripts/verify_rea_integration.py --help` and a
dedicated workspace containing a benign fixture. It uses real MCP tools and the
archive middleware, without calling a model. Its successful path is Ghidra;
Hopper remains the inherited demo edition and was not acceptance-tested in this
upgrade. Optional Android, browser, platform/runtime and managed-code providers
may require dependencies unavailable in the inherited image. REA has no network
and only a read-only workspace mount; this is not a Windows execution sandbox.

The archive preserves outputs, not engine handles or a restored REA ledger.
After restart, agents can read copies but must reopen samples for new analysis.
Unknown entries are observed revision history, not a global current-state
registry. Existing upstream export/import tools remain available on demand;
automatic native snapshot restoration is not implemented. This upgrade has not
yet been validated for detection quality on the user's backdoor badcase.

## Rollback

The old services and original kit remain available. For a fresh baseline
checkout, use the checkpoint tag in a separate directory; do not reset a running
checkout or delete analysis data. To select the original backend on the current
code, set `TRACEGLASS_REA_KIT` to
`/Users/yaozh/llms/reverse/dist/rea-container-kit` when starting a service. Prompt
discovery gracefully degrades if the server does not advertise it. The upgrade
does not migrate or overwrite the original data directory.
