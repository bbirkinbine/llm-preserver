---
description: Read-only cross-artifact consistency check — spec vs tests vs diff vs standing rules. Run after /test-first (spec-to-tests) or before /review (spec-to-diff). Reports findings; changes nothing.
argument-hint: [spec-path — defaults to the most recent shipping spec]
---

Cross-artifact consistency check. The reviewers judge the *code*; this
command checks that the artifacts agree with *each other* — that the
tests actually cover the spec and the diff actually serves it. Run it
at either of two points: after `/test-first` (before implementation —
the cheap time to find a coverage hole) or before `/review`.

Read-only: report findings, change nothing.

Steps:

1. Read the spec at `$ARGUMENTS`, or the most recent
   `**Status:** shipping` (else `draft`) spec under `docs/specs/`.
2. Read the test files the feature added or modified, and the current
   diff against `main` (`git diff main...HEAD`) if implementation has
   started.
3. Build the coverage table — one row per success criterion:

   | Success criterion | Covering test(s) | Status |
   | --- | --- | --- |
   | <criterion, abbreviated> | `tests/test_x.py::test_name` | covered / partial / **uncovered** |

4. Then check, in both directions:
   - **Spec → tests:** every success criterion has at least one test
     that would fail if the behavior were wrong (not merely a test that
     exercises the code path).
   - **Tests → spec:** tests that pin down behavior the spec never
     defines — undeclared scope that should be either specced or
     dropped.
   - **Non-goals:** anything in the diff or tests implementing a
     declared non-goal.
   - **External references:** values in the diff that claim outside
     authority (constants, format markers, API contracts) but aren't
     declared in the spec's `## External references`, or declared
     sources with no pinned URL/date in the code.
   - **Standing rules:** the diff against the don't-touch list in
     `CLAUDE.md` and the rules in `.claude/rules/`.
5. Tag each finding `[auto-fix]`, `[ask-user]`, or `[no-op]` with the
   same meanings the reviewers use. A coverage hole found before
   implementation is `[auto-fix]` (route it back to `/test-first`); a
   spec contradiction is always `[ask-user]`.

End with one line: `consistent` or `N findings (M ask-user)`. If there
is no spec, stop and say so — there is nothing to be consistent with.
