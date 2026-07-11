# CLI usage

The user-facing manual for the `llm-preserver` command line. This
document grows with the tool: every feature branch that adds or
changes a command updates it in the same change. `--help` on any
command is generated from the same source and is always current:

```bash
uv run llm-preserver --help
uv run llm-preserver pull --help
```

Commands documented here: `init`, `pull`, `status`, `show`. Planned
features (verify, full snapshot, cache import, runtime views) are
listed in the roadmap in [`specs/0000-product.md`](specs/0000-product.md)
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

## pull — download selected files from a Hugging Face repo

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
- `--model CREATOR/MODEL` — canonical model directory override. Quant
  repos are grouped under the *original* model's directory; without
  this flag the tool infers the grouping from the repo's `base_model`
  metadata and asks for confirmation. No metadata and no flag is a
  hard stop.
- `--role ROLE` — assign a curator role (`chat`, `coding`,
  `embedding`, `reranker`, `multimodal`) at pull time; repeatable.
  Without it the model is archived role-less and shows under
  "(no role)" in `status` until you assign one.
- `--refresh-docs` — replace documentation files whose upstream
  content changed: the superseded doc is unlocked, replaced with the
  newly downloaded and hashed version, re-locked, and the record and
  manifest are updated. Applies to doc paths only — a changed
  *weight* is always a hard stop, flag or no flag.
- `--verbose` — per-file progress, resolved commit, staging paths,
  and underlying client detail on failures.

Behavior worth knowing:

- **Re-pulls are idempotent.** A file already archived with a
  matching hash is skipped ("already archived"); nothing re-downloads.
  A file whose upstream content *changed* is a hard stop — the
  archive never silently overwrites. For documentation files the stop
  names the way out ("re-run with --refresh-docs to replace this
  documentation file"); for weights there is no override.
- **Interrupted pulls are safe to retry.** Re-run the same command;
  completed files in staging are reused, and the record is only ever
  written after every selected file is fully on disk and hashed.
- **Gated/private repos** use Hugging Face's own login: run
  `hf auth login` once (or set `HF_TOKEN`). The tool takes no token
  flags and never stores or logs the token.

Exit codes name the fault domain so failures can be triaged without
reading source:

| Code | Domain | Typical cause / next step |
| --- | --- | --- |
| 1 | archive/usage | path is not an archive; bad arguments |
| 2 | user input | unknown repo id; gated repo not accepted; no matching files |
| 3 | local environment | network unreachable, disk full — check your machine |
| 4 | hub-side | 5xx or rate limiting — retry later; not your fault |
| 5 | integrity | hash mismatch after download — the file never entered the archive |

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
