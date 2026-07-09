---
description: Invoke the planner subagent against a spec. Read-only — produces a markdown plan, never writes code.
argument-hint: [path to spec, or blank for latest]
---

Invoke the `planner` subagent.

Spec selection:

- If `$ARGUMENTS` is a path to a markdown file under `docs/specs/`, pass that path.
- Otherwise, find the most recent spec — the file in `docs/specs/` with the highest `NNNN-` prefix (excluding `README.md`).

The planner is read-only. It reads the spec, surveys the codebase, and produces a markdown plan covering: files-to-touch, order-of-operations, risks / open questions, and out-of-scope. The plan should be reviewable in under five minutes.

Surface the planner's output verbatim. Do NOT proceed to test-writing or implementation. The human reviews and approves the plan before the next phase.
