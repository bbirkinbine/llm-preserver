---
description: Five forcing questions to clarify a feature before /spec. Run manually on features where the goal or scope is ambiguous. Output feeds the spec's Goal and Non-goals sections.
argument-hint: <one-line feature description>
---

Pre-spec scope check. Run BEFORE `/spec` when the feature's goal or scope feels unclear. The point is to surface ambiguity before it propagates into the spec, the plan, and the tests.

You are interviewing the human. Ask the five questions below, one at a time, and wait for an answer before asking the next. Do NOT answer the questions yourself, infer answers from context, or proceed to writing a spec.

Questions:

1. **Who is this for, specifically?** Name the concrete user, role, or upstream caller. "Future users" or "the system" is not an answer.
2. **What's the smallest version that proves the thesis?** Strip every "while we're here" and "we'll also need." What's the irreducible core?
3. **What does success look like in one sentence?** A behavior-level outcome, not "the code is clean." Something that could be a test assertion.
4. **What would make you kill this in 3 months?** What's the failure mode that would make you say "we shouldn't have built this"?
5. **What's explicitly NOT in scope?** Adjacent things you might be tempted to lump in but should defer or skip.

After the human answers all five, output a short summary block in this shape:

```markdown
## Scope check — $ARGUMENTS

**For:** <answer 1>
**Smallest version:** <answer 2>
**Success in one sentence:** <answer 3>
**Kill condition:** <answer 4>
**Explicitly out of scope:** <answer 5>
```

Tell the human to paste the relevant parts into the spec's `## Goal` and `## Non-goals` sections when they run `/spec`. Do NOT write the spec yourself or invoke `/spec` automatically.

If an answer is vague ("not sure", "depends"), push back once: "what would have to be true for that answer to be concrete?" Don't push twice — ambiguity is a useful signal too, and the human may not have the answer yet.
