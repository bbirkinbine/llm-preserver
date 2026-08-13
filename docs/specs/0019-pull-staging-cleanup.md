# 0019 — Pull Staging Cleanup

**Status:** shipping
**Last updated:** 2026-08-12

## Goal

A pull that fully succeeds can still report a failure and leave staging
residue behind. `pull.py` deletes the staging leaf as its last act
*inside* the same `try` whose `except OSError` raises `PullEnvError`
(fault domain `local environment`, exit 3) — and that delete runs
**after** `write_manifest` and `save_record`, so the archive is already
complete, recorded, and durable at the moment it fires:

```python
write_manifest(prep.model_dir, record)
save_record(record, prep.model_dir)
if prep.plan.to_download:
    # Staging now holds only the client's .cache/huggingface
    # bookkeeping, which must never reach the archive; drop it.
    shutil.rmtree(prep.staging_dir)
```

Deleting bookkeeping is not part of preserving a model. Making it a
failure condition inverts the tool's central promise: it tells the human
their pull failed when the bytes, the hashes, the record, and the
manifest all landed.

The live trigger (2026-08-12) was a 160 GiB whole-repo snapshot of
`Qwen/Qwen3-Coder-Next` that ran 23:32→04:58, archived all 40 shards,
wrote all three record files at 04:58 — and left
`.staging/Qwen/Qwen3-Coder-Next/.cache/huggingface/download/` holding 21
zero-byte `.lock` files and 21 ~124-byte `.metadata` sidecars. 2.4 KiB,
no payload. A later `verify --staging` then reported it as
`Qwen/Qwen3-Coder-Next  2.4 KiB, 42 partial files`, which reads as an
abandoned download and is not one.

Two failure modes compound here. **This spec fixes the first and
deliberately declines the second** — see the Non-goals, where the
residue-clearing half is cut with the reproduction that killed it:

1. **The pull is reported as failed.** Confirmed by code path, not
   inferred: the installed CLI is an editable install of this tree
   (`llm_preserver.pth` → `…/llm-preserver/src`), `save_record` wrote
   `MODEL-RECORD.md` at 04:58 so execution reached the `if`, and 40
   shards downloaded means `to_download` was non-empty. The `rmtree`
   ran and raised.
2. **The residue is unreachable.** The cleanup is gated on
   `prep.plan.to_download`, and re-pulling an already-complete model
   returns early on the no-op path (spec 0014) long before that gate.
   So the leaf can never be cleaned by the tool that made it. `remove`
   is not the answer — it would delete the archived model. The residue
   is permanent, and `verify --staging` misreports it forever. This
   remains true after this spec: the fix makes the misreport *honest*
   (documented, warned about, and pinned by a test) rather than making
   it go away, because every safe-looking way to make it go away
   automatically turned out to be able to kill a running pull.

Root cause, reproduced on the live share and then confirmed against the
host's kernel log (2026-08-12). Reproduction first: a `shutil.rmtree`
over this exact directory shape succeeds 10/10 on the SMB mount, but
with **one file descriptor still open** on a file inside it fails with
`OSError errno=66 (ENOTEMPTY) Directory not empty` on the `download/`
directory. macOS `smbfs` implements delete-on-open by renaming the open
file to a hidden `.smbdelete…` placeholder rather than unlinking it, so
the parent `rmdir` still sees a non-empty directory.

The unified log then supplies the specific descriptor and the specific
event, so none of this is inferred:

```
04:42:13  DarkWake from Deep Idle [CDNPB] : due to ... rtc/Maintenance
04:42:13  (smbfs) smb_iod_start_reconnect: id 53 start reconnect.
04:44:13  (smbfs) smb_iod_sendrq: id 53: SMB_TRAN_FATAL returned error. Reconnect.
04:44:16  (smbfs) smb2fs_reconnect_dur_handle: Warning: Could not reopen
          durable handle for model-00026-of-00040.safetensors.lock
04:44:16  (smbfs) smb2fs_reconnect: smb2fs_reconnect_dur_handle failed <2>
          for sharedFID BRL <0> on <model-00026-of-00040.safetensors.lock>
```

The host dark-woke from deep idle mid-transfer, the SMB session
reconnected twice, and the reconnect could not reopen the **durable
handle on a `.lock` file** — confirming that the client does hold those
handles open for the process lifetime. The residue matches exactly:
it begins at shard 26, the file named in the log, whose `.lock` carries
an 04:42 mtime. `rmtree` deleted shards 1–25's sidecars in order, reached
the file with the broken handle, and raised — leaving the 21 pairs that
follow it. Nothing is logged at 04:58, consistent with an `ENOTEMPTY`
returned by the server rather than a kernel-level fault.

## Success criteria

- **Staging cleanup is never a pull failure.** It moves out of the
  archive-writing `try`, to a point where the pull has already
  succeeded. On `OSError` it logs a single WARNING naming the leaf path
  and the errno, and the pull exits **0**. A pull that archived every
  byte, wrote the record, and merely failed to delete bookkeeping is a
  successful pull, and its exit code says so.
- **The warning is actionable, not alarming.** It states that the
  archive is complete and that what remains is client bookkeeping, so
  the human reading it does not go looking for a damaged model.
- **Residue is left for a human, and nothing pretends otherwise.**
  Nothing in the tool removes a staging leaf except the pull that
  filled it and `remove`. A leaf that outlives its pull therefore
  outlives it until a human deletes it, `verify --staging` keeps
  listing it, and the warning says so rather than promising a
  self-heal. Pinned by a test, so the cut below cannot be quietly
  reversed.
- **Only a pull that downloaded something deletes a staging leaf.** An
  adopt-only pull (files already on disk, record catching up) moved no
  bytes into that leaf and has no standing to remove one, which may
  hold a differently-scoped `--include` pull's staged bytes. Pinned by
  a test that fails when the gate is removed.
- **An emptied creator directory goes with the leaf**, via `os.rmdir`
  and never `rmtree` (the spec 0017 rule). `os.rmdir` refuses a
  non-empty directory, which is the safety property being relied on —
  a sibling model's staging is never at risk. A failure to remove it is
  silent; an empty directory is not worth a warning.
- **Cleanup failure leaves the archive untouched.** The leaf lives under
  `.staging/`, never inside `models/`, so no partial cleanup can reach
  archived payload. Regression-tested.
- **Tests prove the pull-succeeds-anyway path directly**, with the
  `rmtree` failure injected (a real `OSError(ENOTEMPTY)`), asserting
  exit 0, the record and manifest intact on disk, the payload byte for
  byte, and the warning emitted exactly once.

## Non-goals

- **No change to `verify --staging`.** Its counting rule — everything
  under the leaf, hf's `.cache/` bookkeeping included — was adjudicated
  in spec 0012 (2026-07-19) precisely so a single large file interrupted
  mid-download, whose only bytes live in `.cache/…/*.incomplete`, cannot
  hide. That rule stands, and residue is counted by it like any other
  leftover until a human clears it.
- **No automatic clearing of residue — cut at review, 2026-08-13.**
  The draft had the spec 0014 no-op path delete a leaf that held
  nothing but `.cache/` bookkeeping, which would have made the live
  `Qwen/Qwen3-Coder-Next` residue self-healing. The adversarial review
  killed it with a reproduction: **huggingface_hub writes its
  `.cache/huggingface/` scaffolding (`.gitignore`, `CACHEDIR.TAG`)
  before its first network round trip**, so a *running* pull's staging
  leaf is byte-for-byte indistinguishable from dead residue during
  that window. A second `pull` in another terminal deleted the live
  leaf and killed the first pull with `PullEnvError` — the very exit 3
  this spec exists to remove. The warning text made it worse by
  inviting that second terminal. Three findings compounded it: a hub
  repo may ship its own `.cache/` directory (the tool archives
  `gguf/.cache/params.json`, so it stages one too); the `.incomplete`
  protection the guard leaned on is largely vestigial in
  huggingface_hub 1.24.0, which unlinks partials in a `finally` and
  never resumes; and the recovery needed the hub, so an offline
  re-pull could not clear residue for a repo that is by definition
  already archived. Clearing residue belongs in an explicit,
  human-present verb — TODO → `verify --staging --clean`.
- **No retry of the delete.** The confirmed failure is a broken durable
  handle on a `.lock` file the client holds for the process lifetime —
  it does not heal while that process runs, so an in-process retry
  cannot succeed. Nor is a later pull the retry, now that the automatic
  clear is gone.
- **No guard on the successful-pull delete.** That path still removes
  the whole leaf, so a successful `--include '*Q4*'` pull discards a
  parked `*Q8*` download staged beside it. Both reviewers reproduced
  it; it is **pre-existing**, identical on `main`, and unrelated to the
  bug this spec fixes. Queued in TODO with its reproduction rather than
  widened into a diff that is otherwise one idea.
- **No cleanup at pull start.** Staging at that moment is a resume
  source; deleting it would discard exactly the partial bytes spec 0012
  exists to surface and `pull` exists to reuse.
- **No sweep of the eight pre-existing empty creator directories** under
  `.staging/`. They cost nothing, `staging_leftovers` already ignores
  them (a leaf with no regular file is not a leftover), and a
  standalone sweep verb is a bigger surface than this bug earns.
- **No change to `remove`.** It already clears staging-only leftovers.

## External references

**None encoded in shipped code.** The draft carried two
huggingface_hub layout constants (`.cache` as the bookkeeping
directory, `.incomplete` as the in-flight suffix) inside the residue
guard — external-authority values that would have required pinned
provenance under `.claude/rules/python-code.md`, and that a Dependabot
bump could have invalidated silently while every test stayed green,
because the tests built the same shape from the same two literals.
Cutting the guard removed both, so no shipped value depends on the
library's local-dir layout.

The library-behavior claims that remain live in this spec's Non-goals
as *rationale for not building something*, and were read from the
installed dependency at the version this branch locks
(huggingface-hub 1.24.0, MIT): the pre-network `.cache/huggingface/`
scaffolding, the `finally`-unlink of partials, and the process-lifetime
`.lock` handles. Nothing in `src/` asserts them. If the residue-clearing
verb is ever built (TODO → `verify --staging --clean`), it inherits the
provenance requirement in full, and a hermetic tripwire asserting
against hf's own `get_local_download_paths` — rather than a hand-built
fixture — is the way to keep it honest.

## Notes

- Measured on the live archive 2026-08-12, read-only: 42 files,
  2.4 KiB, zero payload bytes, no `*.incomplete`. Eight of the nine
  `.staging/<creator>/` directories are already empty, so the
  successful-cleanup path is the norm and this is the exception.
- The archive sits on a NAS share mounted over SMB, with ample free
  space, so `ENOSPC` is excluded. No BSD file flags are set on the
  residue, so
  this is **not** the ADR 0001 immutable-flag class of failure that
  spec 0017 hit — that one bit `move`, this one bits `rmdir`.
- That 04:58 pull must have ended with `local environment` / exit 3 —
  the code path admits no other outcome — but the scrollback is gone and
  the kernel log records nothing at delete time, so it was not directly
  observed. The diagnosis does not rest on it.
- The trigger was a host **dark wake from deep idle** during a 5.5-hour
  overnight transfer, not a flaky link. That is worth knowing
  independently of this bug: long pulls to a network share are exposed
  to the host's own sleep schedule, and the payload survived here only
  because the transfer layer retried. A `caffeinate`-style hold for the
  duration of a long pull is a plausible follow-up, but it is a
  behavior change to `pull` and belongs in its own spec, not this one.
- The same ordering lesson as spec 0017's two mid-branch corrections
  applies, in a new place: there, the alarm must not precede the fix;
  here, **a cleanup step must not be able to fail the operation it
  cleans up after.** Worth a standing rule if a third instance appears.
