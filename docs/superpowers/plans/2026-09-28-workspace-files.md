# Workspace Files Implementation Plan

**Goal:** Restore the original product's workspace → sample → analysis workflow.
**Architecture:** Product metadata and file APIs compose native AgentScope
storage and workspace backends; React components consume those APIs.
**Spec:** `docs/superpowers/specs/2026-09-28-workspace-files-design.md`

## Tasks

- [x] Backend: persistent workspace/sample metadata; create/list/detail/session
  binding; scoped native workspace path adapter; bounded upload/list/preview/
  download routes. Keep API composition in `api/app.py` small.
- [x] Frontend: typed workspace API, workspace home, sample upload/selection,
  navigation and file browser/preview components. Separate presentation from
  API and orchestration. Reuse existing session feeds.
- [x] Integration: WorkbenchShell selects workspace/session/sample, sends exact
  sample context, creates a fresh analysis when a sample changes, and exposes
  files and existing message inspection through accessible controls.
- [x] Verify: meaningful API/path/isolation tests, frontend projection tests,
  production build, and browser create/upload/select/preview/download/reload.
- [x] Finish existing Todo/delegation validation and native wakeup-race repair;
  preserve existing user work before reloading the development service.

## Verification result (2026-09-28)

- Python: 79 tests passed; compileall and git diff --check passed.
- Web: 31 tests passed; production TypeScript/Vite build passed.
- Browser: create workspace, two real uploads, auto-selection, new analysis on
  sample switch, UTF-8 and 4 KiB hex preview, original download byte comparison,
  reload, and 390 px responsive file-panel access verified.
- Real model: glm-5.3-flash + Docker + reverse MCP + Skill. The UI-created session
  b01ec8f6af27466faeb15e8beed7079b produced three Todos, two dynamic default
  workers, real hash/type/metadata calls, TeamSay reports, independent leader
  verification and three completed tasks. Sample SHA-256 matched upload bytes.
- Native permission requests were inspected and approved only for the specific
  harmless fixture hash check and workspace evidence writes, not blanket rules.
- IDA port 8745 was offline. Verified optional-MCP degradation with real Docker
  and static reverse calls; no successful IDA analysis is claimed in this run.
- Setup/runtime display and cancellation are native registry/dispatcher
  projections. Child confirmation states are visible while cards are folded.

Local evidence: traceglass-data-integrated/workspace-live-verification-success.json
(ignored runtime data, includes persisted native histories and tool outcomes).
