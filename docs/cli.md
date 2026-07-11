# CLI usage

The user-facing manual for the `llm-preserver` command line. This
document grows with the tool: every feature branch that adds or
changes a command updates it in the same change. `--help` on any
command is generated from the same source and is always current:

```bash
uv run llm-preserver --help
uv run llm-preserver pull --help
```

Commands documented here: `init`, `pull` (selective and `--all` full
snapshot), `status`, `show`. Planned features (verify, cache import,
runtime views) are listed in the roadmap in
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
- `--all` — full snapshot: download the repo's whole tree (see the
  next section). Mutually exclusive with `--include`.
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
- `--yes` — auto-accept the size confirmation (the `--all` totals
  prompt). Never the grouping confirm: identity needs a deliberate
  value, so scripted pulls pass `--model` for that.
- `--verbose` — per-file progress, resolved commit, staging paths,
  and underlying client detail on failures.

Behavior worth knowing:

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

## pull --all — archive a whole repo (full snapshot)

Selective pull acquires runnable derivatives; `--all` acquires the
master copy — the original full-precision tree that later formats
derive from. Quantization is one-way lossy, so the original is the
only copy that can be re-quantized, fine-tuned, or loaded by
non-GGUF stacks later.

```bash
# archive the original Qwen3.6-27B tree (~54GB of safetensors shards):
uv run llm-preserver pull Qwen/Qwen3.6-27B --all ~/models
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
  a snapshot of the same repo, and mixing selective + `--all` of the
  same repo, stay fine.
- **Disk preflight.** File sizes come from the same metadata call, so
  the pull refuses (exit 3, local environment) before downloading
  anything when free space at the archive path is short, stating
  required vs. available.
- **Interrupted pulls are safe to re-run.** An interrupted `--all`
  records nothing; re-running the same command re-plans the whole
  tree. Resume comes from the download client reusing files already
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
