---
description: Interrogate a draft spec for underspecified areas and write the answers back into it. Run after /spec and the human's first edit, before /plan, on features with real unknowns.
argument-hint: [spec-path — defaults to the most recent draft spec]
---

Spec clarification pass. Run AFTER the human has edited the draft spec
from `/spec`, BEFORE `/plan`. Where `/scope-check` forces five fixed
questions before a spec exists, `/clarify` reads the spec that now
exists and targets what *it* leaves unspecified — ambiguity caught here
is a one-line spec edit; caught at review it is redone work.

Steps:

1. Read the spec at `$ARGUMENTS`, or the most recent `**Status:** draft`
   spec under `docs/specs/` if blank. If no draft spec exists, say so
   and stop — this command does not create specs.
2. Scan for underspecification against this checklist:
   - **Success criteria** that are not behavior-level or not testable as
     written ("works correctly", "is fast").
   - **Undefined inputs/outputs** — data shapes, formats, ranges, and
     encodings the implementation will have to guess at.
   - **Unstated failure behavior** — what happens on bad input, missing
     resources, partial failure, timeout.
   - **Boundary ambiguity** — edge cases the goal implies but the spec
     doesn't settle (empty, zero, duplicate, concurrent).
   - **Missing or vague `## External references`** — a cited authority
     with no URL, or values that need provenance but have no declared
     source.
   - **Non-goals that contradict the goal**, or obvious adjacent scope
     the spec neither claims nor excludes.
3. Ask the human about the findings — **one question at a time, highest
   leverage first, five questions maximum**. Each question must name the
   spec section it affects and offer your best-guess default so the
   human can answer with "yes, that" cheaply. Do NOT answer your own
   questions or infer silently; the point is to surface the call, not
   make it.
4. After each answer, edit the spec file directly — fold the answer into
   the section it belongs to (`## Goal`, `## Success criteria`,
   `## Non-goals`, `## External references`). Keep the human's voice;
   add, don't rewrite.
5. When done, show a summary of what changed in the spec and stop. Do
   NOT continue into `/plan` automatically.

If the scan finds nothing worth asking, say so explicitly ("spec is
implementable as written") and stop — do not invent questions to justify
the pass. Skip this command entirely on Trivial/Small tasks.
