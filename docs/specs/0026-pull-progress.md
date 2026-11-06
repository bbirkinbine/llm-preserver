# 0026 — Pull Progress

**Status:** draft
**Last updated:** 2026-11-06

Tracked as GitHub issue #70; the issue body is the source of this
draft.

## Goal

Agents archive models as often as humans do: once a model or quant has
proven itself in use, an agent runs `llm-preserver pull` to keep it. In
that setting stdout and stderr are not a TTY, and a pull reports only
one `downloading n of m: <path>` line per file
(`src/llm_preserver/pull_transfer.py`). The Hugging Face client's own
progress bar redraws in place for a live terminal; captured as text it
is noise or nothing. A single multi-GiB shard is therefore one line
followed by minutes of silence. The agent cannot tell a healthy
transfer from a stall, and may kill a pull that was fine. When the pull
ends there is no total of bytes, elapsed time or rate to report back.

Add an opt-in `pull --progress` flag that makes a long pull legible
from plain captured text: periodic self-contained progress lines while
bytes move, and one closing summary line. Default output does not
change: the repo's standing rule is that non-TTY output stays
byte-identical to a progress-free run (`verify`, `remove`), so scripted
and cron output stays deterministic.

## Success criteria

1. With `--progress`, a pull prints one plain-text line at a fixed
   interval while a transfer is in flight. Each line stands alone and
   carries: bytes done of the whole pull's total, transfer rate,
   estimated time remaining, and the file in flight as `n of m` with
   its path. No carriage returns and no terminal control sequences, so
   the line reads the same in a log file, a pipe, or an agent
   transcript. Lines print every 30 seconds (fixed, not configurable)
   and go to stderr, beside the existing `downloading n of m` lines.
   Lines go through the package logger like `downloading n of m`, so
   they carry the same `LEVEL logger:` prefix. Shape, with sizes from
   the existing `human_size` renderer:
   `INFO llm_preserver.pull_progress: progress: 12.3 GiB of 45.6 GiB (27%), 85.2 MiB/s, ~6m40s left, downloading 3 of 12: <path>`
   While a downloaded file is being hashed, the same line continues,
   names the phase and shows how far the hash has read
   (`hashing 3 of 12 (41%): <path>`), so a multi-minute hash of a large
   shard on a slow share can be told apart from a stuck one.
2. The whole-pull total comes from the hub-declared sizes the plan
   already holds. When any planned file has no declared size, the line
   marks the total as a floor (`of at least 45.6 GiB`, the wording
   spec 0018 uses for the same case) and omits the time estimate
   rather than present a guess as exact.
3. With `--progress`, a completed pull ends with one summary line:
   bytes received over the network, file bytes archived, elapsed time,
   and average rate. Progress lines count network bytes against the
   hub-declared total, so on Xet (which can receive fewer bytes than
   the file holds) the last line may read below 100%; the summary's two
   byte counts make that visible rather than hide it. A reused file's
   declared size counts as done when it returns, so a resumed pull's
   percentage is not low for its whole run. A file counts as reused
   only when it was already in staging at its declared size before the
   download *and* the download moved zero bytes; zero bytes alone is
   not enough, because a client that stopped reporting would then make
   every file read as reused. Bytes that did
   not cross the network (files reused from a previous attempt's
   staging, or adopted because an unrecorded on-disk file already
   matched the hub hash) are counted separately from bytes transferred.
4. Without `--progress`, output is byte-identical to today, on a TTY
   and off one, and `HubClient.download` makes exactly today's client
   call (no `tqdm_class`). Off a TTY, a differential test pins it: the
   same pull with and without the flag differs only by the added
   progress and summary lines.
5. `--progress` output is the same whether or not stdout is a TTY. On
   a TTY it replaces the client's own progress bars rather than adding
   to them: with the flag, no client bar renders, and the plain lines
   are the only progress shown (decided 2026-11-06).
6. Progress reporting never changes the exit code, the record, the
   manifest, or which bytes reach the archive. A failure to measure
   (the client stops calling `update_transfer`, or the handler meets
   an unexpected value) degrades to fewer or less precise lines, never
   a traceback and never a failed download.
7. An interrupted pull that used `--progress` prints a resume command
   that carries `--progress` (spec 0007's hint, the same way
   `--hf-logging` rides along per spec 0008).
8. `discover`, which hands off to the same pull flow, accepts
   `--progress` and passes it through.
9. `docs/cli.md` documents the flag, the line format, and that it is
   the intended mode for agent-driven pulls. The format is documented
   for humans and agents to read, not promised as a stable
   machine-parseable contract.

## Non-goals

- Changing default output, on a TTY or off one.
- A TUI or richer TTY rendering.
- Progress for `verify` or `remove`; they already have TTY progress.
- Throttling or bandwidth control.
- Changing how files are downloaded, staged, hashed or moved.

## Notes

- **Measurement seam: `tqdm_class`, not file-size sampling** (settled
  2026-11-06 from the installed source and real downloads; see
  `## External references`). The issue's candidate, sampling the
  partial file's size, fails on the path that matters: for Xet-backed
  files (most weights) the `.incomplete` file sat at 0 bytes for the
  whole transfer, or jumped in ~64 MB bursts with a reported size
  below what was written, which is the "is it stalled?" signal this
  feature exists to give. `hf_hub_download` instead takes a public
  `tqdm_class` argument. A class defining `update_transfer(n)` receives
  network bytes about every 100 ms on both Xet and plain HTTP, off a
  TTY too. `HubClient.download` passes such a class only when
  `--progress` is set, so without the flag the client call is
  unchanged (criterion 4).
  Constraints the plan must honor:
  - `update_transfer` is a duck-typed convention found by `getattr`,
    not a documented API. If a release drops it, nothing errors; the
    class silently gets only the coarse per-file `update`. A contract
    test against the installed client pins it, like spec 0022's
    adapter tests, and must pass at both the 1.26.0 floor and the lock.
  - The handler runs inside hf_xet's callback and must never raise;
    it updates a counter under a lock and a timer thread prints.
  - Increments can be negative (a retry restarts with `Range`).
  - On Xet, network bytes need not sum to the file size (dedup or
    compression): a run ended at 143.8 MB received for a 144.8 MB file.
    Progress against the hub-declared total is therefore approximate
    and may finish below 100% (criterion 3).
- **TTY bars under `--progress`: suppressed** (Brian, 2026-11-06;
  criterion 5). Passing `tqdm_class` replaces the client's bar, so
  some visible change on a TTY is unavoidable. The two choices were a
  vanilla-`tqdm` subclass with `disable=True` (no client bar renders)
  or a subclass of the client's own `tqdm` (its bar keeps rendering,
  but defining `update_transfer` turns Xet's two bars into one with a
  transfer postfix). Suppression won: `--progress` output is then the
  same on a TTY and off one, and the flag is only passed by someone
  who wants the plain lines.
- **Reuse detection.** A previous attempt's staged file is reused
  inside `hf_hub_download` (local-dir metadata), so the tool does not
  see the decision. Under the `tqdm_class` seam, a file that returned
  with zero transfer bytes is the observable sign, cross-checked
  against staging holding the file at its declared size beforehand
  (criterion 3; added at the plan checkpoint, because zero bytes alone
  also describes a client that stopped calling `update_transfer`, or a
  Xet chunk-cache hit).
- **Hashing is in scope** (criterion 1). After each download the tool
  hashes the file (`sha256_of`), which takes minutes on a multi-GiB
  shard with no output in between; the interval line continues through
  it and names the phase. The hash seam already takes a `progress`
  kwarg (spec 0009's verify byte counter), which is the likely source.
- Settled 2026-11-06 with the draft's other calls: 30-second fixed
  interval (a flag can come later if a real run asks for one); stderr,
  beside `downloading n of m`; format documented but not a parsing
  contract.
- Timer thread: sampling from a background thread must stop cleanly on
  success, failure, Ctrl-C (exit 130), and the spec 0025 signal unwind,
  without delaying any of them.

## External references

- **huggingface_hub 1.33.0** (the lock; Apache-2.0), installed source,
  read 2026-11-06:
  - `hf_hub_download(..., tqdm_class: type[base_tqdm] | None = None)`
    is passed through to `http_get` and `xet_get`
    (`file_download.py:342`, `:453`, `:481`, `:505`, `:585`).
  - `_create_progress_bar` (`utils/tqdm.py:324-343`) builds a class
    that is not a subclass of the client's `tqdm` as `cls(**kwargs)`
    and applies none of the client's disable signals.
  - `update_transfer` is looked up with `getattr`: plain HTTP per
    10 MiB chunk (`file_download.py:462-463`), negative on a ranged
    retry (`:415-416`), Xet via `utils/_xet_progress_reporting.py:88-91`
    and `:151-156`.
- **huggingface_hub 1.26.0** (the support floor from spec 0022): the
  same `update_transfer` handling is present, checked in an isolated
  install on 2026-11-06.
- **Real downloads** (2026-11-06, scratch dirs, non-TTY):
  `unsloth/SmolLM2-135M-Instruct-GGUF` `Q2_K.gguf` (88.2 MB) and
  `Q8_0.gguf` (144.8 MB) over Xet, and `Q2_K.gguf` with
  `HF_HUB_DISABLE_XET=1` over plain HTTP. A probe class received
  `update_transfer` about every 100 ms on Xet and per 10 MiB chunk on
  HTTP, and printed nothing to stdout or stderr.

## Implementation plan

Approved by Brian 2026-11-06 at the plan checkpoint, including the
file count (about 33: 17 src, 3 config/CI, 7 tests, 6 docs). One
branch, checkpointed passes; a phase handoff is appended here at
each session boundary.

1. **Neutral pre-work.** Extract the `except PullError` hint body from
   `cli/pull_exec/flow.py` into `cli/pull_exec/error_hints.py` (suite
   green with no test changes; `flow.py` would otherwise near the
   cap). Add `tqdm` as a direct runtime dependency and `types-tqdm` as
   a dev dependency, through `dependency-hygiene`.
2. **Meter and render (pure).** `pull_progress/meter.py` holds one
   lock-guarded state object (declared total and floor flag, phase,
   `n of m`, signed network bytes, archived / reused / adopted bytes,
   hash bytes, injected clock); its byte handler never raises.
   `pull_progress/render.py` renders the line and summary.
3. **Hub seam and contract.** `hub/progress_bridge.py` builds a
   vanilla-`tqdm` subclass (`disable=True`, no monitor thread) whose
   `update_transfer` forwards to a `TransferSink` protocol in
   `hub/types.py`. `HubClient.download` passes `tqdm_class` only when
   a sink is given. Offline contract test drives the real client
   through an httpx mock transport, at the lock and at the 1.26.0
   floor, and joins the CI compatibility matrix.
4. **Core wiring.** `pull_transfer` and `pull_model` take the meter;
   the reuse cross-check and hash progress land here. A seam test pins
   that a pull without the flag calls `download` with no `progress`
   key.
5. **CLI.** `--progress` on `pull` and `discover` (refused with
   `--match-ollama`); a daemon ticker thread inside the spec 0025
   unwind, stopped in `finally` before the resume hint prints; resume,
   `--plan` follow-up and doc-refresh commands carry the flag; the
   summary prints only when a transfer started.
6. **Docs, live check, review.** `docs/cli.md` (flag, an "Agent-driven
   pulls" section), `docs/hf-client-maintenance.md`. Live runs in a
   scratch archive with a file that takes over 30 seconds: Xet piped,
   `HF_HUB_DISABLE_XET=1`, and a real TTY. Weekly live canary gains a
   small commit-pinned Xet-backed download through the sink, since Xet
   has no offline coverage. Then `/review-check`, `/review`,
   `/review-adversarial`.
7. **Close-out** on the branch once the PR is open.

Plan-checkpoint decisions (2026-11-06): a reused file's size counts as
done; reuse needs the staging cross-check, not zero bytes alone; lines
go through the logger as `llm_preserver.pull_progress`; the hashing
line shows hash percentage. Smaller defaults taken as planned: the rate
is per 30-second window (a stall reads 0 B/s) and the estimate uses the
average since start; no percentage when the total is a floor; the first
line prints one interval after the transfer starts, never during
prompts; `--plan --progress` prints no lines but its follow-up command
carries the flag.

## Phase handoff (2026-11-06, after pass 3)

Passes 1-3 done on `spec-0026-pull-progress`; gate green (ruff, mypy
native and `--platform linux`, 1925 passed / 3 skipped; the progress
contract plus the existing compatibility tests pass at the 1.26.0 floor
via `uv run --with huggingface-hub==1.26.0`). Nothing is wired into
`pull` yet, so no user-visible behavior has changed.

- **Pass 1:** `cli/pull_exec/error_hints.py` extracted from `flow.py`
  (271 -> 248 lines, no test changes). `tqdm>=4.42.1` runtime;
  `types-tqdm>=4.66,<4.69` dev, held to the runtime 4.68 series (the
  newest stub build was dated the same day and targets 4.70; the 4.68
  stubs pull `types-requests` + `urllib3`, dev-only). `pip-audit`: no
  known vulnerabilities.
- **Pass 2:** `pull_progress/` (`meter.py` 226, `render.py` 114);
  54 tests in `test_pull_progress_meter.py` / `_render.py`. Calls the
  test round left open, settled in code: the time estimate's average
  uses `done_bytes`; a zero-length window reads `0 B/s`; durations
  round to the nearest second; `reused 1 file` is singular. The hash
  count resets only in `start_hash` (a second reset in `start_file`
  made the guard unprovable; removing it made the mutant fail).
- **Pass 3:** `hub/progress_bridge.py` (vanilla-tqdm subclass,
  `disable=True`, `monitor_interval = 0`, `update_transfer` guarded so
  a sink exception cannot reach the client), `TransferSink` in
  `hub/types.py`, `HubClient.download(progress=None)` with the
  no-progress call byte-identical. `test_hf_client_progress_contract.py`
  (16 tests) drives the real client offline through
  `set_client_factory` + `httpx.MockTransport`; added to the CI
  compatibility matrix. Mutation-proved: bar not disabled, monitor
  thread allowed, sink exception escaping, `update_transfer` renamed,
  `tqdm_class` always passed.
- **Known gap carried forward:** the bridge's keyword test cannot
  catch a bridge that forwards `**kwargs` to tqdm, because a disabled
  tqdm returns before validating them. Xet has no offline coverage
  (pass 6's live canary).

**Resume at pass 4** (core wiring): `pull_transfer.download_and_archive`
and `pull_model` take the meter; `start_file` gets `already_staged`
from the per-file check behind `pull_preflight.already_staged_bytes`;
`sha256_of(progress=meter.add_hash_bytes)`; `FakeHubClient.download`
in `tests/conftest.py` gains `progress=None`. The seam test must pin
that a pull without the flag calls `download` with no `progress` key
at all. Then pass 5 (CLI), 6 (docs, live runs, review), 7 (close-out).
