# Analysis quality implementation plan

Approved scope: the user's request to implement the badcase evolution proposal.

Goal: prevent incomplete analysis being represented as a verified benign verdict,
repair local IDA input preparation, and provide reproducible quality regression runs.
Architecture: AgentScope native tools/middleware/state; domain evidence checks are
advisory and structural, not an automatic malware classifier or replacement runtime.
Tech: Python, Pydantic, AgentScope 2.0.6, pytest.

- [x] Preserve the observed failure as a redacted case with hash, original outcome,
  tool failures and locations. No hidden reasoning, credentials or original binary
  in the portable fixture. Label the claimed trigger as not yet independently verified.
- [x] Add tests for missing/blocked coverage, partial updates, separate sample hashes,
  restart persistence, benign controls, and tool permission paths.
- [x] Implement AnalysisReview using native middle_context, preserving coverage across
  compression. Model-authored evidence is not automatically factual verification.
- [x] Add a short common analysis policy through native system middleware (also applies
  to existing workers); migrate only the exact shipped leader prompt.
- [x] Add a distinct behavior-chain-review Skill with external-input and Cython guidance.
  Existing user-edited Skills remain intact. Include fallback and review instructions.
- [x] Implement PrepareAnalysisInput: scope paths to workspace, hash and stage copies
  for same-host IDA only when explicitly configured; otherwise return REA path and
  an actionable limitation. Never execute the sample or rewrite permission decisions.
- [x] Add a CLI regression harness with fresh-context native AgentScope runs, per-case
  rubric, repetitions, baseline/candidate policy hashes, model config (no secrets),
  transcript, outcome and latency. Report invalid runs separately from quality failures.
- [x] Run unit/integration tests and real model regression scenarios; inspect outputs.
  Include both safe controls and uncertain cases. A fixture replay does not count as
  an end-to-end binary reverse-engineering success.
- [x] Add an optional actual-binary end-to-end run using an isolated case/workspace,
  read-only source artifact copying, no sample execution or user's active session writes.
- [x] Verify deployment status; restart only with no active analysis. Document remaining
  limitations and exact commands for adding future cases and comparing versions.

Keep original reverse project read-only; preserve dirty project changes, existing
reports and sessions. Do not commit or push. Continue in the current checkout where
all previously authorized integration work lives.

Validation evidence and limitations: docs/2026-10-09-analysis-quality-validation.md. The optional real-binary run was attempted but did not finish within its budget; it remains a documented invalid run, not a detection pass.
