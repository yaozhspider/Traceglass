---
name: traceglass-analysis
description: Evidence-backed static sample analysis and review of supplied behavior logs.
---

Record sample identity with the connected reverse MCP's `hash_file` and
`identify_file`, or `inspect_file` when using the minimal local profile. Use the
actual tool names and schemas shown in the current toolkit. Sample paths for
reverse tools are relative to `/workspace/samples`. Select tools according to
the user's question, and read existing Skill resources with `read_skill_resource`
when they refer to sibling paths such as `../field-journal`.
Do not execute untrusted samples in the local workspace. Dynamic conclusions
require supplied logs or the explicitly configured Docker workspace. Native
Bash/Read/Write run in that workspace when the Docker backend is enabled.
Keep experiment files in the case directory reported by `analysis_case_info`;
record commands, outputs and limitations before making dynamic claims.

For complex tasks the leader first reads TaskList and writes its plan with
TodoWrite. Reuse returned task_id values; assign owners by actual member name
and manage dependencies through native TaskUpdate. Delegate independent ready
tasks through native TeamCreate/AgentCreate, normally using the dynamic default
template. A team has at most 8 workers excluding its leader; reuse existing
members through TeamSay. Workers return the Todo ID, evidence, artifacts and
blockers through TeamSay. The leader checks evidence before marking the shared
Todo complete. Framework session state remains the source of truth.

Save reports with workspace Write. Include hashes, observed facts, sources and
offsets, hypotheses, contradictory evidence, limitations, and output paths.
Never treat a planning entry or another agent's assertion as verified evidence.
