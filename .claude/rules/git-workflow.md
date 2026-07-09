# Git workflow

The standing rule: **every change happens on its own branch — never
write feature or fix code on `main`.** Create the branch yourself, as
soon as there is a spec or an issue to work. Do not wait to be asked;
branching is not an optional courtesy step.

## Branch naming

- Work tracked by a GitHub issue → `<issue-number>-<slug>`, e.g.
  `42-add-user-prefs`. Create it with
  `gh issue develop <N> --name <N>-<slug> --checkout`, which links the
  branch to the issue in GitHub's UI. Plain `git switch -c <N>-<slug>`
  also works but loses that linkage.
- Untracked tiny work with no issue — XS fixes, chores, hotfixes →
  `<type>/<slug>`, where `<type>` is one of `feat` `fix` `chore` `docs`
  `refactor`, e.g. `chore/bump-ruff`. Do not invent a fake issue
  number.
- Anything past XS should get a GitHub issue first unless the repo is
  explicitly local-only — issues are the cross-session persistence layer.
  The spec number, the issue number, and the branch number are the same
  number; that shared id ties spec ↔ issue ↔ branch ↔ PR together. In
  local-only mode, use the next local spec number and a branch like
  `spec-NNNN-<slug>`. The number is an identifier, not an execution
  order — gaps in `docs/specs/` are expected (issue numbers are also
  consumed by bugs and questions), and specs ship in whatever order triage
  dictates. See `docs/specs/README.md` → "Numbering".

One branch per spec / unit of work.

## Before the Implement phase

Check `git branch --show-current`. If it returns `main` or `master`,
stop and create the branch first. Two guardrails back this up — the
`no-commit-to-branch` pre-commit hook blocks commits on `main`, and a
SessionStart hook warns when a session opens on `main` — but a guardrail
firing means the branch was created too late. Branch at the right time;
treat the guardrails as a backstop.

## Commits and pushes

Never commit or push on your own. Each commit needs an explicit
"commit" instruction from the human in the current conversation; never
push without being explicitly asked, and never use `--force` without a
direct ask. Workflow: make the change, show `git status` and
`git diff`, then wait.

## Pull requests

Open with `gh pr create --fill --web`. In GitHub-backed mode, the PR body
must contain a closing keyword line — `Closes #<issue-number>` — so the
merge auto-closes the issue. Closing keywords work in the PR body, not in
feature-branch commit messages. In local-only mode, omit the closing
keyword. Run `/review` before opening the PR.
