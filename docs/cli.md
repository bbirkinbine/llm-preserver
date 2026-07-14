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
`verify`. Planned features (cache import, runtime views, smoke tests)
are listed in the roadmap in
[`specs/0000-product.md`](specs/0000-product.md)
and appear here when they ship.

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

- **Original repo** (`Qwen/Qwen3.6-27B`) — the canonical
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
    --include '*Q8_0*' --model Qwen/Qwen3.6-27B
```

The trailing archive path is optional whenever `LLM_PRESERVER_ARCHIVE`
is set (see the init section) — with the variable exported, every
example above works with the path omitted entirely.

`REPO_ID` is an exact hub repo id (`namespace/repo`) — the tool never
resolves fuzzy names. The pull downloads the selected files plus the
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
- `--model CREATOR/MODEL` — canonical model directory override. Quant
  repos are grouped under the *original* model's directory; without
  this flag the tool infers the grouping from the repo's `base_model`
  metadata and asks for confirmation. Grouping is format-directed: a
  GGUF/MLX repo is a *conversion* and groups under its `base_model`; a
  safetensors tree with a `base_model` is a *derived model* (different
  weights) and defaults to its own repo id, with the base mentioned as
  lineage. A repo with *no* `base_model` defaults to the repo id
  regardless of format. Every default is confirmed; metadata that is
  present but unusable is a hard stop.
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
  (X to download)…?" prompt, asked on every pull mode). Never the
  grouping confirm: identity needs a deliberate value, so scripted
  pulls pass `--model` for that.
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
  fix. Three cross-repo checks ride along: an explicit `--model` that
  contradicts the repo's declared `base_model` (catches a copy-pasted
  `--model` filing one model under another's directory — the pull
  still honors `--model`), an adapter repo whose declared base model
  isn't archived, and a quant repo whose full-precision master isn't
  archived — the latter two naming the follow-up `llm-preserver pull`
  command. The grouping-mismatch check flags likely *human error*, so
  it prints first with a distinct `warning:` prefix (highlighted on a
  terminal) instead of `advisory:`. Advisories are archive-aware (a
  companion archived by an earlier pull stays silent) and never change
  the selection — the tool never auto-adds. When the tree ships an
  `adapter_config.json`, the pull fetches that one small file (to a
  temp dir, never the archive) to read its base-model pointer, and
  says so.
- **Renamed parents resolve to their current name.** A repo's card
  can declare its base model by a pre-rename name the hub now
  redirects; the pull spends one light metadata check resolving it
  (announced with an INFO line), so grouping proposals, advisories,
  and archive records carry the current id — a name that still
  resolves years later. If the declared base can't be resolved, the
  declared name stands and the pull proceeds normally.
- **Non-interactive runs never hang or die vaguely.** When stdin
  cannot answer a confirmation (cron, CI, piped input exhausted), the
  pull exits 2 with a message naming the bypass: `--model` for the
  grouping confirm, `--yes` for the size confirmation.

- **Re-pulls are idempotent.** A file already archived with a
  matching hash is skipped ("already archived"); nothing re-downloads.
  A file whose upstream content *changed* is a hard stop — the
  archive never silently overwrites. For documentation files the stop
  names the way out ("re-run with --refresh-docs to replace this
  documentation file"); for weights there is no override.
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
  `<archive-root>/.staging/<creator>/<model>/` (a sibling of
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
  --include '<pattern>' --model <creator>/<model>`. It is the exact
  direct command that reproduces this pull — absolute archive path
  (works without `LLM_PRESERVER_ARCHIVE` and from any directory),
  shell-quoted patterns, and the grouping you just confirmed replayed
  as `--model` so the continue lands in the same model directory.
  `--hf-logging` rides along when the pull ran with it — the
  stalled-transfer scenario the hint serves is the one that flag
  exists for (`--verbose` does not; the hint replays the pull's
  shape, not general diagnostics).
  Because re-pulls are idempotent, running it later downloads only
  what is still missing. Ctrl-C during the transfer prints the hint
  as the final line — directly above your next shell prompt — and
  exits 130 (128 + SIGINT); that interrupt-time print happens on
  *every* pull, including one whose shape you typed yourself (where
  it usefully carries the resolved `--model` your history entry may
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
| 2 | user input | unknown repo id; gated repo not accepted; no matching files |
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
    --include '*Q8_0*' --model Qwen/Qwen3.6-27B --plan
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
  lines — scripted for real, those still need `--model` / a narrower
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
# → confirms the grouping (an original repo has no base_model, so the
#   repo id itself is offered as the canonical model directory)
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
- **Re-running a completed snapshot downloads nothing.** After the
  grouping confirmation it reports "nothing to pull: every selected
  file is already archived" and exits 0 — no size prompt, no
  downloads. (Pass `--model` to skip the grouping question on
  re-runs.)
- **One source repo per format subdirectory.** A second same-format
  snapshot from a *different* source repo is refused (two verbatim
  trees cannot share one directory honestly) — archive it under a
  different `--model` home, or pull selected files instead. Re-running
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
# showing 20 — more available (m)
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
   anywhere; both listings page with `m`.
3. **Pull** — "pull this repo" first asks *how* to archive:
   `1 = pick files` (quant repos — choose your quant from the
   listing) or `2 = whole-repo snapshot` (originals/masters — the
   tree is the artifact, spec 0004 semantics). Then the exact `pull`
   flow: file listing (mode 1 only), advisories, the normal grouping
   confirmation (pull proposes the canonical home — the declared
   base for a quant, the repo's own id for an original or fine-tune
   — and you answer y/n), then the size confirmation. No `--model` flag needed, hub
   metadata never names an archive directory without your yes, and
   the grouping-mismatch warning stays silent because there is no
   override to mismatch. Once the confirmations pass, the pull prints
   the **resume-command hint** — the direct `llm-preserver pull …`
   line that reproduces this exact pull without re-driving the
   navigation (see the pull section). Only the `discover` invocation
   is in your shell history, so this line is the one record of the
   pull you assembled; interrupt the download with Ctrl-C and it
   reprints as the final line, ready to paste when you come back.

`--plan` makes the final pull the dry run (verify, then re-run for
real); `--verbose` and `--hf-logging` as in `pull`. Failures map to the same exit codes
as `pull` (network 3, hub-side 4). Discovery is deliberately
interactive-only: scripts already have exact repo ids, `--include`,
`--yes`, and `--plan`. The tool shows facts and takes your picks — it
never recommends, scores, or auto-selects.

## status — inventory table

```bash
uv run llm-preserver status ~/models
```

One row per archived model: roles (role-less models group under
"(no role)"), formats, completeness. The fast answer to "what is on
the shelf."

## show — one model's record

```bash
uv run llm-preserver show Qwen/Qwen3.6-27B ~/models   # path optional with the env var
```

Prints everything archived for one model: artifacts, per-file
provenance and hashes, pinned commits, license, source repos.

## verify — audit the archive against its records

```bash
uv run llm-preserver verify ~/models          # full audit: re-hash everything
uv run llm-preserver verify                   # same, archive from $LLM_PRESERVER_ARCHIVE
uv run llm-preserver verify --quick           # existence + size only, seconds not hours
uv run llm-preserver verify --model Qwen/Qwen3.6-27B   # one model, not the whole shelf
```

The whole-archive drift detector (spec 0009), BagIt-style: each
model's record enumerates its *expected* files, and verify checks disk
against it — existence, then size, then a full SHA256 re-hash, in that
order, so a missing or truncated file is caught without paying for a
hash. Fully offline; it never contacts the hub.

One result line per model (valid models included — an audit should
read as "everything was checked"), with per-file detail lines under
any model that drifted, then an archive-wide totals summary. The
categories:

- **valid** — every recorded file exists and re-hashes to its recorded
  SHA256.
- **complete** — every recorded file exists at its recorded size, but
  nothing was hash-validated: every `--quick` result caps out here,
  and so does a full run over a record that carries no hashes at all
  (e.g. a future unverified cache import) — "valid" is never claimed
  for a model whose digests were not actually checked.
- **incomplete** — recorded files are missing from disk (each is
  named). The tool never deletes, so this means out-of-band deletion
  or a partial copy.
- **invalid** — everything is present, but at least one file's size or
  hash disagrees with the record (expected and actual are shown), or a
  file could not be read. Bitrot, tampering, or a failing disk.
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

Exit codes are the cron contract — a scheduled run needs no output
parsing:

| Code | Meaning |
| --- | --- |
| 0 | clean — every checked model valid or complete; unhashed/unrecorded findings and manifest-refresh warnings are informational and do not change the code. An empty archive also exits 0, saying so explicitly |
| 1 | archive/usage — path is not an archive; malformed `--model` syntax |
| 2 | user input — `--model` names no archived model (the error lists the archive's model ids so a typo self-corrects). The CLI framework's own usage errors — a missing path with no `LLM_PRESERVER_ARCHIVE` set, an unknown flag — also exit 2, so treat 2 as "fix the invocation", not specifically "unknown model" |
| 5 | integrity — drift found: any model incomplete, invalid, missing its record, or with an unreadable record or payload file |
| 130 | interrupted — Ctrl-C; the in-progress model's sidecar is untouched |

A full run re-reads every archived byte, so it is disk-bound: figure
roughly 2.5 hours per terabyte over gigabit to a NAS, much faster on
local storage. `--quick` catches deletion and truncation (not bitrot)
in seconds and suits a pre-backup sanity pass; the full run is the
quarterly fixity check.
