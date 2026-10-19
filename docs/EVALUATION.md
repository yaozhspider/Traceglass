# Evaluation

Traceglass evaluates three scopes separately: evidence replay, real static analysis and product orchestration. A replay case carries supplied evidence and a schema-bound rubric. Static cases use evaluator-only manifests (`evals/static` plus generated local output), source/build hashes and independent required-fact patterns. Rubrics never enter the agent workspace or static request.

Build the synthetic Linux x86_64 object fixtures into ignored output with:

```bash
.venv/bin/python scripts/build_static_fixtures.py --output traceglass-data/eval-fixtures
```

The builder records source and object SHA-256 values, compiler/version, flags and the object-only command. It refuses to overwrite output. Fixtures are never linked or run. `--binary-manifest` loads and verifies one manifest; `--binary` intentionally has no rubric and is reported `ungraded`.

Candidate static runs expose only REA static tools, analysis evidence query/read, `AnalysisReview`, skills and native review/task tools. `EvidenceLedgerMiddleware` captures complete tool-call records with sample binding; strict `AnalysisReview` references must validate before static scoring. Replay keeps the explicit compatible reference mode for historical text IDs. Product evaluation (`--product`) uses Traceglass's actual `create_app`, native task/team/session storage and permission middleware in a fresh local app, while `StaticEvaluationGuard` filters execution tools and preserves static REA tools. Product orchestration is reported separately and cannot claim delegation quality from standalone runs.

Each record reports `completed`, `invalid`, `timeout` and `ungraded` independently. Scores include verdict labels, required-fact recall, unsupported citation count/rate, and a semantic-review limitation. The final verdict must match its strict successful Review; native product grading uses only the final answer after the last tool boundary, including any retraction. Telemetry records native API response usage outside message history, including compression and API attempts, alongside model/tool calls, errors, repeated calls, REA starts/occupancy, prompt/schema hashes and code/Skill/image fingerprints. Unreported provider-internal retries and usage remain unavailable. `evaluation.compare` checks model, parameters, budgets, cache condition, sample, schema and retry settings before giving descriptive comparisons.

A configured live run on 2026-10-14 reached the real REA container and static tools but timed out at 120 seconds before a final assessment; no target executed. A replay run completed after 66.55 seconds with a structural pass, but the result still requires human evidence correspondence review. These are environment observations, not semantic proof.

The final 180-second bounded static rerun also timed out: 181.28 seconds wall time,
16 executed tools (15 successful, one error), one REA start, 180.93 connection
seconds and 226,973 observed tokens. Trace:
`traceglass-data/eval-live-static-20261014-final/`. No final rubric score exists.
This is not a controlled cost comparison with earlier runs. Automated product
orchestration passes with a scripted provider; live product quality and authorized
dynamic reproduction remain unverified. See the
[implementation validation](superpowers/plans/2026-10-14-analysis-optimization-validation.md).
