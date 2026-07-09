# Secrets and public-repo hygiene

**Treat this repo as public from commit #1, even if it is currently (or
was recently) private.** Many of my repos start private and flip to
public after a feature lands. Rewriting history after that flip is
destructive — every commit SHA changes, existing clones break, and the
old state may already be archived by forks, GitHub's network view, or
anyone who cloned before the rewrite. The cheapest fix is to never
commit the thing in the first place.

The rules below apply across every public surface, not just file
contents:

- File contents and diffs
- Commit messages (subject + body) and tag annotations
- Branch names and tag names
- PR titles, descriptions, review comments
- Issue titles, bodies, comments; Discussions; wiki pages; release notes
- CI workflow logs (echoed env vars, full paths, stack traces are all
  public for public repos)
- Author + committer email on every commit — history is forever

**Never commit:**

- Live credentials of any kind — API tokens, passwords, private keys,
  signing keys, OAuth secrets, session cookies, JWTs. If one ever lands
  in a commit, **rotate it immediately**; assume any value that touched
  history is compromised the moment it lands.
- `.env*` files other than `.env.*.example` (which must contain no real
  values). Gitignore `.env.*` with an explicit `!.env.*.example`
  whitelist.
- Internal hostnames, IPs, subnets, internal URLs, VPN endpoints,
  private Slack/Discord links, IRC channels.
- Names of coworkers, managers, customers, or anyone else who hasn't
  opted in to having their name attached to this repo.
- Private-tracker identifiers — Linear/Jira/Asana ticket IDs, internal
  doc URLs, Notion share links.
- Employer references in commit messages, comments, or repo metadata.
- File paths that leak identity or employer.
- Personal info — home address, phone, personal email, ID numbers.

If the repo is currently private and a flip to public is on the table,
do a full pre-flip scrub before clicking "Change visibility." The flip
exposes all of history, not just the current working tree, so re-audit
every surface in the "never commit" list above across the whole repo:

- `git log -p` — secrets, internal hostnames, employer references, and
  real names hiding in old diffs and commit messages.
- `git log --format='%an <%ae>'` — author and committer identity on every
  commit must be the public GitHub identity, not a work address.
- Branch and tag names, PR/issue titles and bodies, and any CI logs.
- A secret sweep (`gitleaks detect`) and an `.env*` check — only
  `.env.*.example`, carrying no real values, should be tracked.

A hit means either rewriting history (destructive — every SHA changes,
and forks or caches may already hold the old state) or, better, not
flipping until it is clean. Catching it before the flip is the cheap
path.

## Secrets must not enter the context window either

A secret that never lands in a commit can still leak by being *read*:
anything a tool prints enters the conversation context, and transcripts
travel further than the repo (pasted into issues, shared session logs,
bug reports). Two layers keep secrets out of context:

- **Mechanical:** `permissions.deny` in `.claude/settings.json` blocks
  the Read tool on `.env` / `.env.*` files and `*.pem` / `*.key`
  material anywhere in the tree. This intentionally also blocks
  `.env.*.example` — an example file is small; the human pastes its
  contents into chat on the rare occasion the agent needs to see one.
- **Behavioral (this rule):** the deny list only gates the Read tool.
  Do not route around it — no `cat .env`, `printenv`, `env`, sourcing
  env files, or echoing credential-bearing variables through Bash. If a
  command's output could contain a live credential, don't run it;
  describe what you need and let the human run it outside the session.
