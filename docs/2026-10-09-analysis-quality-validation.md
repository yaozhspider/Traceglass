# Analysis quality validation — 2026-10-09

Implemented in the existing AgentScope runtime: shared analysis policy, the
behavior-chain-review Skill, native AnalysisReview coverage records, same-host IDA
input preparation, and a repeatable replay/static-binary evaluation CLI.

## Verification

- Backend suite: 133 tests passed (only existing upstream deprecation warnings).
- Coverage: blocked/missing/string-only paths reject benign proposals; per-sample
  incremental updates and native state roundtrip preserve unresolved paths. Native
  model integration receives the policy even for an existing custom worker role.
- Input preparation: scoped paths, escape rejection, read-only staged copy with
  matching hash, remote/unconfigured IDA behavior, default native permission ASK.
- Real IDA MCP: prepared the authored fixture.o, session_create returned ready;
  closed only the test session and restored the previous active session. No target
  execution. Evidence: traceglass-data-integrated/rea-validation/ida-stage-smoke.json.
- Live deployment: current workspace reports 48 Skills, including the new Skill;
  the source package bundles 12. Same-host IDA preparation explicitly enabled in
  the local ignored deployment config. Existing reports are unchanged.

## Model evaluations (glm-5.3-flash)

Artifacts under traceglass-data-integrated/evals/:

| Run | Result | Meaning |
|---|---|---|
| 20261009-baseline-v2 | incomplete case 5/5 rubric pass | Old prompt can reject benign when the missing evidence is supplied directly |
| 20261009-candidate-v2 | incomplete case 5/5 rubric pass, review called | New workflow records unresolved paths and produces suspicious conclusions |
| 20261009-controls-v2 | connected execution and display-only pass | Identifies supplied execution chain and does not mistake display text for execution |
| 20261009-admin-v3 | authorized administration 5/5 rubric pass | After guidance clarification, distinguishes execution capability from authorization/intent |
| 20261009-client-binary | invalid/incomplete at 600-second budget | No full binary-analysis pass claimed |

Early candidate runs exposed mixed original-evidence/coverage-ID citations; the
replay schema now constrains references to supplied evidence IDs. Another run
reported unknown for an already established authorized execution chain; the tool
schema and Skill now explicitly state that authorization does not erase capability.
Failed runs remain saved; subsequent successes do not replace them.

A baseline/candidate comparison is saved as 20261009-comparison.json. Both passed
all five supplied-evidence replays; **this is not evidence of improved detection**.
Median replay time was 38.73s baseline versus 135.55s candidate (includes Skill and
coverage calls, small uncontrolled timing sample). Current work trades some latency
for auditable coverage; do not apply the full analysis workflow to simple queries.
Automatic rubrics check structure/labels/references, not complete semantic truth.
Manual reading confirmed preservation of key gaps and separation of capability,
authorization and privilege; some wording/order details still require review.

## Actual binary experiment and remaining work

The isolated static run used extracted client.cp313-win_amd64.pyd,
SHA256 645aee6d186798b8b46f306b6ce759b632769405468301917b9c8b52087fa973.
The model opened it in REA/Ghidra, used real cross-references and obtained pseudocode:

- FUN_180003f00 is the _recv_loop wrapper and calls FUN_180004120.
- FUN_180004120 contains calls to PyRun_StringFlags/PyEval_EvalCode and Cython
  error/source references to secure_comm.client._recv_loop.
- The exact network prefix/constant reconstruction was unfinished when the
  600-second run budget expired. Coverage registration/final structured assessment
  were also unfinished. Saved observable transcript and decompile-*.c are in
  20261009-client-binary; the run is explicitly invalid, not counted as a pass.

This targeted single-agent run was not the full multi-agent unpacking product flow.
It does not independently validate the user's exact exec: trigger. The next analysis
should start from these addresses and recover Cython string initialization/dispatch,
then connect the receiver parameter to the execution argument and review privileges.
The original report's global benign verdict remains unsupported.

## Enforcement boundary

AnalysisReview is a structural domain check, not an automatic malware classifier.
It does not block all native file writes/free-form model replies. A model can omit
it or supply incorrect evidence; the evaluator flags missing review and requires
human evidence review. No claim is made that prompt/Skill changes eliminate all
false negatives. Future badcases must add controls and be replayed before release.
