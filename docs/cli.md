# CLI usage

The user-facing manual for the `llm-preserver` command line. This
document grows with the tool: every feature branch that adds or
changes a command updates it in the same change. `--help` on any
command is generated from the same source and is always current:

```bash
uv run llm-preserver --help        # -h works everywhere too
uv run llm-preserver pull -h
```

Two top-level options come from the CLI framework (Typer) rather than
a feature spec: `--install-completion` wires shell tab-completion for
`llm-preserver` into the current shell (command and flag names complete
with TAB afterwards), and `--show-completion` prints that completion
script instead of installing it, for manual setup.

Commands documented here: `init`, `pull` (selective, `--whole-repo`
full snapshot, and `--plan` dry run), `discover`, `status`, `show`,
`verify`, `remove`, `views`. Planned features (cache import, smoke
tests) are listed in the roadmap in
[`specs/0000-product.md`](specs/0000-product.md)
and appear here when they ship.

## Environment variables

Everything is optional — every path can be given explicitly — but a
configured shell makes most commands zero-argument. The first two are
read by `llm-preserver`; the `OLLAMA_*` pair is read by Ollama and
only ever *printed* by this tool:

| Variable | Read by | Purpose |
| --- | --- | --- |
| `LLM_PRESERVER_ARCHIVE` | llm-preserver | Default archive root; the trailing path argument on every command falls back to it. Explicit path wins. |
| `LLM_PRESERVER_VIEWS` | llm-preserver | Views root for `views --dest` fallback: the dest becomes `$LLM_PRESERVER_VIEWS/<tool>` (one subdirectory per runtime). Created automatically on first `--seed-store`; keep it *outside* the archive — a dest inside the archive is refused (Ollama needs read-write on its store, and the archive is never handed out writable). Local disk is ideal: the view is kilobytes. Explicit `--dest` wins. |
| `OLLAMA_MODELS` | ollama | Points Ollama's model store at the *generated view* (never at the archive). Read at `ollama serve` startup. |
| `OLLAMA_NOPRUNE=1` | ollama | Disables Ollama's startup prune, which would delete seeded blob links it considers unreferenced. Set it whenever serving a seeded view. |

A one-time setup for the daily loop:

```bash
# ~/.zshrc
export LLM_PRESERVER_ARCHIVE=~/models
export LLM_PRESERVER_VIEWS=~/llm-views

# then:
llm-preserver views --seed-store
OLLAMA_MODELS=$LLM_PRESERVER_VIEWS/ollama OLLAMA_NOPRUNE=1 ollama serve
```

## init — create an archive

```bash
uv run llm-preserver init ~/models
```

Creates the archive skeleton at the given path: a marker file
(`archive.json`, carrying the archive schema version) and the
`models/` tree. Idempotent — re-running against an existing archive is
a no-op. Every other command takes this path as its *last* argument
and refuses to operate on a directory that is not an initialized
archive.

The path argument on every command falls back to the
`LLM_PRESERVER_ARCHIVE` environment variable, so a one-archive setup
can export it once (e.g. in `~/.zshrc`) and omit the path everywhere:

```bash
export LLM_PRESERVER_ARCHIVE=~/models
uv run llm-preserver status            # no path needed
uv run llm-preserver pull unsloth/Qwen3.6-27B-MTP-GGUF --include '*Q8_0*'
```

An explicit path always overrides the variable (useful for a second
archive). There is no config file — the archive directory itself is
the only state. When the variable isn't set, `init` prints the exact
`export` line for the archive it just created.

The archive layout and record schema are described in
[`data-structures.md`](data-structures.md).

## Choosing what to pull

If you're used to `ollama pull`, note what it does silently: picks a
quantization for you (typically Q4_K_M) and discards the license,
model card, and source linkage. This tool asks you to make that one
choice explicitly, because the answer is part of what gets preserved.
The map:

- **Original repo** (`Qwen/Qwen3.6-27B`) — the
  full-precision weights, roughly 2GB per billion parameters. Archive
  these when the model matters enough to keep its source of truth;
  they are not what desktop runtimes load.
- **Quant repo** (`unsloth/Qwen3.6-27B-MTP-GGUF`) — runnable compressed
  conversions of an original. A quant repo holds many files; pull
  *one* that fits your hardware, never all of them.
- **Quant label** — the size/quality dial, encoded in the filename.
  `Q4_K_M` is the common default (what Ollama usually picks);
  `Q5`/`Q6`/`Q8_0` trade more memory for quality. A file's size on
  disk approximates what it needs in RAM/VRAM, plus headroom for
  context.

When unsure, run `pull <repo-id>` with no `--include`: the file
listing with sizes *is* the decision aid.

**Archiving for a goal.** What a model needs in the archive depends
on what you want to still be able to do with it later:

| Goal | Archive this | How |
| --- | --- | --- |
| Run it locally | one quant that fits your hardware | `pull <quant-repo> --include '*Q4_K_M*'` |
| Re-make any quant later, offline | the repo's bf16/f16 GGUF plus its `*imatrix*` file | `pull <quant-repo> --include '*bf16*,*imatrix*'` |
| Fine-tune it later | the model's own full-precision safetensors | `pull <original-repo> --whole-repo` |

The three compose: a quant for today, bf16+imatrix for quant
independence, the safetensors master for training. The
full-precision-master advisory names the exact `--whole-repo`
command whenever a quant pull leaves the third row uncovered.

[`what-to-archive.md`](what-to-archive.md) walks the same three rows
end to end with a real model — why a quant is a one-way export, how to
check which models on your shelf have a full-precision source, and the
`llama.cpp` commands that turn one into any other quant offline.

## pull — download files from a Hugging Face repo

Running example: `unsloth/Qwen3.6-27B-MTP-GGUF`, a real quant repo
holding ~25 GGUF quantizations of Qwen3.6-27B plus vision projectors
(`mmproj-*.gguf`).

```bash
# interactive: lists the repo's files with sizes, prompts for patterns
uv run llm-preserver pull unsloth/Qwen3.6-27B-MTP-GGUF ~/models

# 8-bit (~29GB — needs a large-memory machine):
uv run llm-preserver pull unsloth/Qwen3.6-27B-MTP-GGUF ~/models --include '*Q8_0*'
# → confirms grouping under Qwen/Qwen3.6-27B (from the repo's
#   base_model metadata), then downloads Qwen3.6-27B-Q8_0.gguf

# later, add the 4-bit for a 24GB GPU — merges into the same model
# directory and record; already-archived files are skipped:
uv run llm-preserver pull unsloth/Qwen3.6-27B-MTP-GGUF ~/models --include '*Q4_K_M*'

# multimodal models: pull a vision projector alongside the weights
uv run llm-preserver pull unsloth/Qwen3.6-27B-MTP-GGUF ~/models \
    --include '*Q8_0*' --include 'mmproj-F16*'

# skip the grouping confirmation with an explicit target:
uv run llm-preserver pull unsloth/Qwen3.6-27B-MTP-GGUF ~/models \
    --include '*Q8_0*'
```

The trailing archive path is optional whenever `LLM_PRESERVER_ARCHIVE`
is set (see the init section) — with the variable exported, every
example above works with the path omitted entirely.

`REPO_ID` is an exact hub repo id (`namespace/repo`) — the tool never
resolves fuzzy names. A value that is not a valid hub repo id — an
Ollama `name:tag` reference (`qwen3-vl:30b-a3b-instruct`) pasted in by
habit is the common case — is rejected with a one-line error and
exit 2, not a stack trace; search the hub by name with the `discover`
command to find the exact id. When the rejected value is
Ollama-shaped, the error adds the recovery command: an Ollama
`name:tag` points at `discover <name>` and
`discover --match-ollama <name:tag>` (see the discover section), and
Ollama's `hf.co/<org>/<repo>[:<quant>]` pull syntax gets its exact
mechanical translation (`pull <org>/<repo>`, the quant shown as an
`--include` hint). Detection lives in the error path only — no id is
ever rewritten or auto-corrected, and the exit code stays 2. The pull downloads the selected files plus the
repo's README/model card and LICENSE, records a SHA256 for every file,
pins the resolved commit hash, and writes the model record
(`model-record.json` + rendered `MODEL-RECORD.md`). Archived payload
files are made read-only and covered by a per-model
`manifest-sha256.txt`.

Weights land at `<format>/<filename>`; documentation files (README /
model card / LICENSE / use-policy) land under
`<format>/docs/<source-repo>/` (e.g.
`gguf/docs/unsloth--Qwen3.6-27B-MTP-GGUF/README.md`), so
docs from two source repos of the same format can never collide.

Options:

- `--include PATTERN` — fnmatch file selection; repeatable, patterns
  union. Case-sensitive. A selection that matches no weight/artifact
  files is an error, not a docs-only pull.
- `--whole-repo` — full snapshot: download the named repo's whole
  tree (see the dedicated section below). The scope is that one repo —
  it never crosses repos (an advisory names the follow-up pull when a
  related repo matters). Mutually exclusive with `--include`.
- `--base-model OWNER/REPO` — record this repo's lineage: the model it
  derives from. Affects the **record only** — never where the files
  land. Use it when the repo's card declares no base, or declares one
  you can see is stale. The record notes who made the claim
  (`base_model_source`: the card, you, or a migration), so a lineage
  line can always be audited back to its source. `status` groups the
  shelf by it and `show` reads it both directions.

  > `--model` is **gone** (ADR 0003). It used to name the directory a
  > pull landed in; a pull now lands in the directory named by the repo
  > id you type, so there is nothing left to choose. Passing it exits 2
  > with the replacement command. The half of its job worth keeping —
  > asserting lineage — is `--base-model`.
- `--role ROLE` — assign a curator role (`chat`, `coding`,
  `embedding`, `reranker`, `multimodal`) at pull time; repeatable.
  Without it the model is archived role-less and shows under
  "(no role)" in `status` until you assign one.
- `--refresh-docs` — replace documentation files whose upstream
  content changed: the superseded doc is unlocked, replaced with the
  newly downloaded and hashed version, re-locked, and the record and
  manifest are updated. Applies to doc paths only — a changed
  *weight* is always a hard stop, flag or no flag.
- `--plan` — dry run: print what the pull would do, then exit without
  downloading or writing (see the dedicated section below).
- `--yes` — auto-accept the size confirmation (the "pull N of M files
  (X to download)…?" prompt, asked on every pull mode). There is no
  longer a grouping confirm to skip: the destination is a pure function
  of the repo id you typed.
- `--verbose` — per-file progress, resolved commit, staging paths,
  and underlying client detail on failures.
- `--hf-logging` — surface the Hugging Face client's own transfer
  telemetry live: stall timeouts, retries, backoff waits, rate-limit
  pauses. This is the client's telemetry passed through verbatim
  (`--verbose` remains this tool's own diagnostics; the two compose).
  One activation line prints at startup; after that, a healthy
  transfer is silent — the vendor's info tier only speaks when
  something stalls or retries, so a quiet run is good news, not a
  broken flag.
  No environment variables needed — though if you exported your own
  `RUST_LOG` filter, it wins over the flag and the tool prints one
  note saying so (an accidentally empty `RUST_LOG` would otherwise
  silence the Xet layer with no explanation). Pinned to info level; the
  client's debug tier (which logs request URLs) is deliberately not
  reachable by any flag. Telemetry lines can still name hub hosts and
  repo ids — skim before pasting output into a public issue.

Behavior worth knowing:

- **The interactive file listing pages instead of walling** (spec
  0018). A repo whose listing fits your terminal prints exactly as it
  always has: every file, one row each, size then path, then the
  pattern prompt. A repo whose listing would overrun the screen opens
  on a **directory roll-up** instead — one line per top-level directory
  with its file count and total size, root files listed individually,
  because the fact that decides your pattern is which quant directories
  exist, not the 166 shard names beneath them:

  ```text
  files in unsloth/Kimi-K3-GGUF (171 files, 6.9 TiB):
      25.5 KiB  .gitattributes
      43.5 KiB  README.md
     630.0 GiB  UD-IQ1_M/                 15 files
     ...
       1.4 TiB  UD-Q4_K_XL/               32 files
     862.4 MiB  mmproj-BF16.gguf  — vision projector
  f = list every file (paged), q = quit
  files to pull (comma-separated patterns, e.g. *Q4_K_M* or *.gguf,*mmproj*):
  ```

  The roll-up summarizes and never replaces: `f` lists every file, one
  screen at a time, with `m` for the next page, `b` for the previous,
  and `s` back to the roll-up. Nothing is fetched in either direction —
  the whole file list arrived with the metadata call the pull already
  made — so paging is free and the footer's total (`showing 21-40 of
  171`) is a true count, not a running tally. Groups appear in hub
  order, at the position of their first file; nothing is sorted or
  ranked. You can type a pattern at any frame, including page four,
  without paging back.

  A directory whose total is a floor — because the hub reports no size
  for one of its files — is marked with a trailing `+` (`369.8 GiB+`),
  and the header says `at least`. That is the number you weigh against
  free disk, so it says when it is understating.

  Two consequences of the key line worth knowing. **Keys are only keys
  where a key line is showing** — a listing that fits prints none, so a
  bare `q` there is a pattern matching a file named `q`, while on an
  overflowing listing it quits (exit 2). And keys match the whole
  answer before the comma split, so `f` is the key but `f,` and
  `f, *.gguf` are pattern lists. Since patterns match the full repo
  path and want a leading `*`, a real pattern never collides.

  Inside a windowed listing all five of `f m b s q` are keys whether or
  not the frame currently acts on them: pressing one that does nothing
  here re-prompts with a line saying why, rather than being read as a
  glob. `m` on the last page answers `no further pages — b, s, q, or
  type a pattern` and asks again. The roll-up's prompt also names one
  of the repo's own directories in its example (`*UD-Q4_K_XL*`), since
  typing a directory name without wildcards matches nothing — patterns
  are matched against the full repo path.

  **Piped and redirected runs are unaffected**: no roll-up, no window,
  no key line — the full flat listing, byte for byte as before. A pipe
  has no scroll problem; it has a file.
- **Every pull states its size before moving bytes.** Whatever the
  mode — `--include`, interactive, `--whole-repo` — the pull runs a
  disk preflight (refusing with exit 3 when the archive volume is
  short) and asks one confirmation stating what this run will
  download: "pull 2 of 2 files (4.6 GiB to download) from …?". `--yes`
  auto-accepts exactly this prompt.
- **Companion-artifact advisories.** Before the confirmation, the pull
  checks the repo tree against a curated rules table (data, not
  inference) and prints an advisory when your selection leaves a known
  companion behind: `*mmproj*` vision projectors, `*mtp-*`
  speculative-decoding heads, `*imatrix*` calibration data, and
  partially selected shard sets — each naming the exact `--include`
  fix. Two cross-repo checks ride along: an adapter repo whose declared
  base model isn't archived, and a quant repo whose full-precision
  master isn't archived — each naming the follow-up `llm-preserver
  pull` command. (A third check, warning that an explicit `--model`
  contradicted the declared base, went with the flag: no override can
  misfile a pull when the repo id names the directory.)
- **Non-interactive runs never hang or die vaguely.** When stdin
  cannot answer a confirmation (cron, CI, piped input exhausted), the
  pull exits 2 with a message naming the bypass — `--yes` for the size
  confirmation, which is now the only question a pull asks. The exception
  is a pull with nothing to do — it asks no questions (next bullet),
  so a scripted re-pull of an already-complete selection exits 0.

- **Re-pulls are idempotent, and a complete one asks nothing.** A
  file already archived with a matching hash is skipped ("already
  archived"); nothing re-downloads. When *every* selected file is
  already archived, no confirmation is asked at all — the destination
  was never in question, so there is nothing a yes or no could change:
  the per-file "already archived" lines
  and the "nothing to pull" summary print, the final line reads
  "`<repo>` is already archived in `<dir>`; nothing new to pull", and
  the command exits 0. (Any answer would reach the same no-op, so the
  question is not worth your keystroke.) A home proposed from the
  repo's *declared base model* still asks the grouping question
  first, even on a complete re-pull — hub metadata never names an
  archive directory without a human yes. A selection with any work
  left — a new file, an unrecorded on-disk file to adopt, a
  `--refresh-docs` replacement — asks the usual questions. A file
  whose upstream content *changed* is a hard stop — the archive never
  silently overwrites. For documentation files the stop names the way
  out ("re-run with --refresh-docs to replace this documentation
  file"); for weights there is no override.
<!-- Stall math source: https://github.com/huggingface/xet-core
  (Apache-2.0), xet_runtime/src/config/groups/client.rs —
  read_timeout 300s, retry_max_attempts 5, retry_base_delay 3000ms;
  fetched 2026-07-13. Re-verify there before editing the numbers. -->
- **A frozen progress bar usually heals itself — give it minutes,
  not seconds.** The byte-transfer layer tolerates 300 seconds of
  connection silence before it counts a stall, then retries up to 5
  times with exponential backoff (3-second base) — all silently at
  default logging. A bar that stops moving is therefore often a stall
  the client is already handling, and it can take ~6 minutes to prove
  it: reach for `--hf-logging` (or patience) before Ctrl-C, and watch
  the stall and retries explain themselves. If you do interrupt, the
  resume hint below has your back. Power users can tune the client's
  own knobs (`HF_XET_CLIENT_READ_TIMEOUT` and friends) as environment
  variables — the tool passes its environment through to the client
  and wraps none of them in flags.
- **Interrupted pulls are safe to retry.** Re-run the same command;
  completed files in staging are reused (they tick by instantly with
  no progress bar), and the record is only ever written after every
  selected file is fully on disk and hashed. The one file that was
  *in flight* at the interruption restarts — a file only counts once
  it is complete — though the transfer backend's chunk cache usually
  makes the restart much cheaper than a full re-download. (The
  client's bars show two phases per large file — "downloading bytes"
  then "reconstructing file" — that's its Xet chunk transfer, not
  two downloads.)
- **Abandoning an interrupted pull.** Downloads stage into
  `<archive-root>/.staging/<owner>/<repo>/` (a sibling of
  `models/` — the model only appears under `models/` once every file
  is staged, verified, and moved, which is why a mid-pull model is
  invisible to `status`). The staging directory is deleted on pull
  success; after an interrupt it holds the completed files and the
  transfer client's partial-download bookkeeping. If you decide not
  to finish the pull, delete that model's staging directory by hand —
  nothing under `.staging/` is referenced by the archive, so removing
  it only costs the resume head start.
- **The resume-command hint.** When the pull's shape came from the
  interactive file listing (patterns you typed at the prompt, so your
  shell history doesn't have them), the pull prints one line right
  after the confirmations, before the first byte moves:
  `to continue this pull later: llm-preserver pull <repo-id> <path>
  --include '<pattern>'`. It is the exact
  direct command that reproduces this pull — absolute archive path
  (works without `LLM_PRESERVER_ARCHIVE` and from any directory),
  shell-quoted patterns, and the grouping you just confirmed replayed
  `--hf-logging` rides along when the pull ran with it — the
  stalled-transfer scenario the hint serves is the one that flag
  exists for (`--verbose` does not; the hint replays the pull's
  shape, not general diagnostics).
  Because re-pulls are idempotent, running it later downloads only
  what is still missing. Ctrl-C during the transfer prints the hint
  as the final line — directly above your next shell prompt — and
  exits 130 (128 + SIGINT); that interrupt-time print happens on
  *every* pull, including one whose shape you typed yourself (where
  it usefully carries the resolved selection your history entry may
  lack). Only the transfer-start print is skipped when you typed
  `--include`/`--whole-repo` yourself: that command is already in
  your history. The hint spells the command `llm-preserver …` — paste
  it as-is after installing the CLI on your PATH (README → "Install
  the command on your PATH"), or prefix `uv run` and run it from the
  project directory. Note the line carries your machine's absolute
  archive path — worth trimming if you paste terminal output into a
  public issue.
- **Gated/private repos** use Hugging Face's own login: run
  `hf auth login` once (or set `HF_TOKEN`). The tool takes no token
  flags and never stores or logs the token. Logging in also helps
  *public* pulls — anonymous requests get lower hub rate limits (the
  client prints a warning suggesting `HF_TOKEN` when unauthenticated).

Exit codes name the fault domain so failures can be triaged without
reading source:

| Code | Domain | Typical cause / next step |
| --- | --- | --- |
| 1 | archive/usage | path is not an archive; bad arguments |
| 2 | user input | malformed or unknown repo id; gated repo not accepted; no matching files |
| 3 | local environment | network unreachable, disk full — check your machine |
| 4 | hub-side | 5xx or rate limiting — retry later; not your fault |
| 5 | integrity | hash mismatch after download — the file never entered the archive |
| 130 | interrupted | Ctrl-C during the transfer — paste the resume hint (printed as the last line) to continue |

## pull --plan — dry run (verify, then run)

`--plan` runs the whole decision half of a pull — resolve the tree,
apply the selection and grouping rules, evaluate advisories, total
the sizes, check disk — prints the itemized result, and exits without
downloading or writing anything:

```bash
uv run llm-preserver pull unsloth/Qwen3.6-27B-MTP-GGUF ~/models \
    --include '*Q8_0*' --plan
# plan: pull from unsloth/Qwen3.6-27B-MTP-GGUF into …/models/Qwen/Qwen3.6-27B
#      28.9 GiB  Qwen3.6-27B-Q8_0.gguf
#       12 KiB   README.md  — doc, rides along
# total to download: 28.9 GiB (2 of 2 files)
# disk preflight: ok (312.4 GiB free)
# advisory: tree ships mmproj-F16.gguf (vision projector); the
#   selection excludes it — add --include '*mmproj-F16.gguf'
# plan only: nothing downloaded, nothing written
```

Interactive pulls barely need it — every pull already shows the size
confirmation, and answering `n` walks away safely. `--plan` exists
for the *scripted* form, where `--yes` leaves no moment to look:
verify the command once with `--plan`, then run the identical command
without it. Details:

- Composes with every selection mode: `--include`, `--whole-repo`,
  and the interactive listing (the pattern prompt still runs; the
  plan prints instead of pulling).
- Asks no confirmation prompts. Questions a real pull would ask
  (grouping, "selection covers every weight?") are resolved to the
  answer that lets planning continue and printed as `would ask:`
  lines — scripted for real, those still need a narrower
  `--include`.
- Exit codes are gateable: 0 when the pull would proceed; 3 (local
  environment) after the report when the disk preflight would refuse.
- The plan lists per-file sizes and marks already-archived skips —
  unlike the size confirmation, which deliberately shows counts only.
- One adjudicated exception to "downloads nothing": a repo shipping
  `adapter_config.json` gets that one small file fetched (temp dir,
  never the archive) so the adapter-base advisory is accurate; the
  output says so.

## pull --whole-repo — archive a whole repo (full snapshot)

Selective pull acquires runnable derivatives; `--whole-repo` acquires
the master copy — the original full-precision tree that later formats
derive from. Quantization is one-way lossy, so the original is the
only copy that can be re-quantized, fine-tuned, or loaded by
non-GGUF stacks later.

The scope is the *one repo you name*. On a quant repo it means every
quantization in that repo (rarely what you want — pull one
`--include` instead), and it never reaches across repos: archiving a
quant does not fetch its original, which lives in a separate repo —
the full-precision-master advisory names that follow-up pull when it
applies.

```bash
# archive the original Qwen3.6-27B tree (~54GB of safetensors shards):
uv run llm-preserver pull Qwen/Qwen3.6-27B --whole-repo ~/models
# → lands at models/Qwen/Qwen3.6-27B/ — the repo id, verbatim, with
#   nothing to confirm about the destination (ADR 0003)
# → refuses up front if the tree will not fit on the archive volume
# → confirms once with what will actually download:
#   "pull 14 of 14 files (50.3 GiB to download) from Qwen/Qwen3.6-27B?"
```

Snapshot behavior:

- **One confirmation, showing remaining work.** No per-file listing or
  pattern prompt — the selection *is* the tree. The prompt states what
  this run will actually download ("pull 3 of 14 files (9.1 GiB to
  download; 11 already archived)…"); per-file progress comes from the
  client's own progress bars plus an `n of m` log line per file.
  "Already archived" counts files *recorded* by a previous completed
  pull — an interrupted run records nothing, so its re-run still says
  the full file count even though completed staged files tick by
  instantly and their bytes are netted out of the GiB figure and the
  disk preflight.
- **Re-running a completed snapshot downloads nothing.** It reports
  "nothing to pull: every selected file is already archived" and
  exits 0 — no size prompt, no downloads. The grouping question is
  skipped when the plan has no work; a home
  proposed from the declared base model still confirms first (see the
  selective-pull section).
- **One source repo per format subdirectory.** A second same-format
  snapshot from a *different* source repo is refused (two verbatim
  trees cannot share one directory honestly) — archive it under a
  different repo, or pull selected files instead. Re-running
  a snapshot of the same repo, and mixing selective + `--whole-repo`
  of the same repo, stay fine.
- **Disk preflight.** File sizes come from the same metadata call, so
  the pull refuses (exit 3, local environment) before downloading
  anything when free space at the archive path is short, stating
  required vs. available.
- **Interrupted pulls are safe to re-run.** An interrupted
  `--whole-repo` records nothing; re-running the same command
  re-plans the whole tree. Resume comes from the download client reusing files already
  fully downloaded into staging, and the disk preflight charges only
  the bytes still missing — a half-finished 300GB pull does not
  demand 300GB of free space again.
- **Tree fidelity.** The snapshot preserves repo-relative paths
  verbatim — sharded weights beside `config.json`, and README/LICENSE
  at their in-tree locations rather than the selective pull's
  `docs/<source-repo>/` directory (each snapshot owns its format
  subdirectory, so in-tree docs cannot collide). One consequence: if
  a selective pull of the same repo came first, its relocated doc
  copy remains and the snapshot adds the in-tree one — additive
  duplication, never a conflict.
- **Formats.** An original tree records as `hf-snapshot`; an
  `mlx-community/*` repo lands in `mlx/`; a GGUF repo snapshot lands
  in `gguf/` — same inference as selective pulls.
- **Gated originals** (Llama-style license acceptance) work exactly
  like gated quants: accept the license on the hub once, then
  `hf auth login` — no tool flags.

## discover — find a model by name and pull it

For when you know a model's *name* but not the exact repo id — the
step `pull` can't help with. `discover` closes the browser trip:

```bash
uv run llm-preserver discover 'qwen3 0.6b gguf' ~/models
# hub search results for 'qwen3 0.6b gguf':
#   1. Qwen/Qwen3-Embedding-0.6B-GGUF  —  181142 downloads · 2025-07-14
#   2. Qwen/Qwen3-0.6B-GGUF            —  38687 downloads · 2025-05-09
#   ...
# showing 1-20 of 20 — more (m)
# pick a model to explore (number; m = more, q = quit): 13
# model tree for unsloth/Qwen3-0.6B-GGUF:
# up — ancestry, root at top (picking a number climbs the tree):
#   1. Qwen/Qwen3-0.6B-Base  —  1062579 downloads   [original — no parent]
#   2.    └─ Qwen/Qwen3-0.6B —  27809311 downloads
#            └─ unsloth/Qwen3-0.6B-GGUF  [this repo — you are here]
# down — derivatives of this repo (picking drills into one):
# quantized versions:
#   3. ...
#   0. pull this repo (unsloth/Qwen3-0.6B-GGUF)
# showing 3-21 of 21 — more (m)
# hop the tree by number — 0 = pull unsloth/Qwen3-0.6B-GGUF (m = more, q = quit): 0
# → the normal pull flow: file listing, advisories, size confirmation
```

Discovery is open-ended navigation, not a fixed number of steps: you
can hop the tree as long as you like, and the session ends only when
you pick the "pull this repo" line (which starts the normal pull) or
type `q`. Three stages, every step a numbered pick:

1. **Search** — the hub's own free-text results, passed through
   verbatim (the hub's relevance order — the tool never re-ranks).
   Each row shows hub facts: downloads, last-updated, and a `gated`
   marker where the repo needs accepted terms (`hf auth login` as
   usual). An empty result set exits 0 — refine the query and re-run.
2. **Model tree** — the picked repo's parents (repeated `base_model`
   hops; a renamed parent shows both ids, a dead one says "not found
   on the hub" — stale hub metadata is shown, never guessed around)
   and its derivative children grouped by relation (quantized /
   finetune / adapter / merge), hub-sorted by downloads. Sections are
   direction-labeled (up = parents, down = derivatives), a
   `your path:` breadcrumb shows the repos you've hopped through
   (hopping back to one pops the path), and `0` is always the
   pull-this-repo key — stable no matter how many pages you fetch. Pick a number to hop
   anywhere; both listings page with `m` and `b` (see below).
3. **Pull** — "pull this repo" first asks *how* to archive:
   `1 = pick files` (quant repos — choose your quant from the
   listing) or `2 = whole-repo snapshot` (originals/masters — the
   tree is the artifact, spec 0004 semantics). Then the exact `pull`
   flow: file listing (mode 1 only), advisories, then the size
   confirmation. The file listing pages the same way these frames do —
   a big quant repo opens on a directory roll-up with `f` to list every
   file, so the handoff out of `discover` no longer drops a 171-row
   wall on a terminal that cannot scroll back (spec 0018; see the pull
   section). There is no grouping question — the repo row you
   picked *is* the directory (ADR 0003), so hub metadata cannot name
   an archive directory at all, which is the spec 0006 invariant now
   held structurally rather than by a prompt. Once the confirmation
   passes, the pull prints
   the **resume-command hint** — the direct `llm-preserver pull …`
   line that reproduces this exact pull without re-driving the
   navigation (see the pull section). Only the `discover` invocation
   is in your shell history, so this line is the one record of the
   pull you assembled; interrupt the download with Ctrl-C and it
   reprints as the final line, ready to paste when you come back.

**Paging: one screen at a time, and numbers that stay put.** Both
listings show a single window of rows sized to your terminal, so a
frame never scrolls off the top — which matters on a console with
little or no scrollback (`screen`, a serial session, a CI log). Long
repo ids wrap, and the window counts that: a row too wide for your
terminal is charged the two lines it actually occupies, so the frame
fits the screen rather than the line count.

`m` shows the next window; `b` (`b = back a page`) steps back to the
window before, with no network call, and is offered only once there is
one. Hub fetches are decoupled from windows — one fetch feeds several
windows, and the buffer is topped up before a window would come up
short, so `m` never hands you a one-row frame. The footer says where
you are: `showing 21-39 of 39 — more (m)` means picks 21 through 39
are on screen, 39 numbers have been handed out so far, and there is
more to come. Everything counted there is typeable — the footer never
advertises a number the prompt would refuse. Step back and the count
stays at the furthest you have seen (`showing 1-20 of 39 — more (m) ·
back (b)`). At the end of the listing `m` is simply withdrawn from the
footer and the prompt; if a window was already open when the rows ran
out, `m` answers `no further rows on the hub` rather than reprinting
the frame.

A pick number, once shown, names the same repo for the rest of that
listing — paging never renumbers what you already read, so a number
you noted before pressing `m` is still safe to type afterwards. (A
number that has scrolled off still works; one that was fetched but
never displayed does not, because you cannot have read it.) The cost
of that guarantee is visible and deliberate: a relation's section
label reappears when a later page brings more of it (`quantized
versions:` a second time), and a window that opens mid-section says
`quantized versions (continued):`. That is an honest description of
what was fetched, not a duplicate. When output is piped rather than
shown on a terminal, the window is a fixed 20-line budget and rows are
never wrap-adjusted, so runs stay byte-identical.

`--plan` makes the final pull the dry run (verify, then re-run for
real); `--verbose` and `--hf-logging` as in `pull`. Failures map to the same exit codes
as `pull` (network 3, hub-side 4). Discovery is deliberately
interactive-only: scripts already have exact repo ids, `--include`,
`--yes`, and `--plan`. The tool shows facts and takes your picks — it
never recommends, scores, or auto-selects.

### discover --match-ollama — which hub repo holds these bytes?

For when you already *run* a model in Ollama and want to archive the
same build — not a same-named near-miss whose outputs subtly diverge.
Ollama's store is content-addressed and Hugging Face publishes per-file
SHA256s, so byte-identity is checkable with metadata calls alone:

```bash
llm-preserver discover --match-ollama bge-m3:latest
# ollama store: /Users/you/.ollama/models
# local bge-m3:latest — model layer sha256: 4bf5cf…
# checking the first 20 hub search results for 'bge-m3' (the hub's relevance order):
# gpustack/bge-m3-GGUF
#   bge-m3-FP16.gguf  1157671200 bytes  byte-identical to the local model
#   bge-m3-Q4_K_M.gguf  437778496 bytes  unverified
#   ...
# CompendiumLabs/bge-m3-gguf
#   bge-m3-f16.gguf  1157671200 bytes  unverified
#
# 15 of 20 results have no GGUF files: BAAI/bge-m3, Xenova/bge-m3, …
#
# 1 byte-identical match — run this to archive it:
#   gpustack/bge-m3-GGUF  —  181142 downloads · 2025-07-14
#     llm-preserver pull gpustack/bge-m3-GGUF --include bge-m3-FP16.gguf
```

Reads the model-layer digest from the local Ollama manifest
(read-only, no Ollama server needed). The store is found by a fixed,
disclosed probe: `$OLLAMA_MODELS` when set (an explicit override —
never a fallback chain), else `~/.ollama/models` (macOS, Windows,
manual Linux runs), else `/usr/share/ollama/.ollama/models` (Linux
system installs); the first line of output names the store that was
read, and a miss on every candidate names each path checked. It then
searches the hub with the tag-stripped model name (`--search <term>`
overrides) and fetches each candidate's file listing — metadata only,
nothing downloaded, nothing hashed. By default the first 20 search
results are checked; `--limit <n>` (max 500) pages deeper for models
whose byte-identical repo ranks low — a low-download re-upload of a
niche model is exactly the repo this mode exists to find. Each result
costs one hub metadata call, which is why deeper scans are an
explicit ask, not the default. Each
candidate GGUF gets a stated fact: **byte-identical** (SHA256 equals
the local blob), or **unverified** with its size, so a same-size
different-digest file is visibly a near-miss (a different converter
run), not a match. Candidates stay in the hub's order; repos with no
GGUF files roll up into one summary line, and the matches print in a
footer as the final output — the last line is the exact
`pull --include` command that archives exactly those bytes. When
several repos match, they all hold the same bytes and any one pull
suffices; each match line carries the repo's hub facts (downloads ·
last-modified · gated — the same facts search rows show) so you can
pick by provenance. Facts, never a ranking. The tool
never pulls — paste the command yourself (append the archive path if
`$LLM_PRESERVER_ARCHIVE` isn't set, and add `--base-model` lineage if you
want to direct the home).

Boundaries worth knowing: match mode takes no positional arguments,
never touches the archive, and refuses `--plan` (it promises a pull
dry run, and match mode never pulls); a candidate whose metadata fails
to load is noted and the scan continues; "no exact match was verified" is a
successful scan (exit 0) — the facts just came back negative. A
missing store or unknown model name exits 2 naming what was looked for
and where. Matching is only as good as the name-search candidates:
hf.co has no search-by-hash, so a model whose repos use unrelated
names may find nothing even at `--limit 500` — that limitation is
stated, not worked around with heuristics (no fuzzy matching, ever);
`--search` with a sharper term is the deterministic lever.

## status — inventory table

```bash
uv run llm-preserver status ~/models
```

One row per archived model: roles (role-less models group under
"(no role)"), formats, human-readable size (binary units, the same
rendering as pull's confirmations and remove's previews), completeness.
The fast answer to "what is on the shelf." Exact byte counts live in
`show`.

### Lineage grouping

Rows are grouped by declared lineage (ADR 0003). A model whose record
names a `base_model` is indented one level under that base:

```
model                         formats      roles      size      completeness
zai-org/GLM-4.7-Flash         hf-snapshot  (no role)  58.2 GiB  ok
  unsloth/GLM-4.7-Flash-GGUF  gguf         (no role)  16.4 GiB  ok
```

The old layout said this with the directory tree; one directory per
source repo says it here instead. The claim comes from the record and
nothing else — `status` walks `models/*/*` to see which directories
exist, but reads no payload file and makes no network call — so it is
only as good as its `base_model_source` (`show` names that origin: the
model card, your `--base-model`, or a migration's reading of the old
directory nesting).

**A base the archive does not hold still gets a row**, parenthesized,
with a dash in the formats, roles, and size cells:

```
model                            formats  roles      size      completeness
(Qwen/Qwen3-Coder-Next)          -        -          -         not archived
  unsloth/Qwen3-Coder-Next-GGUF  gguf     (no role)  46.6 GiB  ok
```

`not archived` is not a completeness verdict — there is no record to
assess. The row reports one fact: something on the shelf declares this
id as its base, and the archive has no directory for it. Such a row
never appears alone, since it exists only because a derivative
references it.

The tool states the fact and never recommends. Whether a given gap is
worth closing — and the one case where a missing base is a hard
runtime dependency rather than provenance — is covered in
[`what-to-archive.md`](what-to-archive.md).

**Indentation is one level, never deeper.** Only a base that declares
no base of its own adopts, so in a chain of three the middle model
indents under the first and the third returns to the left margin.
Longer chains exist across records — each record stores one hop, and
those hops connect — but the renderer stops at one level rather than
nesting them.

A row at the left margin therefore does not prove that model derives
from nothing. It means the model declares no base at all, or that the
base it declares is itself a derivative. A declared base the archive
does not hold does *not* produce a margin row — it produces the
`not archived` header above, with the derivative indented under it.
`show` is the way to read a single model's declared base without the
grouping in the way.

### The completeness column

The last column reports the health of the model's *record*, and it is
derived from the record alone — nothing in that column (or in `size`)
touches the filesystem. Several problems join with commas.

| Cell | Meaning |
| --- | --- |
| `ok` | None of the below apply |
| `no record` | The model directory has no `model-record.json` |
| `record unreadable` | The record is present but failed to load or validate. The roles cell shows `-` rather than "(no role)" — roles are unknown, not absent |
| `newer record schema` | The record's `record_schema_version` is newer than this tool understands. Flagged, never refused, so read-only inspection of a future archive still works |
| `no license` | The record carries no license label |
| `missing checksums` | At least one file entry has no SHA256, so `verify` cannot prove that file valid |

**`ok` means the model describes itself completely — not that its files
are present or intact.** That distinction is deliberate: `status` is a
metadata report that runs in milliseconds and never reads a weight
file. Auditing disk against the record is
[`verify`](#verify--audit-the-archive-against-its-records), which has
its own separate vocabulary (*complete* = every expected file present,
*valid* = every hash matches). A model can read `ok` here and still be
missing every byte on disk.

## show — one model's record

```bash
uv run llm-preserver show Qwen/Qwen3.6-27B ~/models   # path optional with the env var
```

Prints everything archived for one model: artifacts, per-file
provenance and hashes, pinned commits, license, source repos.

## verify — audit the archive against its records

> **Flag rename (ADR 0003).** Scoping is `--repo OWNER/REPO`, matching
> what a model directory now is. `--model` is still accepted so an
> existing cron line keeps working; it prints a one-line note naming
> `--repo`. Passing both is exit 2.

```bash
uv run llm-preserver verify ~/models          # full audit: re-hash everything
uv run llm-preserver verify                   # same, archive from $LLM_PRESERVER_ARCHIVE
uv run llm-preserver verify --quick           # existence + size only, seconds not hours
uv run llm-preserver verify --repo Qwen/Qwen3.6-27B    # one repo, not the whole shelf
uv run llm-preserver verify --staging         # only: any abandoned downloads? (instant, no hashing)
```

The whole-archive drift detector (spec 0009), BagIt-style: each
model's record enumerates its *expected* files, and verify checks disk
against it — existence, then size, then a full SHA256 re-hash, in that
order, so a missing or truncated file is caught without paying for a
hash. Fully offline; it never contacts the hub.

One result line per model (valid models included — an audit should
read as "everything was checked"), with per-file detail lines under
any model that drifted, then an archive-wide totals summary. Run
interactively, verify also shows live progress on stderr — a
`checking <model> (N files, X GiB recorded)` line as each model
starts, and an in-place `hashing <file>: 12.4 GiB / 28.9 GiB` counter
while a large file streams through the hash — so a long run never
looks hung. Piped or cron runs get none of this: without a terminal
on stderr the output is exactly the report below, nothing more. The
categories:

- **valid** — every recorded file exists and re-hashes to its recorded
  SHA256.
- **complete** — every recorded file exists at its recorded size, but
  nothing was hash-validated: every `--quick` result caps out here,
  and so does a full run over a record that carries no hashes at all
  (e.g. a future unverified cache import) — "valid" is never claimed
  for a model whose digests were not actually checked.
- **incomplete** — recorded files are missing from disk (each is
  named). `remove` updates the record whenever it deletes, so a
  recorded-but-missing file is never its doing — this means an
  out-of-band deletion (a manual `rm` inside the archive) or a partial
  copy.
- **invalid** — everything is present, but at least one file's size or
  hash disagrees with the record (expected and actual are shown), or a
  file could not be read. Bitrot, tampering, or a failing disk.
- **unmigrated** (layout, *appended* to the fixity verdict) — the
  directory holds another repo's files, so path, `hub_id`, and
  `source_repo` do not all agree (ADR 0003). It reads as
  `valid, unmigrated` rather than replacing the fixity word, because
  "did the bytes check out" and "is this in the right place" are
  different questions and one must not erase the other. Nothing is
  damaged; the line names the offending repo and `migrate` as the fix.
  Exit 1 — a scheduled verify goes red until the layout is converted —
  but **drift outranks it**: a model that is both drifted and
  unmigrated still exits 5, with both words on its line.
- **unhashed** (per-file, informational) — the record carries no
  SHA256 for the file (e.g. an unverified cache import); existence and
  size are still checked.
- **unrecorded** (per-file, informational) — on disk but in no record:
  something was hand-copied in. The tool's own generated files
  (`model-record.json`, `MODEL-RECORD.md`, `manifest-sha256.txt`) are
  exempt.

Verify is read-only over payloads and records. Its one write is
`manifest-sha256.txt` in each model directory: a regenerable,
`sha256sum -c`-compatible sidecar (also written by `pull`), refreshed
on every full run for every model with a readable record — drifted
models included, since the manifest derives from the record, which
remains the source of truth. A stale sidecar left by an older pull is
overwritten. `--quick` writes nothing. When the sidecar cannot be
written (a read-only-mounted archive, a full disk), verify prints a
per-model "manifest not refreshed" warning on stderr and keeps going —
the payload verdict and the exit code are unaffected, so a
deliberately read-only mount still verifies. The sidecar means fixity
stays checkable with coreutils alone:

```bash
cd ~/models/models/Qwen/Qwen3.6-27B && sha256sum -c manifest-sha256.txt
```

### Abandoned downloads (`--staging`)

A pull stages into `.staging/<owner>/<repo>/`, and only after every
file has moved into `models/` and the record is written does it delete
that directory. An interrupted pull — Ctrl-C, a crash, a dropped link —
therefore leaves partial bytes in `.staging/` and writes no record.
Because the audit above checks only what each record *lists* under
`models/`, and `.staging/` is a sibling of `models/`, a half-finished
download is otherwise invisible: an archive-wide `verify` can report
"all valid" while gigabytes of an abandoned pull sit forgotten in
staging (spec 0012).

Detecting a leftover is just the presence of a non-empty
`.staging/<owner>/<repo>/` directory — no record, no `models/` walk,
no hashing. So finding them never needs a hash run:

```bash
uv run llm-preserver verify --staging                       # list abandoned downloads, then stop
uv run llm-preserver verify --staging --repo Qwen/Qwen3.6-27B    # scope to one
```

`--staging` skips the recorded-file audit entirely and prints one line
per leftover — `<owner>/<repo>  4.5 KiB, 2 partial files`, sorted by
id — or `no abandoned downloads in .staging/` when clean. It is
near-instant regardless of archive size, writes nothing (not even the
manifest sidecar a full run refreshes), and always exits 0: a leftover
is an incomplete acquisition you chose to interrupt, not corruption of
preserved data. The size and file count cover the *whole* staging
directory — including huggingface's own `.cache/huggingface/`
bookkeeping and the in-progress `.incomplete` blob — because the point
is to surface every incidental byte the record-based audit can't see and
let you decide; it does not try to separate downloaded payload from hf's
client-side scratch. `--quick` is a no-op alongside `--staging` (the scan
never hashes anyway). Under `--staging` the `--repo` id namespace is
the staging tree, not `models/` — a first-ever interrupted pull has no
model directory at all — so an unknown `--repo` lists the ids present
in `.staging/`.

A plain `verify` (full or `--quick`) never *fails* on a leftover, but it
does not stay silent either: when `.staging/` holds abandoned downloads
it prints a one-line footer —
`note: 2 abandoned downloads in .staging/ — run 'verify --staging'` —
after the audit summary, with the exit code unchanged (the footer
prints even when the audit itself exits 5 for drift). With `--repo`
set, the footer counts only that model's leftover.

Resolving a leftover uses commands that already exist, and the scan
prints the id both need: resume the original
`pull <owner>/<repo> …` (staging is reused, so completed shards are
not re-fetched), or discard it with `remove <owner>/<repo>` (which
clears a staging-only leftover even when no `models/` directory exists).
One caveat: a pull running *right now* also has content in `.staging/`,
and the scan cannot tell a live transfer from a forgotten one — run
`--staging` when no pull is in flight.

Exit codes are the cron contract — a scheduled run needs no output
parsing:

| Code | Meaning |
| --- | --- |
| 0 | clean — every checked model valid or complete; unhashed/unrecorded findings, abandoned-download (`.staging/`) notes, and manifest-refresh warnings are informational and do not change the code. An empty archive also exits 0, saying so explicitly. `--staging` always exits 0 (or 1/2 for a bad path/`--repo`) |
| 1 | archive/usage — path is not an archive; malformed `--repo` syntax; a directory whose layout is `unmigrated` (ADR 0003) |
| 2 | user input — `--repo` names no archived model (the error lists the archive's model ids so a typo self-corrects). The CLI framework's own usage errors — a missing path with no `LLM_PRESERVER_ARCHIVE` set, an unknown flag — also exit 2, so treat 2 as "fix the invocation", not specifically "unknown model" |
| 5 | integrity — drift found: any model incomplete, invalid, missing its record, or with an unreadable record or payload file |
| 130 | interrupted — Ctrl-C; the in-progress model's sidecar is untouched |

A full run re-reads every archived byte, so it is disk-bound: figure
roughly 2.5 hours per terabyte over gigabit to a NAS, much faster on
local storage. `--quick` catches deletion and truncation (not bitrot)
in seconds and suits a pre-backup sanity pass; the full run is the
quarterly fixity check.

## migrate — convert an archive to one directory per source repo

Archives created before ADR 0003 name each directory for the *original*
model and file third-party quants underneath it. `migrate` converts
such an archive to one directory per source repo. It is the only
supported way to do that: a hand-moved directory leaves the record
claiming an identity its path no longer matches, and the manifest
sidecar hashes the record, so `verify` would call it invalid forever.

**Nothing is re-downloaded and nothing is re-hashed.** Paths *inside*
an artifact do not change, so every recorded digest stays true; the
manifests are regenerated from the record. In place, the moves are
renames, so a large archive converts in minutes rather than the hours a
copy would take.

```bash
# see exactly what would move, changing nothing:
uv run llm-preserver migrate ~/models --plan

# rehearse on one model, into a throwaway copy the original never feels:
uv run llm-preserver migrate ~/models --to /tmp/mig-test --repo zai-org/GLM-4.7-Flash

# convert for real (preview, then a y/n):
uv run llm-preserver migrate ~/models
```

- `--plan` — print the conversion and exit. Names every directory, its
  target, the bytes that move, each empty directory that will be
  removed, and any collision that would block the run.
- `--repo OWNER/REPO` — convert (or copy) only this directory;
  repeatable. Naming a directory the archive does not hold is exit 2,
  never a silent no-op.
- `--to ROOT` — write a converted **copy** at `ROOT` and leave the
  source untouched. The reversible mode; combine with `--repo` to
  rehearse on one model. Bytes are copied, never hardlinked.
- `--view-dest PATH` — a runtime-view destination to name in the
  refresh hint; repeatable. The path is composed into printed text and
  **never opened** — migrate does not touch a view tree, it only tells
  you what to re-run.
- `--yes` — skip the confirmation, never the disclosure. A
  non-interactive run *without* it refuses (exit 2) rather than act on
  a piped answer.

**Safety properties worth knowing.** The run refuses as a whole — never
half-converts — on a collision, an unreadable record, an artifact with
no recorded `source_repo`, or a recorded path that resolves outside its
directory. It is idempotent and resumable: the plan is derived from
disk, so an interrupted run is finished by running it again, and a
converted archive reports nothing to do. The only deletion it performs
is `os.rmdir` on a directory the move emptied — which fails rather than
recursing if anything remains, and is named in the preview first. No
payload file is ever unlinked; bytes leave a directory by moving out of
it.

**Afterwards**: runtime views point at the old paths, so re-run `views`
against each destination. Migration cannot discover view trees it was
not told about — the marker points dest → archive, not the reverse.

**A v1 archive must convert before it accepts new content.** `pull` and
`remove` refuse with exit 2, naming `migrate` and the count. `status`,
`show`, `verify`, and `views` keep working throughout, so the archive
stays inspectable and runnable while you decide.

## remove — delete a model, or some of its files, from the archive

```bash
uv run llm-preserver remove Qwen/Qwen3.6-27B ~/models          # the whole model
uv run llm-preserver remove Qwen/Qwen3.6-27B                   # archive from $LLM_PRESERVER_ARCHIVE
uv run llm-preserver remove Qwen/Qwen3.6-27B --include '*Q4_K_M*'  # one quant, keep the rest
uv run llm-preserver remove Qwen/Qwen3.6-27B --yes             # scripted: skip the prompt
```

The archive's one sanctioned deletion path (spec 0010) — the "delete"
in the tool's create/read/update/delete cycle. Hand-`rm` inside the
archive lets the record and directory drift apart and silently strands
interrupted-pull staging; `remove` keeps the record, the on-disk files,
and the staging leftovers consistent. It never touches the hub and
never deletes anything outside the named model's directory and its
staging sibling.

Two granularities:

- **Whole model** — `remove <owner>/<repo>` deletes the model
  directory (record, rendered markdown, manifest, all payload) and the
  model's `.staging/<owner>/<repo>` leftovers if any. A model that
  exists *only* as staging leftovers (an interrupted pull that never
  completed, invisible to `status`) is removable the same way — remove
  reports there is no archived model and offers to clear the staging
  directory. A model whose record is missing or unreadable is still
  removable: the preview falls back to a filesystem-derived file count,
  so degraded metadata never leaves a model stuck.
- **Pattern-scoped** — `remove <owner>/<repo> --include '<pattern>'`
  deletes only the matching payload files, drops their entries from the
  record (an emptied artifact and its now-empty format directory go
  too), and regenerates `MODEL-RECORD.md` and `manifest-sha256.txt`, so
  `verify` reports `valid` afterward. This is the quant-swap and
  shed-the-master path.

`--include` uses the same fnmatch language as `pull --include`
(repeatable, case-sensitive, union of patterns), with two differences
worth internalizing:

- It matches the **archived** paths `show` lists (format-dir-prefixed —
  `gguf/…`, `hf-snapshot/…`), not the hub repo's filenames that `pull`
  matches. A floating pattern like `*Q4_K_M*` (what the pull resume
  hint prints) matches in both; a pattern anchored at a hub filename's
  start does not match on remove.
- Pull's "documentation always rides along" rule does **not** carry
  over — remove deletes exactly what matches, and a doc file is neither
  auto-included nor protected.
- A pattern matching **every** archived file is refused, pointing at
  plain `remove` — pattern mode never empties a model silently. A
  pattern matching **nothing** is a user error (exit 2, the pattern
  echoed). Files on disk that no record lists (`verify`'s `unrecorded`
  class — stranded junk, or the leftovers of an interrupted pattern
  removal) match patterns too, so re-running the same command finishes
  an interrupted one.

Behavior worth knowing:

- **The preview is the safety mechanism.** Deletion is permanent —
  there is no trash can or undo. Every run prints what it will delete
  (formats, file counts, sizes — the same human sizes as `status` —
  and the staging directory when present) before asking to confirm.
- **`--yes` skips the question, not the disclosure.** The full preview
  still prints, then a result line naming what was deleted, so a
  script's log carries the audit trail. The prose is not promised
  machine-parseable; exit codes are the scripting contract.
- **Non-interactive means `--yes`.** Because the delete is
  irreversible, a run with no terminal on stdin refuses (exit 2) unless
  `--yes` is passed — a piped or inherited `y` is not accepted as a
  stand-in for a human. Scripts and cron jobs opt in explicitly with
  `--yes`; interactive terminals get the normal prompt.
- **Deletion is crash-safe by ordering.** Whole-model removal deletes
  the record first, so an interrupt leaves an unrecorded directory
  `status`/`verify` already surface (and a re-run finishes) — never a
  record naming missing files. Pattern removal writes the updated
  record first, so an interrupt leaves informational `unrecorded`
  strays a re-run of the same command sweeps up.
- **Ctrl-C during deletion** exits 130 and prints the exact re-run
  command as the final line (absolute archive path, quoted patterns,
  no `--yes` — the re-run earns its own preview). There is no separate
  resume state; the same invocation finishes the job.
- **Live progress on a TTY.** Run interactively, remove prints one
  `removing <file>` line per file on stderr (removal over a slow mount
  should not look hung). Piped or cron runs get none of it: stdout
  stays byte-identical to a progress-free run.

Exit codes match the rest of the tool:

| Code | Domain | Cause |
| --- | --- | --- |
| 0 | success | model or files removed; also a declined confirmation ("nothing removed") |
| 1 | archive/usage | path is not an archive; malformed `<owner>/<repo>`; a symlinked model directory (refused, never followed); pattern removal against a model with no readable record |
| 2 | user input | unknown model (the archive's model ids are listed so a typo self-corrects); a pattern matching nothing; any non-interactive run (no terminal on stdin) without `--yes` |
| 130 | interrupted | Ctrl-C during deletion — paste the re-run command (printed as the last line) to finish |

Removing whole models or pattern subsets is the only deletion the tool
performs; the archive is otherwise append-only.

## views — run archived models in place (phase 1: Ollama)

```bash
uv run llm-preserver views ~/models --tool ollama --dest ~/ollama-view              # print instructions only
uv run llm-preserver views ~/models --tool ollama --dest ~/ollama-view --seed-store # seed the external store
llm-preserver views --seed-store   # both paths from env: $LLM_PRESERVER_ARCHIVE + $LLM_PRESERVER_VIEWS
```

Both paths have env fallbacks: the archive argument falls back to
`$LLM_PRESERVER_ARCHIVE` (as everywhere), and `--dest` falls back to
`$LLM_PRESERVER_VIEWS/<tool>` — a views *root* with one subdirectory
per tool, so later adapters never collide. An explicit `--dest` wins.
(`$OLLAMA_MODELS` itself always points at the generated view, never at
the archive: Ollama requires read-write on its store, and the archive
is neither Ollama-shaped nor writable by tools.)

A complete first run, from nothing to a prompt — the instructions-only
run prints this same flow with your real paths and the first usable
model's name filled in, so every line is pasteable:

```bash
export LLM_PRESERVER_ARCHIVE=~/models    # one-time; add both to ~/.zshrc
export LLM_PRESERVER_VIEWS=~/llm-views

llm-preserver views --seed-store         # seed (or refresh) the ollama view
OLLAMA_MODELS=$LLM_PRESERVER_VIEWS/ollama OLLAMA_NOPRUNE=1 ollama serve

# in a second terminal:
ollama list                              # the archived models, minted names
ollama run qwen/qwen3.6-27b:q8_0         # example — any name from the usable list
```

Models are too large to shuttle between bulk storage and local disks,
so a *view* (spec 0002) makes a runtime able to find archived models
where they already live: a disposable directory of symlinks and
generated paperwork **outside** the archive, pointing into it. The
archive is read-only throughout — view generation works against a
read-only mount, writes nothing into the archive, and re-running
refreshes the view. Deleting a view loses nothing; the marker file at
its root (`llm-preserver-view.json`) is how both the tool and a human
recognize the tree as generated and disposable. A non-empty `--dest`
without that marker is refused untouched, and a `--dest` inside the
archive is always refused.

Eligibility is reported, never silent: an Ollama view links **GGUF
files with recorded SHA256s** only. Every run prints a breakdown split
into **usable** (each runnable model, with its exact `ollama run` name
once seeded) and **not usable** (one short reason per model —
safetensors-only models need a copying import; unhashed files have no
digest to name a blob with; sharded GGUF sets are not linked in phase
1). Companion files riding in GGUF artifacts (READMEs, docs) are
expected and stay out of the display; genuine problems (an unhashed
quant, a sharded set) surface under the model they belong to. If
nothing is eligible, nothing is written and the run exits 1.

Two modes:

- **Default (instructions only).** Writes nothing. Prints the
  recommended `--seed-store` flow first (run in place, no copy — the
  tool's stance per ADR 0001: views, not copies), then the official
  alternative — a Modelfile with `FROM <archive path>` plus
  `ollama create`, which *copies* the weights into Ollama's own store
  (`~/.ollama/models` by default); useful for a model you want
  permanently inside Ollama, or if an Ollama update ever breaks
  views.
- **`--seed-store` (best effort, no copy).** Ollama does not support
  external model stores; this mode is explicitly best-effort and says
  so loudly. It seeds a complete Ollama-shaped store at `--dest`: one
  `blobs/sha256-<digest>` symlink per eligible GGUF (Ollama names
  blobs by the SHA256 of the file bytes — exactly what the records
  already hold, so nothing is ever re-hashed), plus a synthesized
  manifest and minimal config blob per model, so the models are
  registered directly — no `ollama pull`, no `ollama create`, no
  network. One command finishes the job:

  ```bash
  OLLAMA_MODELS=~/ollama-view OLLAMA_NOPRUNE=1 ollama serve
  ```

  and `ollama list` / `ollama run` see the archived models
  immediately. Do **not** import seeded models with `ollama create`:
  measured live (ollama 0.32.0), `create` rewrites GGUF layers into a
  new full-size blob — exactly the copy this mode exists to avoid —
  which is why the tool writes the paperwork itself.
  `OLLAMA_NOPRUNE=1` matters: Ollama's startup prune deletes blobs no
  manifest references, and an Ollama-initiated change that orphans a
  seeded link would see it deleted (the archive file behind a pruned
  link is untouched — only the link dies).

Running a view server *alongside* a normal Ollama install — small
always-on models in Ollama's own store, large archived models served
from the view on a second port — is the recommended day-to-day shape;
the worked setup lives in [`ollama-hybrid.md`](ollama-hybrid.md).

What `--help` can't carry:

- **The store is swapped, not merged.** `OLLAMA_MODELS` pointed at the
  view hides models previously pulled into `~/.ollama/models`, and
  vice versa. Only Ollama's model store moves; keys, logs, and other
  runtime state stay in Ollama's normal home.
- **Symlink targets are absolute**, so the view assumes a stable
  archive mount point. A moved mount means regenerate the view —
  seconds, and nothing of value lives in it.
- **Refresh is safe for Ollama's paperwork.** Re-running rebuilds the
  tool's own content (blob symlinks pointing into the archive, the
  `modelfiles/` tree, the marker) and prunes stale entries for models
  no longer archived; manifests and blobs Ollama itself created in the
  view store are never touched.
- **Names are minted deterministically** from the archive layout —
  `<owner>/<repo>:<tag>` lowercased, the tag from the GGUF filename
  — no ranking or judgment, same stance as `discover`.
- **Blob names come from recorded digests, not from re-hashing.** The
  seeded store asserts the SHA256s the records hold; run
  `llm-preserver verify <archive>` first when seeding a view of an
  archive you did not create.
- **No template layers, by design.** The synthesized paperwork
  carries no template/params layers; Ollama falls back to the chat
  template embedded in the GGUF itself. Verified live for both model
  classes: embeddings and generate-class chat both render correctly
  through a seeded view.

| Code | Domain | Cause |
| --- | --- | --- |
| 0 | success | view seeded/refreshed, or instructions printed |
| 1 | archive/usage | path is not an archive; no eligible models (nothing written) |
| 2 | user input | unknown `--tool` value; `--dest` inside the archive; non-empty `--dest` without the view marker |
| 130 | interrupted | Ctrl-C — the view may be partial; re-run to refresh |
