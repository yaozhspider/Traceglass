---
name: key-function-analysis
description: Deeply analyze high-priority functions selected by deterministic ranking or explicitly named by the user. Use when a reverse-engineering or malware-analysis task asks to prioritize, explain, decompile, trace, or recover the logic of key functions in a binary.
---

# Key Function Analysis

Use `rank_key_functions` to seed the queue, except when the user names targets or evidence already identifies a relevant input handler, execution sink, callback, or a function connecting them. These paths take precedence regardless of score. Treat `priority_tier` as an analysis queue, not a verdict:

- `critical` or `high`: perform deep analysis.
- `medium`: perform standard analysis when needed to explain a call chain.
- `low`: retain as triage context; promote user-selected targets and functions on an external-input or high-impact path to deep analysis.

For input-to-execution questions, load `reverse-engineering` and read `references/external-input-execution.md`. Include input origin, transformations, dispatch condition, operation/argument provenance, raw evidence, and gaps for each path. Identify wrappers and implementations through registration tables and callers; a compiler prologue or runtime error string cannot establish an initialization-only role. Unresolved indirect calls and low scores are coverage gaps, not evidence of safety.

For every deep target, collect evidence in this order:

1. `disassemble_function` to establish the real basic blocks, calls, branches, constants, and return paths.
2. `decompile_function` to recover readable control and data flow; compare suspicious or ambiguous blocks with assembly.
3. Use callers/callees exposed by the available analyzer or IDA read-only tools to connect the function to its entry path and side effects.
4. Trace inputs, transformations, security-relevant outputs, and error/cleanup paths. Identify the exact condition that makes each behavior reachable.
5. For crypto, decoding, validation, injection, persistence, or network logic, record parameters, buffers, key material, encodings, and API sequencing. Do not infer behavior from a single API or string.

Report one subsection per deep target with:

- function name, virtual address, rank, score, and priority tier;
- callers and callees that are supported by tool evidence;
- stepwise control-flow and data-flow explanation;
- recovered algorithm or state machine, including unknowns;
- security or malware relevance and confidence;
- evidence addresses/offsets and unresolved questions.

If a user names a function, analyze it deeply even when its deterministic score is low. Never invent a function address; resolve it from tool evidence, including registration tables or call targets when the automatic function list is incomplete. Keep the sample static and offline, and clearly separate observations, hypotheses, and conclusions.
