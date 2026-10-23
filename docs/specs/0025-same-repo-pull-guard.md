# 0025 — Same-Repo Pull Guard

**Status:** draft
**Last updated:** 2026-10-23

## Goal

Two `pull` runs of **different** repos do not interfere: each writes
only inside its own `.staging/<owner>/<repo>/` and
`models/<owner>/<repo>/`, there is no archive-level index or lock to
contend for, and the owner-directory cleanup is a plain `rmdir` that
refuses while a sibling still has files there. Measured 2026-10-23: two
concurrent `--whole-repo --yes` pulls of two repos under one owner into
a scratch archive both exited 0, `verify` reported both valid, and
`.staging/` was empty afterward.

Two runs of the **same** repo do interfere, and nothing in the tool
detects it. They share one staging leaf and one record. The
`huggingface_hub` per-file locks serialize the download of each file,
but the first run to finish moves the staged files into the model
directory and then deletes the staging leaf under the second run, which
fails hashing or moving files that are gone (exit 3 — the exact
"second terminal kills the first" scenario spec 0019 refused to
introduce). Worse, both runs loaded the record at planning time, so the
later `save_record` overwrites the earlier one from a stale copy; with
different `--include` selections that leaves payload on disk that no
record lists. The archive is on a NAS shared by more than one client
machine, so the second run can be on another host, where a pid means
nothing and attribute caches lag by seconds.

This spec makes a pull **claim its repo** for the duration of the run
with a marker file created by an atomic exclusive create, and refuse a
second pull of the same repo while the claim is held. The claim is
scoped to one repo, so pulls of different repos stay fully parallel. A
marker left behind by a hard kill is treated as stale only when the
tool can prove it (same host, dead pid); from another host it is
reported with its path and left for the human, the spec 0019 posture
that a new automatic delete must clear a higher bar than the problem it
rides with.

## Success criteria

### The claim

1. A pull that will write (anything past `--plan`) creates a marker
   file inside its staging leaf before planning reads the record, via
   `os.open` with `O_CREAT | O_EXCL`. No `exists()` pre-check: on SMB
   and NFS a stat can answer from the client's attribute cache, while an
   exclusive create is executed by the server. The create call *is* the
   test.
   The marker lives at `.staging/<owner>/<repo>/.llm-preserver-pull.json`.
   A hub repo whose selection includes a **root-level** file of that
   name (compared casefolded, for case-insensitive filesystems) is
   refused at selection time, exit 2, under both `pull` and `--plan` —
   staging uses hub paths verbatim, so that file would download over
   the marker. The name does **not** join `TOOL_OWNED_ROOT_FILENAMES`:
   that set is checked against the model-dir-relative path (wrong for
   relocated selections) and only after download (too late), and
   joining it would change `unrecorded_files`, `remove`, and `migrate`
   (decided 2026-10-23, plan round).
2. The marker is JSON: `tool`, `repo_id`, `hostname`, `pid`,
   `started` (UTC ISO-8601). It is validated on read the way the views
   marker is (spec 0002): a marker that is not this shape is never
   treated as stale, never deleted, and the refusal names its path.
3. `--plan` creates no marker and is never refused by one: it writes
   nothing, so it has nothing to protect, and a human asking "what
   would this do?" while a pull runs must get an answer.
4. The marker is released in a `finally` on every exit from
   `pull_model`: success, the 0014 no-op and metadata-only returns,
   every `PullError`, `PullDeclined`, and `KeyboardInterrupt`. Ctrl-C
   mid-transfer therefore leaves no marker, so the spec 0007 resume
   command works unchanged on the next run. On the success path the
   marker leaves **last**: `discard_staging_leaf` deletes every other
   entry in the leaf, then the release unlinks the marker and `rmdir`s
   the leaf. When that cleanup fails (spec 0019), the release still
   removes the marker, so a tidiness failure never reads as a running
   pull. The release unlinks only a marker whose content is still this
   run's own, so a marker swapped in by anyone else survives.
   *Review round, decided by Brian 2026-10-23:* the draft removed the
   marker with the leaf in one `rmtree`, which deletes it first and the
   client's bookkeeping after it — seconds over SMB in which a second
   pull could claim the leaf and lose its in-flight files, and the 0019
   warning would then point at its live directory.

   SIGTERM and SIGHUP during `pull_model` unwind like Ctrl-C: the
   claim is released, staged bytes stay, the resume command is the last
   line when the terminal can still show it, and the exit is 128 + the
   signal number (143, 129). Python runs no `finally` on an unhandled
   signal, so without this a closed terminal or SSH session left a
   marker that every other host refuses on (decided by Brian
   2026-10-23, review round). `kill -9` cannot be caught.

### The refusal

5. A second pull of the same repo, on the same host or another, while
   the marker is held, stops before planning's hub call and before any
   confirmation prompt, with one error line naming the repo, the
   holder's hostname, pid and start time, and the marker path. Exit 3
   (`PullEnvError`, local environment: what stops the run is another
   process, not the arguments). For a typed `--include` /
   `--whole-repo` pull that is before any hub call at all; the
   interactive listing, `--revision` resolution, and the `discover`
   handoff already talked to the hub before `pull_model` runs, and a
   refused interactive run loses its typed selection. Claiming earlier
   would hold the marker while a human browses a listing (decided
   2026-10-23, plan round).
6. A marker whose `hostname` equals this host's and whose `pid` is not
   running (`os.kill(pid, 0)` raises `ProcessLookupError`) is stale:
   the pull replaces it and proceeds, printing one INFO line that it
   cleared a marker left by pid N. A pid that exists but belongs to
   another user (`PermissionError`) is treated as running.
7. A marker from a **different** hostname is never judged stale, however
   old: the run refuses as in criterion 5, and the message says that if
   the other machine's pull is no longer running the human should delete
   the marker file by its printed path. No age-based expiry (the two
   clients' clocks need not agree) and no `--force`.
8. A marker that exists but cannot be read (permissions, `ESTALE`) is a
   refusal with the path, not a traceback (the 0011/0012 class).

### What does not change

9. Two concurrent pulls of **different** repos, including two under the
   same owner, both succeed and both verify `valid`. The second never
   sees the first's marker.
10. Every existing pull test stays green unchanged apart from fakes; a
    pull on a fresh archive produces the same output as today with the
    marker gone by the time the success line prints.
11. `verify --staging` keeps listing a running pull's leaf (it already
    counts the client's bookkeeping); the marker is one more small file
    in that count and needs no special casing.

### Documentation

12. `docs/cli.md` gains a short "Running pulls in parallel" section
    under `pull`: different repos may run concurrently from any number
    of machines; the same repo may not and is refused; the disk-space
    preflight is per run, so two large pulls can jointly overrun a
    volume and the loser exits 3 with a resumable staging leaf; `status`
    and `verify` may show an in-flight model as incomplete until its
    record lands, for longer over a network mount. Exit 3 is added to
    the exit-code table entry for `pull` with this cause.

### Live verification (before the spec is called shipped)

13. Same host, two terminals, the same repo started within a second of
    each other: exactly one proceeds; the other prints criterion 5's
    line.
14. Two client machines against the real NAS archive, same repo: exactly
    one proceeds. This is the only proof that the share's exclusive
    create is server-side for both clients' protocols; the spec assumes
    it and does not cite it.
15. A pull killed with `SIGKILL` on host A, then the resume command on
    host B: refused with the path; after deleting the marker by hand,
    the resume succeeds. Then the same on host A alone: proceeds
    automatically as stale.

## Non-goals

- A cross-run disk budget. The preflight stays per run; the losing pull
  already fails cleanly and resumes. Documented instead (criterion 12).
- The owner-directory `rmdir` race between a finishing pull and a
  starting sibling's `mkdir(parents=True)`. Microsecond window, clean
  exit 3, resumable. Not worth a lock.
- Making `save_record` atomic (tmp then rename). Worth doing, cheap, and
  it would shorten the transient-corruption window a concurrent `status`
  or `verify` can see — but it is a separate idea and ships as its own
  chore, so this diff stays one idea (the spec 0019 lesson).
- Guarding `remove`, `migrate`, or `verify` against a running pull of
  the same repo. `remove` of a repo mid-pull is a real hazard, but it
  is the human's own two commands; queue it in TODO.md as a follow-up
  that can reuse this marker.
- Any `--force` or `--unlock` flag. The remedy for a foreign stale
  marker is `rm` of one printed path; a flag that deletes it is the
  automatic delete this spec declines to add.
- Cross-host liveness. Two machines that share a hostname will judge
  each other's markers by pid and get it wrong; known limit, noted in
  the docs, not solved.

## External references

None. The one outside behavior the design leans on — that `O_EXCL`
create is atomic on the server for SMB and NFS clients of one share —
is proven by criteria 13-15 on the real archive rather than cited.
`os.open` flag semantics are the Python standard library's.

## Notes

- **Where it lives.** A new `pull_claim.py` owns the marker: claim,
  validate, stale check, release. Not `pull_lock.py` — "lock" already
  means the ADR 0001 payload lock (`file_locks.py`). `pull.py` is at
  293 lines against the 300 cap, so the input validators move to
  `pull_inputs.py` (shared by `pull` and `--plan`) to make room.
- **Plan-round decisions (2026-10-23).**
  - The archive-conversion gate (`require_migrated_archive`) runs in
    `pull_model` before the claim, so a refused pull of an unconverted
    archive creates no `.staging/` directories.
  - A repo id that `staging_dir_for` cannot split (an Ollama
    `name:tag`) takes no claim: it cannot reach a write, and skipping
    keeps the 0011/0013 error and hint byte-identical.
  - A holder `pid` outside `1..2**31-1` (or a bool, or not an int) is
    malformed: `os.kill(0, 0)` and `os.kill(-1, 0)` address process
    groups, and an oversized pid raises `OverflowError`.
  - Every holder field is scrubbed with `clean_text` before printing;
    anyone with write access to the share can write a marker.
  - The claim retries `mkdir` + create a bounded number of times on
    `ENOENT`, covering a sibling's owner-directory `rmdir` landing
    between the two calls.
  - Stale takeover re-reads the marker, confirms it unchanged, unlinks,
    and re-creates with `O_EXCL`; on `EEXIST` it re-evaluates once,
    then refuses. Two same-host crash-recoverers racing inside that
    window can both proceed — accepted, like pid reuse.
  - macOS `gethostname()` can change with the network; the failure is
    a refusal the human clears by deleting the file. Documented beside
    the same-hostname limit.
- **Claim before planning, not before transfer.** Claiming only at
  `on_transfer_start` would let a second run plan against a record the
  first is about to rewrite, then claim after the first releases, and
  overwrite it from the stale copy — the exact harm. The cost is that
  the marker is held while a human sits at the size or every-weight
  prompt; the refusal is still true then ("another pull of this repo is
  in progress").
- **Partial-write window.** A reader that opens the marker between the
  exclusive create and the content write sees an empty file, which
  criterion 2 treats as malformed and refuses. Safe side.
- **pid reuse.** A stale marker whose pid has been recycled by an
  unrelated process on the same host is judged running. The human
  deletes the file; the message says how. Accepted.
- **Test shape.** Hermetic tests pre-plant markers (own pid, a dead
  pid, a foreign hostname, malformed, unreadable) against a tmp archive
  and the fake hub client, and assert release on each exit path by
  checking the leaf afterward. The guard is mutation-proved: with the
  claim deleted, the same-repo test must fail. True two-process
  contention is criteria 13-15, live.
- **Read-only `.staging/`** (decided by Brian 2026-10-23, review
  round). The claim is created before planning knows whether the pull
  will write, so a re-pull with nothing new to download now fails at
  exit 3 when `.staging/` is not writable, where spec 0014 used to
  finish without writing. Accepted and documented: falling back to an
  unclaimed pull on `EACCES`/`EROFS` would let adopt-only and
  metadata-only writes run unguarded.
- **Queued elsewhere.** Atomic `save_record`; `remove` honoring the
  marker. Both go to TODO.md on this branch.
