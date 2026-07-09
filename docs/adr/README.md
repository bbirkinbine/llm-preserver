# Architecture Decision Records (ADRs)

An ADR captures a **cross-cutting technical decision** and the reasoning
behind it, at the moment it is made. Read these before changing
architecture, and before re-opening a decision that looks settled — the
rationale that is obvious today is the tribal knowledge a fresh session
(human or agent) lacks in six months.

## Spec vs ADR — which one

| | Spec (`docs/specs/NNNN-*.md`) | ADR (`docs/adr/NNNN-*.md`) |
| --- | --- | --- |
| Answers | *What* this unit of work delivers | *Why* we made a technical choice |
| Scope | One feature / issue | Cross-cutting — many features inherit it |
| Numbered by | The GitHub issue number | An independent sequence (see below) |
| Lifecycle | Ships, then is a design log | Stands until superseded |
| Trigger | Any non-trivial feature | A choice costly to reverse |

Rule of thumb: if the decision affects only the feature in front of you,
it lives in that feature's spec under `## Sketch`. If several features
will inherit it — a storage engine, an async/sync boundary, a public API
shape, an authentication model, a serialization format, a module-boundary
doctrine — it earns an ADR. Most small projects never write one; reach
for an ADR on **Large** work (see `CLAUDE.md` → the task-size table).

A feature spec whose approach hinges on an ADR links to it rather than
re-arguing the decision.

## Numbering

`NNNN-<kebab-name>.md`, zero-padded to four digits, starting at `0001`.

ADRs are numbered **independently of issues** — this is the deliberate
difference from specs, where the number *is* the issue number. An ADR is
a sequential decision log: the next ADR is the highest existing number
plus one. Create the file with `/adr <title>` (which drafts the four
sections from an in-session design discussion when one exists, or lays
down a skeleton to fill in) or by hand — see
[`../../WORKFLOW.md`](../../WORKFLOW.md) → "Authoring an ADR: when, and two
styles." Once assigned, a number never changes, even after the ADR is
superseded.

## Status header

Top of every ADR:

```markdown
# NNNN — <Title>

**Status:** proposed | accepted | superseded-by-NNNN | deprecated
**Last updated:** YYYY-MM-DD
```

Status vocabulary:

- `proposed` — written, not yet agreed. Under discussion.
- `accepted` — the decision is in force. New code follows it.
- `superseded-by-NNNN` — replaced by a later ADR. Link forward to it,
  and set the successor's `## Context` to reference what it replaces. The
  old ADR stays in place — the log of *why we changed our mind* is as
  valuable as the current decision.
- `deprecated` — no longer followed, with no direct successor (e.g. the
  subsystem it governed was removed).

Superseding, not editing, is how an ADR changes. An accepted ADR is a
historical record; when the decision changes, write a new ADR rather than
rewriting the old one. (The `**Last updated:**` field tracks status flips
and clarifications, not reversals of the decision itself.)

## Section shape

The four sections in the template are the Michael Nygard ADR format:

- **Context** — the forces in play when the decision was made: technical
  constraints, product needs, what made a decision necessary *now*. State
  the problem honestly, including the parts that argue against the
  decision you reached.
- **Decision** — the choice, in active voice ("We will …"). One decision
  per ADR; if you are deciding two things, write two ADRs.
- **Consequences** — what becomes easier and what becomes harder. The
  trade-offs you are accepting, and the follow-on work the decision
  creates. A consequences section that lists only upsides is incomplete.
- **Alternatives considered** — the options you rejected and why. This is
  what stops the decision from being relitigated: the next person can see
  their "obvious" idea was already weighed.

Keep an ADR reviewable in under ten minutes. The value is in the
reasoning, not the length.
