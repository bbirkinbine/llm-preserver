#!/usr/bin/env bash
# PreToolUse hook — block catastrophic Bash commands before they run.
#
# Reads the PreToolUse JSON payload on stdin, extracts tool_input.command,
# and matches it against a deny-list of unrecoverable / hard-to-undo
# patterns. Exits 2 (with the reason on stderr) to block the tool call;
# exits 0 to allow it.
#
# This is a mechanical backstop, NOT a substitute for the explicit-approval
# rule in CLAUDE.md. The rule catches things like "git push" that are
# policy-gated but recoverable; the deny-list catches things that are
# truly unrecoverable: rm -rf on system paths, git clean -fd, history
# rewrites, disk writes, filesystem creates, terraform destroy,
# destructive SQL. (Force-push and hard reset are deliberately NOT
# listed — see the git section below.)
#
# It is also one layer, not the whole defense. Claude Code's OS-level
# sandboxing (/sandbox) and permission modes sit above this hook and are
# the right tool for unattended runs; /rewind recovers from mistakes the
# deny-list was never meant to catch. Keep this list narrow and
# false-positive-free rather than growing it toward a sandbox substitute.
#
# To bypass for a legitimate need:
#   - run the command outside the agent session (a regular terminal), or
#   - temporarily comment out the PreToolUse hook in .claude/settings.json.
# Do not edit this script to silently allow a one-off — bypass deliberately.

set -euo pipefail

# Extract tool_input.command from the JSON stdin payload using only sed —
# no python3 / jq dependency, so the hook works on a bare machine before
# `uv sync` has run. This is not a full JSON parser; it is a deliberate
# best-effort extraction:
#   1. flatten newlines (pretty-printed payloads) to spaces,
#   2. strip everything up to and including the `"command":"` key,
#   3. cut the value at its closing quote — the first `"` that is followed
#      by `}` or `,` (i.e. end of tool_input, or the next field).
# For the dangerous commands this list targets (no embedded quotes) the
# extraction is exact. Commands containing embedded quotes may be clipped,
# but the dangerous substring still survives for matching. If the
# `"command"` key isn't found at all, COMMAND is empty and we allow — the
# hook must never block the agent because of an extraction miss.
PAYLOAD="$(cat | tr '\n' ' ')"
COMMAND=""
# Only extract if the key is present; otherwise leave COMMAND empty so an
# extraction miss falls through to "allow" rather than scanning the raw
# payload (which would false-positive on the description / cwd fields).
if printf '%s' "$PAYLOAD" | grep -q '"command"[[:space:]]*:'; then
  COMMAND="$(printf '%s' "$PAYLOAD" \
    | sed -e 's/.*"command"[[:space:]]*:[[:space:]]*"//' \
          -e 's/"[[:space:]]*[},].*$//')"
fi

if [[ -z "$COMMAND" ]]; then
  exit 0
fi

check() {
  local pattern="$1"
  local reason="$2"
  if printf '%s' "$COMMAND" | grep -qiE -- "$pattern"; then
    cat >&2 <<EOF
BLOCKED by .claude/hooks/block-destructive.sh
  reason:  $reason
  command: $COMMAND
  pattern: $pattern

This is a mechanical backstop for unrecoverable commands. To proceed:
  - run the command outside the agent session, or
  - temporarily disable the PreToolUse hook in .claude/settings.json.
EOF
    exit 2
  fi
}

# --- rm catastrophes ---
# rm with a recursive+force flag combo targeting /, ~, $HOME, or a bare *.
check 'rm[[:space:]]+(-[a-zA-Z]*[rfRF][a-zA-Z]*[[:space:]]+)+(/|~|\$HOME|\*)([[:space:]]|$)' \
      'rm -rf targeting /, ~, $HOME, or *'
check 'rm[[:space:]]+.*--no-preserve-root' \
      'rm --no-preserve-root'
check '(^|[[:space:]])sudo[[:space:]]+rm([[:space:]]|$)' \
      'sudo rm'

# --- git: unrecoverable only ---
# Force-push and `git reset --hard` are deliberately NOT here: force-push
# is gated by the explicit-approval rule in CLAUDE.md, and `reset --hard`
# is recoverable via the reflog. This list is for the truly unrecoverable
# git operations only.
check 'git[[:space:]]+clean[[:space:]]+-[a-zA-Z]*[fd]' \
      'git clean -fd (permanently deletes untracked work — not in reflog)'
check 'git[[:space:]]+(filter-branch|filter-repo)' \
      'git history rewrite (filter-branch / filter-repo)'

# --- disk / filesystem ---
check ':\(\)[[:space:]]*\{[[:space:]]*:\|:&[[:space:]]*\};:' \
      'forkbomb'
check 'dd[[:space:]]+.*of=/dev/' \
      'dd writing to a device'
check '(^|[[:space:]]|;|&)mkfs(\.[a-z0-9]+)?[[:space:]]+.*/dev/' \
      'mkfs.* against a /dev/ target (filesystem create)'
check '>[[:space:]]*/dev/(sd[a-z]|nvme|disk[0-9])' \
      'raw redirect to a disk device'

# --- system power ---
check '(^|[[:space:]])(sudo[[:space:]]+)?(shutdown|reboot|halt|poweroff)([[:space:]]|$)' \
      'system power command (shutdown / reboot / halt / poweroff)'

# --- permission catastrophes ---
check 'chmod[[:space:]]+(-R[[:space:]]+)?(777|0777)[[:space:]]+/' \
      'chmod 777 on a system path'

# --- service / infra destruction ---
check 'gh[[:space:]]+repo[[:space:]]+delete' \
      'gh repo delete'
check 'terraform[[:space:]]+destroy' \
      'terraform destroy'

# --- destructive SQL passed on a DB CLI ---
# Anchored to a recognized DB client so it doesn't trip on the words
# "drop table" appearing in an echo, a filename, a heredoc, or a comment.
check '(psql|mysql|mariadb|sqlite3|mongosh?|clickhouse-client|cockroach[[:space:]]+sql)[[:space:]].*(drop|truncate)[[:space:]]+(table|database|schema)' \
      'destructive SQL (DROP / TRUNCATE) passed to a DB client'

exit 0
