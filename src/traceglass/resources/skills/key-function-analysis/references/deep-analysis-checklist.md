# Deep Analysis Checklist

Use this checklist for `critical`/`high` functions or explicit user targets.

| Area | Questions to answer |
| --- | --- |
| Entry/exit | Who calls it? What arguments and return values are observable? Which branches terminate or retry? |
| Data flow | Which buffers, globals, strings, and constants enter the function? Where are they copied, decoded, transformed, or compared? |
| Calls | Which callees are reached on each branch? Which imports are capability indicators versus actually reached behavior? |
| State | Does it mutate files, registry/configuration, memory protections, processes, sockets, or global state? Under what guard? |
| Algorithm | Can the transformation be expressed as pseudocode, equations, a state machine, or a protocol sequence? |
| Confidence | Which claims are directly evidenced, which are hypotheses, and what static evidence is still missing? |

Prefer a compact control-flow summary plus an evidence table over a long undifferentiated decompiler dump.
