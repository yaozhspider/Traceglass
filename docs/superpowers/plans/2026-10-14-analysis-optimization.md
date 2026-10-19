# Traceglass analysis optimization implementation plan

> Execute inline using superpowers:executing-plans with test-first changes and verification checkpoints.

**Goal:** Implement the approved optimization and vulnerability-analysis design and bundle unpacking.
**Architecture:** Keep native AgentScope Task/Team/runtime and isolated REA sessions. Extend existing evidence storage, review and evaluation rather than introducing a parallel subsystem.
**Spec:** /Users/yaozh/.claude/plans/calm-juggling-tome.md (2026-10-14 revision)
**Tech stack:** Python 3.12, AgentScope 2.0.6, Pydantic, SQLite, pytest.

## Constraints

- Work against the user's current checkout: account/catalog integration is uncommitted and absent from HEAD. Preserve those changes; no blanket staging or checkpoint commit.
- No deterministic pre-analysis, dossier or shared mutable REA binary session.
- Keep legacy review and edited prompts/Skills compatible; review_passed remains structural.
- Dynamic execution requires authorized sandbox MCP; never run unknown samples in host tests.

## Tasks and gates

- [x] 0. Record file hashes and full regression baseline (178 tests passing; existing upstream deprecation warnings).
- [x] 1. Bundle unpacking with references/source hashes and Traceglass sandbox adapter. Verify native discovery, workspace synchronization and resource reads, preserving edited packages.
- [x] 2. Shared verdict/vulnerability contract; extend review paths and detailed exploit evidence. Test unknown reachability, confirmed-static/unknown-exploitability, overclaims, negative scoped findings and legacy saved state. Add vulnerability Skill and replay fixtures; update Assessment/rubric and evaluation Skill routing.
- [x] 3. Versioned core/policy assembly and migration at local/account access; keep frozen historical defaults and edited roles. Test repeated upgrade, personal IDs, owned workers, old policies and custom prompts.
- [x] 4. Default-single delegation, specific native task binding and dependency/owner checks under existing lock. Exercise real AgentCreate/Invite with concurrency and failed creation. Unify catalog guidance and compatibility alias; test search/activation/fallback paths.
- [x] 5. Extend EvidenceStore with bounded complete call records, source/hash/status metadata and scoped lookup. Archive final tool responses once, preserve streaming metadata and failures. Add independent strict reference validation and compatible replay mode; test wrong tenant/sample, altered/truncated data, unknown IDs, failure and restart.
- [x] 6. REA owner-task release, lazy schema-backed connections, active connection quota and safe idle cleanup. Preserve permissions/MCP semantics; rebuild connection-bound tools/workflows and evidence identity. Test zero-start text worker, concurrent startup, cancellation, quota, active calls, isolated binary state and stale handle rejection.
- [x] 7. Fresh verifier context and scoped original-evidence dispatch. Test that preexisting member history cannot impersonate independent verification; keep optional delegation.
- [x] 8. Real static source fixtures/manifests with independent answers, goal-aware runner, product orchestration benchmark and comparable cost/quality metrics. Keep replay/static/product/dynamic results separate; report unavailable live environments honestly.
- [x] 9. Full regression, frontend checks where relevant, package resource verification, implementation/scope audit and final documentation. No completion claim until every approved requirement has authoritative evidence.

For each behavioral task: add a failing consumer-boundary test, run it, implement, then run focused regression. Re-run the full suite after related batches. Preserve live-evaluation limitations separately from local automated verification.

## Verification outcome

Implementation and bounded local/live checks are complete; this does not certify analysis accuracy or cost improvement. See [validation and remaining external limits](2026-10-14-analysis-optimization-validation.md). The live static run timed out at 180 seconds, no live product quality or authorized dynamic execution is claimed, and repeated paired/held-out benchmarks remain future quality acceptance work.
