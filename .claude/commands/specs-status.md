---
description: Refresh the generated status block in docs/specs/README.md and print the same status list in chat. Driven by the specs-status.sh hook script; reads each spec's **Status:** field. Never edits a spec.
argument-hint: [status filter — e.g. "draft", "shipped", "abandoned"]
---

Show the status of every spec under `docs/specs/`, and refresh the
persisted dashboard.

The `## Status` block at the top of `docs/specs/README.md` is the single
place to see which specs have landed and which are in flight. It is kept
current automatically by the `specs-status.sh` PostToolUse hook (it
regenerates whenever a spec is created or its `**Status:**` changes).
This command forces that same regeneration and prints the list in chat —
use it when you want the table in front of you, or to repair the block if
it ever looks stale.

Procedure:

1. Run the generator, which regenerates the block in
   `docs/specs/README.md` and echoes the rendered list plus a per-status
   count line:

   ```bash
   bash .claude/hooks/specs-status.sh --print
   ```

2. If `$ARGUMENTS` is non-empty, filter the printed lines to specs whose
   status matches `$ARGUMENTS` (case-insensitive) before showing them.
   The regeneration in step 1 always covers every spec — the filter is
   display-only, applied to the chat output, not to the written block.

3. Show the output. Shipped, abandoned, and superseded specs render
   struck through (`~~…~~`); evergreen, draft, shipping, and paused
   render live. A `(blocked)` tag follows any spec whose `Depends on:`
   lists a dependency that has not shipped. Specs missing the
   `**Status:**` field are flagged inline as needing attention — surface
   them, do not guess or edit their status.

This command does not edit any spec file. The only thing it writes is the
generated block in `docs/specs/README.md`, which is a rendered cache of
the spec `**Status:**` fields — never a source of truth. If the script is
absent (an older bootstrap), fall back to aggregating the `**Status:**`
lines yourself with the equivalent of:

```bash
# NOTE: avoid the variable name `status` — it's a read-only built-in in zsh.
for spec in docs/specs/[0-9][0-9][0-9][0-9]-*.md; do
  [ -e "$spec" ] || continue
  title=$(awk 'NR==1 && /^# / { sub(/^# */,""); print; exit }' "$spec")
  spec_status=$(awk '/^\*\*Status:\*\*/ { sub(/^\*\*Status:\*\* */,""); print; exit }' "$spec")
  printf '%s | %s\n' "${spec_status:-MISSING}" "$title"
done
```
