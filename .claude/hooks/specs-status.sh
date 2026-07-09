#!/usr/bin/env bash
# specs-status hook + generator — keep docs/specs/README.md showing the
# live status of every spec.
#
# docs/specs/README.md carries a generated block between the markers
#   <!-- specs-status:start --> ... <!-- specs-status:end -->
# This script regenerates that block from each spec's **Status:** field.
# Shipped, abandoned, and superseded specs render struck through (~~...~~);
# evergreen / draft / shipping / paused render live. The block is the
# single place to see, at a glance, which specs have landed and which are
# still in flight. Source of truth stays each spec's **Status:** line —
# this is a rendered cache, never hand-edited.
#
# Three invocations:
#   --hook   PostToolUse mode. Reads the tool JSON on stdin and only
#            regenerates when the edited file is a spec
#            (docs/specs/NNNN-*.md). Quiet; never blocks the tool.
#   --print  Regenerate, then echo the rendered list + a counts line to
#            stdout. This is what /specs-status runs.
#   (none)   Regenerate quietly. A manual force-refresh.
#
# See docs/specs/README.md -> "Status" and CLAUDE.md -> "Hooks and
# guardrails".

set -uo pipefail

MODE="write"
case "${1:-}" in
  --hook) MODE="hook" ;;
  --print) MODE="print" ;;
  "") MODE="write" ;;
  *)
    echo "usage: specs-status.sh [--hook|--print]" >&2
    exit 2
    ;;
esac

# Locate the specs dir relative to this script, so the hook works no matter
# what the tool's CWD is. Script lives at .claude/hooks/, so the project
# root is two levels up.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SPECS_DIR="$ROOT/docs/specs"
README="$SPECS_DIR/README.md"

# Hook mode: only act on edits to a spec file. The PostToolUse matcher is
# Edit|Write (every edit), so this gate keeps us off unrelated writes —
# including edits to README.md itself, whose name has no NNNN- prefix.
if [ "$MODE" = "hook" ]; then
  input="$(cat)"
  fp="$(printf '%s' "$input" \
    | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
    | head -1)"
  case "$fp" in
    */docs/specs/[0-9][0-9][0-9][0-9]-*.md | docs/specs/[0-9][0-9][0-9][0-9]-*.md) ;;
    *) exit 0 ;;
  esac
fi

[ -d "$SPECS_DIR" ] || exit 0
[ -f "$README" ] || exit 0

START='<!-- specs-status:start -->'
END='<!-- specs-status:end -->'
NOTE='<!-- Generated block — do not edit by hand; the specs-status hook overwrites it. Source of truth is each spec'"'"'s **Status:** field. -->'

# --- gather one row per spec: number, status, title, slug, depends ---
rows="$(mktemp)"
for spec in "$SPECS_DIR"/*.md; do
  [ -e "$spec" ] || continue
  base="$(basename "$spec")"
  [ "$base" = "README.md" ] && continue
  case "$base" in
    [0-9][0-9][0-9][0-9]-*.md) ;;
    *) continue ;;
  esac
  num="${base%%-*}"
  title="$(awk 'NR==1 && /^# / { sub(/^# */, ""); print; exit }' "$spec")"
  st="$(awk '/^\*\*Status:\*\*/ { sub(/^\*\*Status:\*\* */, ""); sub(/[ \t]+$/, ""); print; exit }' "$spec")"
  dep="$(awk '/^\*\*Depends on:\*\*/ { sub(/^\*\*Depends on:\*\* */, ""); sub(/[ \t]+$/, ""); print; exit }' "$spec")"
  [ -z "$st" ] && st="MISSING"
  printf '%s\t%s\t%s\t%s\t%s\n' "$num" "$st" "${title:-$base}" "$base" "${dep:-}" >> "$rows"
done

# status of a given spec number (from the rows we just gathered) — used to
# decide whether a Depends-on edge is still blocking.
status_of() {
  awk -F'\t' -v n="$1" '$1 == n { print $2; exit }' "$rows"
}

# sort rank by status: in-flight first, the closed design-log at the bottom.
rank_of() {
  case "$1" in
    evergreen) echo 0 ;;
    draft) echo 1 ;;
    shipping) echo 2 ;;
    shipped) echo 3 ;;
    paused) echo 4 ;;
    abandoned) echo 5 ;;
    superseded-by-*) echo 6 ;;
    MISSING) echo 9 ;;
    *) echo 8 ;;
  esac
}

render_row() {
  local num="$1" st="$2" title="$3" slug="$4" dep="$5"
  if [ "$st" = "MISSING" ]; then
    printf -- '- [%s](%s) — **no `**Status:**` field — needs attention**\n' "$title" "$slug"
    return
  fi
  local label="[$title]($slug)"
  case "$st" in
    shipped | abandoned | superseded-by-*) label="~~$label~~" ;;
  esac
  local line="- $label  ($st)"
  if [ -n "$dep" ] && [ "$dep" != "-" ]; then
    local blocked=0 d
    for d in $(printf '%s' "$dep" | tr ',' ' '); do
      case "$d" in
        [0-9][0-9][0-9][0-9]) [ "$(status_of "$d")" != "shipped" ] && blocked=1 ;;
      esac
    done
    if [ "$blocked" = 1 ]; then
      line="$line — depends on $dep (blocked)"
    else
      line="$line — depends on $dep"
    fi
  fi
  printf '%s\n' "$line"
}

# --- render the body (sorted by rank, then spec number) ---
body="$(mktemp)"
if [ ! -s "$rows" ]; then
  printf '%s\n' '_No specs yet. Run `/spec <name>` to create the first one; this list fills in and stays current as statuses change._' > "$body"
else
  ranked="$(mktemp)"
  while IFS=$'\t' read -r num st title slug dep; do
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(rank_of "$st")" "$num" "$st" "$title" "$slug" "$dep" >> "$ranked"
  done < "$rows"
  while IFS=$'\t' read -r _ num st title slug dep; do
    render_row "$num" "$st" "$title" "$slug" "$dep"
  done < <(sort -t"$(printf '\t')" -k1,1n -k2,2 "$ranked") >> "$body"
  rm -f "$ranked"
fi

# --- splice the body between the markers in README ---
out="$(mktemp)"
awk -v start="$START" -v end="$END" -v note="$NOTE" -v bodyfile="$body" '
  index($0, start) {
    print
    print note
    while ((getline line < bodyfile) > 0) print line
    close(bodyfile)
    skipping = 1
    next
  }
  index($0, end) { skipping = 0; print; next }
  !skipping { print }
' "$README" > "$out"

# Only rewrite when the content changed, to avoid needless mtime churn (and
# any chance of the PostToolUse write re-triggering tooling on README).
if ! cmp -s "$out" "$README"; then
  cat "$out" > "$README"
fi

if [ "$MODE" = "print" ]; then
  cat "$body"
  if [ -s "$rows" ]; then
    echo
    awk -F'\t' '
      { c[$2]++; order[$2] = order[$2] }
      END {
        n = 0
        split("evergreen draft shipping shipped paused abandoned", ks, " ")
        for (i = 1; i <= 6; i++) if (ks[i] in c) { if (n++) printf " · "; printf "%d %s", c[ks[i]], ks[i]; delete c[ks[i]] }
        for (k in c) { if (n++) printf " · "; printf "%d %s", c[k], k }
        if (n) print ""
      }
    ' "$rows"
  fi
fi

rm -f "$rows" "$body" "$out"
exit 0
