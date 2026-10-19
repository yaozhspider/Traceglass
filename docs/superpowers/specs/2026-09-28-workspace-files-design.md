# Workspace and file workflow

> Historical feature design. Current implementation: [system design](../../DESIGN.md), [data model](../../design/DATA_MODEL.md), and [API and frontend](../../design/API_AND_FRONTEND.md).

The user explicitly requested implementation using the original reverse product
as the reference: workspaces, uploads, selecting existing samples, and files.
This extends the current AgentScope workbench without replacing its runtime.

## User flow

1. A workspace home lists existing workspaces and creates named workspaces.
2. Inside a workspace, the sidebar contains workspace switching, uploaded
   samples with file size/hash, and the workspace's analysis conversations.
3. Local file upload reports progress/errors, persists the bytes, and selects
   the uploaded sample. Existing samples are selectable before analysis.
4. Selecting another sample from an existing conversation starts a fresh
   analysis. The submitted sample context includes real paths and SHA-256.
5. A file panel lists workspace directories and generated reports, opens a
   bounded text/hex preview, and downloads original files. HTML is displayed
   as text; uploaded files are never executed by upload/preview.
6. The conversation retains live Todo, tool details, approvals, and expandable
   sub-Agent execution records. Workspaces remain accessible without a model.

## Boundaries

AgentScope owns sessions, model execution, permissions, Team/Task, sandbox
lifecycles and workspace backends. Product metadata only describes workspaces,
samples and associations. New workspace IDs must map to distinct host paths:
the native 2.0.6 manager otherwise separates containers while sharing the
same agent bind mount. Preserve pre-existing session paths/history.

Uploads have explicit size limits, generated storage names, content hashes,
and atomic publication. File routes are restricted to the workspace and must
reject traversal and symlinks escaping its root. Errors do not turn into empty
lists or successful sample cards. Do not modify the original reverse project.

## Acceptance

Create workspace → upload harmless fixture → select it → send analysis with
its exact path/hash → list/open/download file → reload and recover selection
and history. Verify two new workspaces cannot read one another's files, and
that switching workspace or sample cannot retain a stale attachment. Cover
size limits, filename/path handling, interrupted uploads, binary/text previews,
and no-data states. Keep controls usable on narrow screens.
