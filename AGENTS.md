# AGENTS.md

This project uses `CLAUDE.md` as the source of project context for AI
coding agents. See [`CLAUDE.md`](CLAUDE.md) in this directory for:

- Stack and conventions
- Workflow expectations (Spec → Plan → Test-first → Implement → Verify)
- Available subagents, skills, and slash commands
- Don't-touch list
- Code / commit style rules
- Public-repo hygiene rules

`AGENTS.md` exists as a portable fallback for non-Claude agents (Codex,
Cursor, Gemini, etc.) that look for this filename by convention. The
authoritative content lives in `CLAUDE.md`; keep them in sync by editing
`CLAUDE.md` and leaving this file as a pointer.

**If non-Claude agents work this repo regularly**, invert the
relationship instead of maintaining a stub: make `AGENTS.md` the real
file and `CLAUDE.md` a symlink to it (`ln -sf AGENTS.md CLAUDE.md`) —
one file on disk, auto-loaded under both filenames. The filename only
controls which tools auto-load the content; a controlled study
(arXiv 2601.20404, Jan 2026, 124 PRs) found a well-structured agent
context file cut agent runtime ~29% and token use ~17%, regardless of
which name it carries. For a Claude-Code-primary repo, the stub
arrangement above is fine.
