# 0024 — Hugging Face Revision Support

**Status:** shipped (PR #59)
**Last updated:** 2026-10-20

## Goal

A syntactically valid `pull` can archive the wrong repository tree.
`pull` reads only a repo's **default branch**, but publishers sometimes
keep each variant or quantization on its own branch or tag and leave
`main` holding something else. The trigger is
`turboderp/GLM-5.3-Flash-exl3`. A read-only metadata probe on
2026-10-20 showed:

| revision | resolves to | files | size | contents |
| --- | --- | --- | --- | --- |
| `main` (default) | `ef396bc0…` | 7 | ~0 GiB | `cal_trace.*`, `qbench_prompts.json`, README: calibration material |
| `4.05bpw` | `2a30229e67012798ba9f0cd832bb78abf4c363d5` | 31 | 153.8 GiB | `config.json`, `chat_template.jinja`, safetensors shards: the deployable EXL3 model |

The repo has seven quant branches (`4.05bpw`, `3.05bpw`, `2.05bpw`,
`sc_*`) and no tags. `pull turboderp/GLM-5.3-Flash-exl3 --whole-repo`
succeeds, hashes, records, and exits 0, and what it preserves is the
calibration trace. Nothing in the output says the model is on another
branch. Writing `owner/repo@4.05bpw` does not help, because the
repo-id validator rejects it (spec 0011).

This spec adds `pull --revision TEXT`. The value can be a branch, a tag,
or a full commit SHA. `pull` resolves it to an immutable 40-hex commit
in its single metadata call, before any selection or transfer. Every
download, record field, and composed recovery command then uses that
commit. A branch is a moving pointer, so it selects the tree for one
invocation and is never stored as provenance. When `--revision` is
omitted, `pull` behaves exactly as it does today.

This is a correctness and provenance feature. The archive already pins
every file to a full commit (`FileEntry.revision`, schema v2). What is
missing is a way to choose *which* commit gets pinned.

## Success criteria

### CLI surface

1. `pull` accepts `--revision TEXT`. Its help text says the value may be
   a Hugging Face branch, tag, or full commit SHA, and that
   `llm-preserver` resolves it to an immutable commit before downloading.
2. Omitting `--revision` leaves behavior byte-identical: the hub seam is
   called as `repo_info(repo_id)` with no revision argument, the real
   client calls `model_info(repo_id, files_metadata=True)` exactly as
   today, and plan, hint, and success output are unchanged. Every
   existing test stays green without edits, apart from fakes gaining an
   optional parameter.
3. `owner/repo@revision` is **not** parsed. It still fails as an invalid
   repo id (exit 2).
4. An empty or whitespace-only `--revision` is refused with exit 2
   before any client is built or network call is made.
5. `--revision` composes with `--include`, `--whole-repo`, `--plan`,
   `--refresh-docs`, `--role`, `--base-model`, `--yes`, and the
   interactive file listing.

### Resolution and provenance

6. The requested revision is passed to the one metadata call:
   `HfApi.model_info(repo_id, revision=<value>, files_metadata=True)`.
   No second metadata call is added, and the spec 0003 one-call design
   holds.
7. The file inventory, sizes, and LFS SHA256s that feed selection, the
   interactive listing, archive-status markers, advisories, the plan,
   and hashing all come from the requested revision, never from the
   default branch.
8. `RepoInfo.commit` is the full SHA the hub returned for that revision.
   On the revision path the client refuses (hub-side fault, no traceback)
   a returned SHA that is not 40 lowercase hex characters. It also
   refuses one that differs from a requested value that is itself a full
   40-hex SHA.
9. Every `download()` call receives `RepoInfo.commit`, never the branch
   or tag text. This is already the invariant (`pull_transfer.py`,
   `pull_metadata.py`), and tests pin it for the revision path.
10. The artifact-level `revision` and every written `FileEntry.revision`
    hold the resolved full SHA. No record schema change: the requested
    ref is display and execution context only.
11. A branch or tag resolves afresh on each invocation. A full SHA is
    deterministic. The existing protections against changed weights and
    changed docs (`pull_plan`) apply unchanged against the selected
    revision's inventory.
12. Branch names containing `/` (e.g. `feature/q4`) and hub PR refs
    (`refs/pr/N`) are passed through verbatim. The client URL-quotes the
    revision (`quote(revision, safe='')`, verified in 1.33.0 source).

### Display

13. `--plan` with `--revision` prints one `revision:` line under the
    plan header naming the requested ref and the resolved commit when
    they differ, or the commit alone when the request was that commit.
    `--plan` still downloads nothing and writes nothing.
14. A real (non-plan) pull with `--revision` prints the same resolution
    line once, before the interactive listing or confirmations, so the
    human sees which tree they are choosing from before bytes move.
15. Requested-ref text is echoed through `clean_text` (it is
    user-typed, but terminal hygiene is uniform).

### Recovery commands

16. With `--revision`, every composed command (the spec 0007 resume
    hint at transfer start and on Ctrl-C, the `--plan` follow-up line,
    and the spec 0020 `--refresh-docs` recovery line) carries
    `--revision <resolved-sha>`, never the branch or tag. A moving
    branch therefore cannot switch a resumed pull onto a different tree.
    Without `--revision`, composed commands are unchanged.
17. The composer accepts only a 40-hex value for `--revision`. A
    non-conforming value yields no hint, the same rule spec 0007 applies
    to a malformed repo id.

### Errors

18. A missing revision (the hub raises `RevisionNotFoundError`, verified
    live for an unknown branch and for a 7-character SHA), a missing
    repo, a gated repo, or an inaccessible repo exits 2 through the
    existing mapping with no traceback, no record, no staging dir, and
    no archive mutation. Other client failures keep their current fault
    domains.

### One artifact, one tree (review-round adjudication, 2026-10-20)

21. A `--revision` pull whose target artifact (same format subdir) is
    recorded at a different commit stops during planning with exit 5
    (`error [integrity]`), before any download, in `--plan` too. The
    message names the archived commit and the two ways out:
    `--revision <archived commit>` to extend that tree, or remove the
    artifact to switch trees. A pull that names the archived commit
    proceeds. Default-branch pulls are not guarded (pre-0024 behavior
    kept). Both reviewers reproduced the motivating case: disjoint shard
    names plus a same-size hashless `config.json` merged two branches
    at exit 0 into one artifact whose `revision` named only the newer
    commit.
22. `--revision .` and `--revision ..` are refused at exit 2 before any
    network call. The HTTP client normalizes a trailing `/revision/..`
    dot segment away, which would silently read the default branch
    (security review, reproduced with httpx).

### Docs and canary

19. `docs/cli.md` gains **Pulling a branch, tag, or pinned commit**.
    It covers syntax, why the option exists (variants on non-default
    branches; `main` may hold metadata or calibration), mutable
    selectors vs. the resolved pin, plan output, the recovery commands,
    errors, why `repo@revision` is unsupported, and the GLM example as
    dated evidence. The README feature summary and the pull options
    list mention the flag.
20. The opt-in live canary (`tests/test_hf_live_canary.py`) gains a
    read-only check that `repo_info(gpt2, revision=<pinned sha>)`
    returns that SHA. No mutable branch head and no large model is
    hard-coded, and CI keeps no dependence on the GLM repo.

## Non-goals

- **`discover --revision`.** Discover's handoff keeps the default
  branch. Discover pre-fetches `RepoInfo` without a revision; threading
  one through would widen the feature into the discover flow. Queued in
  TODO: a discover-side notice when a picked repo has non-default
  branches, which is how the GLM case would surface there.
- **Several revisions of one repo side by side.** ADR 0003 gives one
  directory per source repo, and criterion 21 refuses a second commit
  into an artifact rather than merging it. Per-revision directories
  would be a layout change and need an ADR.
- **Guarding default-branch pulls against mixing.** A plain pull into
  a directory filled from a branch can still merge default-branch
  files, limited only by the per-file immutability checks. Refusing it
  would also refuse the normal "main moved, add another quant" workflow.
  Documented in `docs/cli.md`.
- **Listing a repo's branches and tags** (`list_repo_refs`). Useful for
  discoverability, but it is a second metadata call and a new surface.
  Queued.
- **`repo@revision` shorthand.** Rejected: it would weaken repo-id
  validation and is ambiguous with branch names containing `/`.
- **Recording the requested branch/tag name.** It is not provenance. A
  later spec can add a display-only field if live use wants it.
- **Short-SHA expansion.** The hub rejects 7-character SHAs
  (`RevisionNotFoundError`), so they error through criterion 18.

## Notes

- **Integration point.** `HubClientProtocol.repo_info(repo_id, revision:
  str | None = None)` and `HubClient.repo_info`. With `revision=None`
  the client makes the call it makes today, and the kwarg is omitted, so
  the call-shape test is unchanged. `run_pull` fetches `RepoInfo` up
  front when `--revision` is set (it already does so for the interactive
  listing) so the resolved commit is known before hints are composed,
  and passes it down through the existing `repo_info=` seam. That keeps
  it to one metadata call, and `prepare_pull` / `pull_model` keep their
  signatures.
- **Staging.** `.staging/<owner>/<repo>/` is per repo, not per revision.
  `hf_hub_download(local_dir=…)` re-downloads a staged file whose etag
  differs at the requested commit (download guide, below), and LFS files
  are hash-checked against the selected revision's SHA256. The plan's
  "already staged" byte credit is path-based, so after an interrupted
  pull of a *different* revision the disk estimate can be low. That is a
  preflight inaccuracy, not an integrity gap, and it is accepted.
- **Fake clients.** Some test doubles define `repo_info(self, repo_id)`.
  Because the no-revision call stays positional-only, they keep working
  unchanged. Only the shared `FakeHubClient` gains the optional
  parameter and records it.

## External references

- **Hugging Face `HfApi.model_info` reference (huggingface_hub v1.33.0)**
  — https://huggingface.co/docs/huggingface_hub/v1.33.0/en/package_reference/hf_api,
  retrieved 2026-10-20, license: Apache-2.0 (huggingface_hub project).
  Verified: signature `model_info(repo_id, revision=None, timeout=None,
  securityStatus=None, files_metadata=False, expand=None, token=None)`;
  `revision`: "The revision of the model repository from which to get the
  information."; raises `RepositoryNotFoundError` and
  `RevisionNotFoundError` ("If the revision to download from cannot be
  found."); `ModelInfo.sha`: "Repo SHA at this particular revision."
- **Hugging Face download guide (v1.33.0)** —
  https://huggingface.co/docs/huggingface_hub/v1.33.0/en/guides/download,
  retrieved 2026-10-20, license: Apache-2.0. Verified: `revision` may be
  a branch, tag, PR ref (`refs/pr/3`), or commit hash, and "When using
  the commit hash, it must be the full-length hash instead of a
  7-character commit hash"; `local_dir` re-downloads a file when its
  metadata has changed.
- **Independent check: installed source and live probe.**
  `inspect.getsource(HfApi.model_info)` in the locked 1.33.0 client shows
  the request path `/api/models/{repo_id}/revision/{quote(revision,
  safe='')}`, which is the basis for criterion 12. A read-only
  `model_info` probe on 2026-10-20 produced the GLM table above and
  showed `RevisionNotFoundError` for an unknown branch and a short SHA.
  The GLM branch head is a dated observation for the docs. No test
  hard-codes it.

## Implementation plan (approved 2026-10-20)

Planner output approved with these decisions (Brian, "go with your
recommendations"):

1. File-count guard waived for this spec (~8 src, ~6 test, ~5 docs).
2. The resolution line prints on stdout before the interactive listing
   on real pulls; in `--plan` it prints only inside `render_plan`.
3. A windowed listing may scroll the line off screen; accepted for v1
   (it stays in scrollback).
4. An uppercase 40-hex request counts as a SHA and is compared
   lowercased; hints carry the hub's lowercase SHA.
5. The client's 40-hex check on the returned SHA runs only on the
   revision path; checking every call is queued under Hardening.

Shape: new `revision.py` (shared 40-hex rule, resolution line,
positional-when-None fetch), `cli/pull_exec/plan_run.py` extracted from
`flow.py` (proven behavior-neutral before revision lands), additive
`revision=` on the hint composers and `render_plan`.
