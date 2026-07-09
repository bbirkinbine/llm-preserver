---
description: Invoke the reviewer-adversarial subagent on the current branch's diff. Argues against the change rather than for it. Pair with /review for A/B comparison.
argument-hint: [<base>..<head> or blank for HEAD vs merge-base with main]
---

Invoke the `reviewer-adversarial` subagent.

Diff selection:

- If `$ARGUMENTS` matches `<ref>..<ref>`, use that range.
- Otherwise, use `$(git merge-base HEAD main)..HEAD`.

Spec selection:

- The most recent spec under `docs/specs/` (highest `NNNN-` prefix, excluding `README.md`), unless a different one is referenced in recent commit messages.

The adversarial reviewer is independent — it has not seen the implementation reasoning. Its job is to find reasons the diff should NOT merge: spec deviation, weak tests, missing edge cases, hidden side effects, don't-touch violations, simpler alternatives that were skipped.

Surface its findings verbatim. Do NOT argue with or rationalize away its calls. The point of running this alongside `/review` is to surface failure modes a collaborative review misses; if the adversarial reviewer's concerns are real, fixing them is cheaper now than after merge.

Intended workflow: run `/review` and `/review-adversarial` on the same diff, read both, and adjudicate. They use the same section structure so side-by-side comparison is direct.
