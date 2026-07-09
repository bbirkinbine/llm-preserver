#!/usr/bin/env bash
# statusLine — one line under the prompt: branch · model · context %.
#
# Claude Code pipes session JSON to this script on stdin and displays
# whatever it prints (see .claude/settings.json → statusLine). The three
# fields shown are the ones that back standing rules in this scaffold:
#   branch     — the never-build-on-main rule stays visible every turn,
#                not just at the SessionStart branch-check warning.
#   model      — mid-session model switches (plan on a strong model,
#                execute on a cheaper one) are easy to forget.
#   context %  — quality degrades well before the hard limit; this is
#                the number that tells you when to reach for a phase
#                handoff / /clear (see WORKFLOW.md → "Session hygiene").
#
# Extraction uses only grep/sed — no jq / python3 dependency, so the
# script works on a bare machine before `uv sync` has run (same policy
# as block-destructive.sh). This is best-effort parsing, not a JSON
# parser: a missed field renders as a dash, never an error — the status
# line must not break the session.

set -euo pipefail

PAYLOAD="$(cat | tr '\n' ' ')"

# model.display_name — first "display_name" value in the payload.
MODEL="-"
M="$(printf '%s' "$PAYLOAD" \
  | grep -o '"display_name"[[:space:]]*:[[:space:]]*"[^"]*"' \
  | head -n 1 \
  | sed -e 's/.*:[[:space:]]*"//' -e 's/"$//')" || true
[[ -n "$M" ]] && MODEL="$M"

# context_window.used_percentage — anchor on the "context_window" object
# first: rate_limits also carries used_percentage keys, so matching the
# bare key on the full payload could pick up the wrong one. May be null
# or absent early in a session; render a dash then.
PCT="-"
CTX="$(printf '%s' "$PAYLOAD" | sed -e 's/.*"context_window"[[:space:]]*:[[:space:]]*{//')" || true
P="$(printf '%s' "$CTX" \
  | grep -o '"used_percentage"[[:space:]]*:[[:space:]]*[0-9][0-9.]*' \
  | head -n 1 \
  | sed -e 's/.*:[[:space:]]*//')" || true
[[ -n "$P" ]] && PCT="${P%.*}%"

# git branch — empty outside a repo or on a detached HEAD.
BRANCH="$(git branch --show-current 2>/dev/null)" || true
[[ -z "$BRANCH" ]] && BRANCH="(no branch)"

printf '%s · %s · ctx %s\n' "$BRANCH" "$MODEL" "$PCT"
