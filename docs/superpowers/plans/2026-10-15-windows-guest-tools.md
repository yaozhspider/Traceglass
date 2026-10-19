# Windows guest tools implementation plan

**Goal:** Expose the installed Windows analysis tools through AgentScope using the verified ESXi Guest Operations channel.

**Approved design:** The user approved implementing the host-side vSphere adapter and model tools described in this conversation. Keep the existing Docker workspaces disconnected. Guest credentials never enter model arguments, sandbox environment or tool output. Use VMware Tools for file transfer and execution; no inbound guest service is required.

**Execution:** Inline in this side conversation; no subagents, commits, deployment restart, VM reset or execution of user samples. Existing uncommitted work is preserved. New implementation lives under `traceglass/windows`; edits to existing configuration/composition are narrow.

## Interfaces and files

- `windows/config.py`: administrator configuration, explicit account allowlist, certificate pinning, fixed executable paths and execution policy.
- `windows/transport.py`: lazy pyvmomi connection, exact VM identity, credential validation, bounded file transfers, process start/status/termination.
- `windows/guest.py`: standard-library guest runner; fixed CLI operations, durable result/log files, timeout and process-tree cleanup; optional local x64dbg Automate driver.
- `windows/service.py`: persistent scoped jobs, VM lease, immutable staged sample hash, upload/start/status/collect and scoped debugger control.
- `windows/tools.py`: native FunctionTool definitions and sanitized errors. Schemas supply the APIs to the model.
- `config.py`, `api/app.py`, `integrations/tool_policy.py`: opt-in backend composition and searchable optional Windows tool group.
- `docs/WINDOWS_GUEST_TOOLS.md`, example JSON: setup, operating workflow, capability limits and live verification command.
- `tests/unit/test_windows_tools.py`, `tests/integration/test_windows_tools.py`: transport-independent behavior, guest execution and model-facing tool integration.

## Task 1 — configuration, guest runner and scoped jobs

- [x] Write failing tests for opt-in access, rejected path escape, literal subprocess arguments, failed exits, timeout and account/workspace task isolation.
- [x] Run the tests before implementing. Confirm failures are missing feature behavior.
- [x] Implement private admin config with environment credential references, fixed tool profiles, a standalone guest runner and persistent scoped job/lease storage.
- [x] Verify the tests. Guest tests use authored Python fixtures only; no target samples.

## Task 2 — VMware Tools channel and debugger API

- [x] Add transfer URL tests before transport implementation; extend redirect, process and TLS/VM-identity coverage during integration.
- [x] Implement pyvmomi transport using the trusted ESXi certificate, pinned VM ID/name and VMware Tools guest authentication.
- [x] Implement typed x64dbg operations with a local x64dbg Automate plugin/client; readiness must explicitly distinguish installed executable, automation driver and active session. No unrestricted debugger command endpoint.
- [x] Verify transport boundaries with real SDK types, redirect-handler checks and a live ESXi capability probe; pass an authored WASM through upload, execution, status and collection.

## Task 3 — framework wiring, docs and verification

- [x] Write integration tests demonstrating native tool schemas, SearchAnalysisTools/reset_tools discovery, execution policy and evidence capture.
- [x] Register the tools through the existing extra_agent_tools hook only for explicitly authorized accounts. Leave the feature off when configuration is absent.
- [x] Document administrator configuration and restart requirement without embedding credentials. Preserve existing production environment and process.
- [x] Run new tests, affected account/catalog/evidence tests, then the backend test suite once. Inspect the focused diff and report exactly which live capabilities passed.

## Acceptance

The model can discover the new APIs and use typed parameters, while the backend handles ESXi credentials. A Windows task has durable identity, sample hash, status and retrievable results; failures and timeouts remain failures. Other accounts/workspaces cannot inspect or control it. The Windows debugger adapter reports unavailable when its plugin/client is missing, and never equates GUI installation with working automation.

## Verified delivery

- Opt-in native schemas and catalog group, caller-scoped durable jobs, lost-start recovery without replay, bounded no-follow workspace I/O and sample-bound evidence are implemented.
- Full backend suite: 266 tests passed before the final pending-operation recovery extension; affected suites are rerun for the final code.
- Live ESXi/Tools probe and authored empty WASM conversion/collection passed. All nine configured tool paths exist in the guest.
- Installed x64dbg Automate 0.9.2 in a separate guest venv plus official v0.8.1-ghost_fungus plugins. New empty x64 and x32 sessions both opened and closed successfully.
- Real target loading, breakpoints, stepping, memory operations and baseline restoration remain untested. Dynamic execution stays off until a clean administrator-approved baseline is selected.
- Production environment/process and all pre-existing changes are preserved. No user sample was executed and no snapshot was reverted.
