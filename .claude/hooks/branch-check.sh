#!/usr/bin/env bash
# SessionStart hook — warns when a coding session opens on main/master.
#
# Feature and fix code must be written on a dedicated branch, never on
# main. This hook is the early reminder; the no-commit-to-branch
# pre-commit hook is the hard backstop. See CLAUDE.md -> "Git workflow".
#
# stdout from a SessionStart hook is added to the session context, so
# the agent sees this warning at the start of every session.

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"

case "$branch" in
  main | master)
    echo "Git: this session started on '$branch'. Per CLAUDE.md -> Git workflow,"
    echo "do not write feature or fix code on '$branch'. Create a branch first:"
    echo "  - issue-tracked work:  gh issue develop <N> --name <N>-<slug> --checkout"
    echo "  - untracked tiny work: git switch -c <type>/<slug>"
    ;;
esac
