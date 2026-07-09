---
description: Create a spec at docs/specs/NNNN-<slug>.md. Drafts goal / success / non-goals from the current discussion when one exists; otherwise lays down a skeleton to fill in. Stops for human review either way.
argument-hint: <feature name>
---

Create a new spec file under `docs/specs/`.

Procedure:

1. Determine `NNNN`:
   - Default GitHub mode: use the GitHub issue number for this work,
     zero-padded to four digits (spec number = issue number = branch
     number; see `.claude/rules/git-workflow.md`). If the conversation
     already references the issue, use that number. Otherwise run
     `gh issue list -s open` and ask the human which issue this spec
     belongs to. If no issue exists yet, stop and say so — anything past
     XS gets an issue before a spec. Offer to draft `gh issue create` for
     the human to approve.
   - Local-only mode: if the repo context says it does not use GitHub
     issues, do **not** run `gh` or block on issue creation. Use the
     highest existing 4-digit prefix in `docs/specs/` + 1.
   - Never reuse an existing prefix, and never use `0000` — it is
     reserved for the product spec (`docs/specs/0000-product.md`).
2. Derive a slug from `$ARGUMENTS` (lowercase, hyphen-separated, no punctuation).
3. Title-case `$ARGUMENTS` for the H1.
4. Determine today's date in `YYYY-MM-DD` (UTC or local, consistent with prior specs).
5. Decide how to fill the body:
   - **Draft mode — when the current conversation already contains a
     substantive discussion of this feature or fix** (goals, desired
     behavior, constraints, edge cases): populate `## Goal`,
     `## Success criteria`, and `## Non-goals` from that discussion, as
     concrete prose rather than placeholders. Write success criteria as
     observable, testable outcomes. Keep it reviewable in under ten
     minutes. Where you had to make a call the discussion did not settle,
     mark it inline as `<!-- assumption: ... -->` so the human can confirm
     or correct it. Do NOT invent facts, citations, or
     `## External references` provenance that did not come from the
     discussion — an unknown is an open question, not a fabricated answer.
   - **Skeleton mode — when there is no prior discussion to draw on:**
     write the placeholder skeleton below for the human to fill in.

   Either mode writes `docs/specs/NNNN-<slug>.md` in this shape
   (substitute `NNNN`, the title-cased name, and today's date):

```markdown
# NNNN — <Title-cased feature name>

**Status:** draft
**Last updated:** YYYY-MM-DD

## Goal

<one paragraph: what we're building and why>

## Success criteria

- <observable, testable outcome>
- <observable, testable outcome>

## Non-goals

- <thing we are explicitly not doing>

## Notes

- <optional: known risks, dependencies, open questions>
```

The `**Status:**` and `**Last updated:**` fields are load-bearing — `/specs-status` reads them to print the status table. Don't omit them.

If the work is blocked on other specs shipping first, add a
`**Depends on:** NNNN` line directly under `**Last updated:**` —
ordering lives there, never in the spec number itself.

Stop after writing the file — in either mode. Do NOT proceed to planning
or implementation. A drafted spec is a first pass for the human to review
and edit, not an approved spec: surface the path you wrote, and in draft
mode list any `<!-- assumption: ... -->` markers you left so they are easy
to resolve. The human owns the spec before any other phase begins.
