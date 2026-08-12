#!/usr/bin/env bash
# Refuse to call a spec branch ready while its close-out is missing.
#
# The close-out rule (CLAUDE.md) predates this script and was still
# missed on spec 0017: the PR opened, merged, and only then did anyone
# notice TODO still listed the spec under "In progress". That cost a
# second PR for pure bookkeeping — the exact waste the rule exists to
# prevent. A rule that is written down and still gets skipped needs a
# check, so this is that check.
#
# Runs from /review-check, the gate before /review and the PR. Silent
# on any branch that is not `spec-NNNN-<slug>`.
set -uo pipefail

branch="$(git branch --show-current 2>/dev/null)" || exit 0
[[ "$branch" =~ ^spec-([0-9]{4})- ]] || exit 0
spec="${BASH_REMATCH[1]}"

spec_file="$(ls docs/specs/"$spec"-*.md 2>/dev/null | head -1)"
if [[ -z "$spec_file" ]]; then
  echo "close-out: no docs/specs/${spec}-*.md for branch ${branch}" >&2
  exit 1
fi

missing=()

# 1. The spec must not still call itself a draft.
status_line="$(grep -m1 '^\*\*Status:\*\*' "$spec_file" || true)"
case "$status_line" in
  *draft*) missing+=("${spec_file}: still '**Status:** draft' — flip it to shipping/shipped") ;;
esac

# 2. TODO must carry the spec in its "## Shipped" section specifically.
# Matching the number anywhere in the file is what let spec 0017
# through: it appeared under "In progress" and in passing references,
# so a whole-file grep was satisfied while the Shipped entry was
# missing — the very thing the rule asks for.
if ! awk '/^## Shipped/{s=1;next} /^## /{s=0} s' TODO.md 2>/dev/null | grep -q "\b${spec}\b"; then
  missing+=("TODO.md: spec ${spec} is not in the '## Shipped' section")
fi

# 3. The session notes must mention it, so a /clear'd session finds it.
if ! grep -q "\b${spec}\b" CLAUDE.md 2>/dev/null; then
  missing+=("CLAUDE.md: no session note mentioning spec ${spec}")
fi

# 4. Usage docs ride the feature: a CLI change must reach docs/cli.md.
if git diff --name-only main...HEAD 2>/dev/null | grep -q '^src/llm_preserver/cli/'; then
  if ! git diff --name-only main...HEAD 2>/dev/null | grep -q '^docs/cli.md$'; then
    missing+=("docs/cli.md: untouched while src/llm_preserver/cli/ changed")
  fi
fi

if (( ${#missing[@]} )); then
  echo "" >&2
  echo "close-out incomplete for spec ${spec} — these belong on THIS branch," >&2
  echo "before the PR opens, not in a follow-up (CLAUDE.md → close-out):" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  echo "" >&2
  exit 1
fi

echo "close-out: spec ${spec} bookkeeping present"
