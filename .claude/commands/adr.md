---
description: Create an Architecture Decision Record at docs/adr/NNNN-<slug>.md. Drafts context / decision / consequences / alternatives from the current discussion when one exists; otherwise lays down a skeleton to fill in. Stops for human review either way.
argument-hint: <decision title>
---

Create a new ADR under `docs/adr/`.

An ADR records a **cross-cutting technical decision** — one that several
features inherit and that is costly to reverse (a storage engine, an
async/sync boundary, a public API shape, an auth model, a serialization
format). It is not a feature spec. If the decision affects only one unit
of work, it belongs in that feature's spec under `## Sketch`, not here.
See `docs/adr/README.md` for the full convention.

Procedure:

1. Determine `NNNN` — ADRs are numbered **independently of issues**
   (unlike specs, where the number *is* the issue number): take the
   highest existing 4-digit prefix in `docs/adr/` and add 1, zero-padded
   to four digits. The first ADR is `0001`. Never reuse a number, even
   for a superseded ADR.
2. Derive a slug from `$ARGUMENTS` (lowercase, hyphen-separated, no
   punctuation).
3. Title-case `$ARGUMENTS` for the H1.
4. Determine today's date in `YYYY-MM-DD`, consistent with prior ADRs.
5. Decide how to fill the body:
   - **Draft mode — when the current conversation already contains a
     substantive discussion of this decision** (the options weighed, the
     constraints, the trade-offs): populate `## Context`, `## Decision`,
     `## Consequences`, and `## Alternatives considered` from that
     discussion, as concrete prose rather than placeholders. State the
     forces honestly, including what argues against the choice; list the
     downsides under consequences, not only the upsides. Where you had to
     make a call the discussion did not settle, mark it inline as
     `<!-- assumption: ... -->`. Do NOT invent constraints, benchmarks, or
     rejected options that did not come from the discussion.
   - **Skeleton mode — when there is no prior discussion to draw on:**
     write the placeholder skeleton below for the human to fill in.

   Either mode writes `docs/adr/NNNN-<slug>.md` in this shape (substitute
   `NNNN`, the title-cased name, and today's date):

```markdown
# NNNN — <Title-cased decision name>

**Status:** proposed
**Last updated:** YYYY-MM-DD

## Context

<The forces at play — technical constraints, product needs, what makes a
decision necessary now. State the problem, including the parts that argue
against the decision you reach. Not the answer yet.>

## Decision

<The choice, in active voice: "We will …". One decision per ADR.>

## Consequences

<What this makes easier and what it makes harder — the trade-offs
accepted, and any follow-on work the decision creates. List downsides,
not only upsides.>

## Alternatives considered

- <option> — <why it was not chosen>
```

The `**Status:**` and `**Last updated:**` fields are load-bearing — keep
them. If this ADR replaces an earlier one, set the older ADR's status to
`superseded-by-NNNN` and link the two together (see `docs/adr/README.md`).

Stop after writing the file — in either mode. Do NOT proceed to
implementation. A drafted ADR is a first pass, not an accepted decision:
surface the path you wrote, and in draft mode list any
`<!-- assumption: ... -->` markers you left. The human reviews and edits
the rationale before the decision is acted on.
