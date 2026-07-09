---
description: Invoke the reviewer subagent on the current branch's diff. Independent review — does not see implementation reasoning.
argument-hint: [<base>..<head> or blank for HEAD vs merge-base with main]
---

Invoke the `reviewer` subagent.

Diff selection:

- If `$ARGUMENTS` matches `<ref>..<ref>`, use that range.
- Otherwise, use `$(git merge-base HEAD main)..HEAD`.

Spec selection:

- The most recent spec under `docs/specs/` (highest `NNNN-` prefix, excluding `README.md`), unless a different one is referenced in recent commit messages.

The reviewer is independent — it has not seen the implementation reasoning. It checks: spec match, test quality (runs them, flags tautologies), edge cases, side effects, don't-touch zones, naming + docstrings, file size, public-repo hygiene.

Surface its findings verbatim. Do NOT argue with or rationalize away its calls — if it says "needs to be redone," that's a real signal. The human adjudicates.
