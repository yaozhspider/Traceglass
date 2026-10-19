# Analysis quality and badcase evolution

## What changes

The shared analysis policy requires external-input behavior coverage and an explicit
scope before a benign conclusion. It is supplied by native AgentScope middleware
for existing leaders/workers as well as new templates. Only exact shipped leader
prompts are migrated; custom prompts, limits and roles remain intact.

The new `behavior-chain-review` Skill provides the actual workflow, Python/Cython
execution sinks, authentication/authorization distinctions, evidence requirements
and fallback steps. It is a separately named package so startup can install it
without overwriting previously edited Skills. Legacy Skill sources remain read-only.

`AnalysisReview` is a native ToolBase. Its sample-keyed, incremental records live in
AgentState.middle_context, separate from Todo and preserved by native serialization
and context compression. Unresolved paths are summarized in model instructions.
Each session owns its records: workers report evidence through TeamSay; the leader
must review and enter the merged paths. It does not mark Todo complete for the model.

The review checks declared coverage, evidence methods, scope and contradictions.
A benign proposal fails with blocked/missing paths, only strings/import evidence,
or declared remote execution. A failed review returns gaps and recommends unknown.
It is **not semantic proof** and cannot establish that citations or addresses are true.
It does not intercept every natural-language answer or block native Write/Bash; a
model can still skip it. Regression runs explicitly flag a missing review call.
A human or independent model must inspect evidence correspondence and the final text.

## IDA workspace inputs

Before opening an analyzer, use `PrepareAnalysisInput(path=...)`. It validates a
workspace path, computes SHA-256 and returns REA's `/workspace/...` path. With
`TRACEGLASS_IDA_LOCAL_FILES=true`, it also stages a read-only snapshot outside the
agent-writable workspace and returns an IDA host path. Enable this only when IDA
MCP and the Traceglass API share the same host/filesystem. Remote IDA needs explicit
file transfer; the tool never guesses that a remote filesystem is local.

The staged input limit is 512 MiB. Each copy has an isolated directory; the original
is never executed or modified. IDB/cache products may live beside the copy, and
staged directories persist for review. Remove an old directory only after its IDA
session has been closed. In default permission mode the preparation tool follows
native permission checks; this adapter never approves a tool on the user's behalf.

If preparation or IDA loading fails, record a blocked path and use REA Ghidra on
`rea_file_path` or another verified analyzer. The framework does not invent a
successful result, reroute an approved command invisibly, or infer a benign verdict.

## Repeatable evaluation

Configure the existing model environment, then run from the project root:

```sh
.venv/bin/python -m traceglass.evaluation --variant baseline --repetitions 5 \
  --output traceglass-data-integrated/evals/baseline-run-001
.venv/bin/python -m traceglass.evaluation --variant candidate --repetitions 5 \
  --output traceglass-data-integrated/evals/candidate-run-001
```

Each run directory must be new. `--case NAME` selects cases, `--concurrency 2`
bounds simultaneous model calls, and `--timeout` / `--max-iters` bound each run.
The baseline uses the frozen shipped v3 policy without the new Skill/review tool.
The candidate uses the current policy, Skill and native AnalysisReview.

The runner uses AgentScope's Agent/ReAct and structured output, not a separate
execution engine. Portable fixtures contain facts and expected rubric labels;
**expected answers, historical labels and user trigger claims are not given to the
model**. Replay runs have no shell/network/sample-execution tools. Assessments,
observable calls/results (not private reasoning), model parameters, policy/Skill/
case hashes, latency, coverage and scoring are saved. Keep output directories local:
model tool outputs and analysis artifacts can contain sensitive sample data.

Fixture types:

- `pyinstaller-incomplete`: redacted facts from the real failed analysis; correct
  outcome is unknown/suspicious because the execution chain was not recovered.
- `connected-execution`: an authored source chain with a different trigger; should
  identify remote execution without assuming administrator privileges.
- `display-only`: similar strings, but verified bounded display handling; tests false positives.
- `authorized-admin`: execution exists with explicit administrative authorization;
  tests separation of technical capability and malicious intent.

Scores check labels, required evidence IDs, gaps, next steps and candidate review
usage. Review dataflow, authorization and privilege prose manually; a green score
is not a complete semantic evaluation. Invalid model/tool/timeout runs are separate
from failed quality rubrics. Exit status is nonzero for either kind of failure.

For a real binary use the separate static mode (requires configured REA kit):

```sh
.venv/bin/python -m traceglass.evaluation --binary /absolute/path/client.pyd \
  --max-iters 35 --timeout 600 \
  --output traceglass-data-integrated/evals/client-static-001
```

The binary is copied to a temporary read-only input and gets an isolated native REA
connection. Only an allowlist of static REA tools is exposed: no Bash, target imports,
dynamic execution, original workspace mutation or live user-session continuation.
Native default permissions remain active, with scoped allow rules for static REA
operations; a guard denies tools outside the static allowlist for main and workers.
The final evidence/addresses require independent review; binary runs receive no
automatic “passed malware analysis” label. This targeted single-agent analysis does
not measure the whole multi-agent product or full PyInstaller extraction pipeline.

For independent graded fixtures, use `--binary-manifest` after building the
authored object fixtures; `--product` selects the actual native product/task/team
composition. These results remain separate from replay and ungraded binary mode.
See [Evaluation](EVALUATION.md) for manifests, cost metrics and known live limits.

## Adding a badcase

1. Preserve sample and member hashes, user question, model/config, policy and Skill
   versions, tool failures, observable evidence and original report. Do not overwrite
   the failed report. Keep credentials/private reasoning out of portable fixtures.
2. Identify the first broken boundary: planning, tool access, evidence acquisition,
   dataflow reasoning, worker reporting, leader synthesis, or conclusion scope.
3. Add an `evals/cases/*.json` fixture with `id`, `sample_sha256`, `task`, evidence IDs
   and a rubric. Separate independently verified facts from user claims/hypotheses.
4. Add both a positive case and nearby negative/dual-use controls; hold out variants
   to avoid optimizing only for a prefix or API name.
5. Change the smallest responsible layer, run baseline and candidate with equal
   budgets and at least five repetitions for critical cases. Inspect all outcomes.
6. Compare key-chain discovery, citation correctness, false positives, unsupported
   benign claims, runtime and cost/usage where available. Do not claim improvement
   when the baseline also passes, or when a replay merely supplied the missing clue.
7. Re-run real binary extraction/analysis separately before a release-quality claim.
   Preserve the run folder and a human-reviewed acceptance note with remaining gaps.

Compare saved runs without another model call:

```sh
.venv/bin/python -m traceglass.evaluation.compare \
  traceglass-data-integrated/evals/baseline-run-001 \
  traceglass-data-integrated/evals/candidate-run-001
```

The comparison keeps invalid and ungraded binary runs visible, and warns about
case/model/budget/repetition mismatches. It does not infer statistical significance.
