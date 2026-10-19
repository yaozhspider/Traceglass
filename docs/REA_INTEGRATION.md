# REA 6.3.0 integration

Updated on 2026-10-10 from the deployed 6.1.0 kit. Real MCP discovery returns
**139 tools and six workflows**, compared with 138 tools in 6.1.0 and 133 in the
original 5.0.0 kit. No tools were removed between 6.1.0 and 6.3.0.

## Included

- Pinned `rea-agents@6.3.0` from the official npm package, verified against its
  SHA-512 integrity. `resources/rea-release.json` records the upstream source,
  package integrity and SHA-256 hashes of the unmodified Skill, five references
  and MIT license.
- New `inspect_analysis_view`: read a summary, facet, item or bounded page from
  completed binary-layout or JavaScript application Evidence without repeating
  analysis. Same-session retained IDs and portable inline Evidence are supported.
  The returned view preserves parent identity, digest, coverage and limitations.
- Native MCP discovery supplies every advertised tool to both leader and worker
  integration paths. Image-bound cached schemas now allow lazy registration;
  first actual use starts each agent's independent connection. Twelve common
  tools start enabled; `SearchAnalysisTools` finds
  the others and returns native `reset_tools` activation arguments. The catalog
  is dynamic, not a fixed 133-tool allowlist. Registration does not guarantee
  that every optional provider is installed or that the model calls every tool.
- `ListReaWorkflows` and `GetReaWorkflow` expose six native MCP prompts:
  `investigate_feature`, `compare_application_versions`, `verify_reconstruction`,
  `trace_crash`, `audit_residual_unknowns`, `prepare_bounded_process_capture`.
  Fetching guidance does not execute analysis.
- The official `reverse-engineer-anything` Skill is available to main and worker
  workspaces. During this deployment, three existing pristine 6.1.0 copies were
  backed up and updated after checking every old release file hash and rejecting
  extra files. User-edited copies must be preserved. Normal workspace startup
  still installs missing skills only; it does not overwrite existing packages.
- Analysis results, terminal failures and REA evidence/unknown revisions are
  archived in `rea-evidence.sqlite3`, outside the analysis sandbox. Native
  result metadata carries archive receipts. `QueryAnalysisEvidence` and
  `ReadAnalysisEvidence` expose ownership-scoped originals; the REA names remain
  compatibility aliases. Truncated/failed records cannot pass strict reference
  checks. Native Read/reverse paths are hashed inside the authorized workspace;
  IDA follow-ups bind only explicit verified session handles.

AgentScope owns the execution loop, permission gates, teams, schemas and streaming.
MCP prompts use a narrow compatibility boundary around the client's `_session`
in AgentScope 2.0.6; integration tests cover that boundary.

## Connection lifecycle (2026-10-14)

Generate matching metadata without analyzing a target:

```sh
.venv/bin/python scripts/cache_rea_tools.py --kit traceglass-data/rea-kits/6.3.0-pwntools
```

`TOOLS.json` is tied to the immutable kit manifest and schema hashes. A mismatch
does not register stale definitions; `ConnectRea` explicitly refreshes metadata.
Registration/activation does not imply a connected engine. Resident connections
are capped per process (default 10, `TRACEGLASS_REA_MAX_CONNECTIONS`); text workers
and archive readers use no slot. A new connection queues for up to 30 seconds
(`TRACEGLASS_REA_QUEUE_WAIT_SECONDS`, finite and positive) before reporting capacity
exhaustion. This waits for admission only; it never replays a tool call.
`GetReaStatus` reads aggregate occupied/available/queued/active-call counts and
whether the caller holds a connection, without starting a container. It does not
expose other users' session or workspace identities. Capacity errors include these
counts and distinguish queue timeout from a failed MCP/Docker connection. A caller
without a connection cannot free other agents' slots with `ReleaseReaSession`.
Idle connections with no active calls are reaped after 600 seconds by default.
Pauses and approval waits continue that idle timer; set
`TRACEGLASS_REA_IDLE_SECONDS=0` to retain them until explicit release/shutdown.
The runtime prompt explains the policy, and expired handles return a reopen
error. `ReleaseReaSession` releases only the caller's idle connection. A normally
completed reply automatically releases its own connection if no tool call is
active. Interrupted, failed or iteration-limited replies retain the idle policy.
Leader acceptance also releases idle member connections. Neither cleanup path
terminates an active tool call. Capacity status refreshes as slots become free.
Owner tasks close MCP scopes; schema-bound proxies and workflows rebuild around
the new connection. Archived IDs survive; live handles and current binary do not.
No failed or expired call is automatically replayed.

The budget is process-local. Several API processes need a shared quota and
native-team mutation lock before claiming a global limit.

## Build and activate

With the original engine kit image already loaded, from the repository root:

```sh
.venv/bin/python scripts/build_rea_image.py \
  --base-kit /Users/yaozh/llms/reverse/dist/rea-container-kit \
  --output traceglass-data/rea-kits/6.3.0-pwntools
```

The builder verifies the base image ID, package integrity, built version and
installed pwntools provider versions. `containers/rea/pwntools-requirements.txt`
pins pwntools 4.15.0, pyelftools 0.33, Unicorn 2.1.2 and pefile 2024.8.26.
The updated deployment retains its existing Capstone 6.0.0a11. These live in an
isolated `/opt/rea-pwntools` virtual environment; `REA_PWNTOOLS_PYTHON` points
to its Python executable inside the image. The virtual environment also leads
the container's PATH, so ordinary `python3` uses the same analysis libraries.
No host Python install is needed.
The archive name follows the release lock. Hash-guarded Rosetta/Hopper patches
are reapplied; both guarded upstream module hashes remain unchanged in 6.3.0.
A separate kit manifest pins the resulting immutable image ID. The original
kit, the 6.1.0 image and the previous 6.3.0 image are retained.

After smoke checks, set `TRACEGLASS_REA_KIT` to the absolute generated kit path
and restart the backend. A process environment override takes precedence over
`.env`; update the launch environment as well. The current deployment uses:

- Kit: `/Users/yaozh/llms/traceglass/traceglass-data/rea-kits/6.3.0-novmp-20261015`
- Backend: `http://127.0.0.1:8002`
- Frontend: `http://127.0.0.1:5177`
- Existing account data: `traceglass-data-accounts`

## Verification and limits

Verified on 2026-10-10:

- 168 Python tests pass (78 existing dependency deprecation warnings).
- Actual old/new container discovery: 138 → 139 tools; the sole added tool is
  `inspect_analysis_view`. Six live MCP workflows are available.
- Real Ghidra 12.1.4 smoke: open a benign ELF object, overview, search `main`,
  decompile and close. Three records were durably archived and read back.
- Both leader and worker middleware/toolkit paths discover and activate the new
  tool against real MCP metadata. This check does not call a model.
- Real `analyze_javascript_application` followed by `inspect_analysis_view`
  reads one module from retained evidence and returns a continuation offset.
  The JavaScript fixture is parsed as text, never executed.
- The pwntools-enabled image fixes the earlier `inspect_binary_layout`
  `provider_unavailable` failure. Real offline ELF analysis verifies the artifact
  hash and `.text` section; `inspect_analysis_view` returns the exact requested
  sections, parent Evidence ID and total count. Ghidra still passes in the same
  image. Five evidence records are durably archived and read back.
- The provider installation passes `pip check`; the builder imports and checks
  all three pinned distributions as the unprivileged runtime user, without network.
  Fifteen targeted REA integration/unit/contract tests pass after this change.

Local verification reports and deployment backups are under the ignored
`traceglass-data/rea-kits/6.3.0/` and `6.3.0-pwntools/` directories. Repeat the
Ghidra and pwntools smoke with a benign x86-64 ELF fixture:

```sh
.venv/bin/python scripts/verify_rea_integration.py \
  --kit traceglass-data/rea-kits/6.3.0-pwntools \
  --workspace traceglass-data-integrated/rea-validation --sample fixture.o \
  --workspace-id pwntools-validation \
  --archive traceglass-data/rea-kits/6.3.0-pwntools/validation/evidence.sqlite3 \
  --output traceglass-data/rea-kits/6.3.0-pwntools/validation/ghidra-layout.json \
  --check-layout
```

Hopper is still the inherited demo edition and was not acceptance-tested in
this upgrade. Optional Android, browser, platform/runtime and managed providers
may require additional dependencies. REA has no network and only a read-only
workspace mount; this does not provide a Windows execution sandbox.

Archived outputs do not restore engine handles or the live REA ledger. After a
restart agents can read saved evidence but must reopen samples. Unknown records
are observed revision history. Automatic ledger restoration is not implemented.
This release upgrade does not establish improved malware detection accuracy.

## Rollback

To undo only the pwntools installation, select `traceglass-data/rea-kits/6.3.0`;
the older `traceglass-data/rea-kits/6.1.0` also remains available. Set the path in both the launch environment and `.env`,
then restart the backend. Preserve account databases and analysis files. The
original 5.0.0 engine kit also remains at
`/Users/yaozh/llms/reverse/dist/rea-container-kit`.

Historical source baseline: commit `2b1f301`, tag
`checkpoint-before-rea-6.1.0-20261009`. Use a separate checkout when inspecting
that baseline; do not reset a running checkout or delete analysis data.
