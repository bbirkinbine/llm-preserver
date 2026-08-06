# llm-preserver

Archives local LLMs for long-term offline use: pulls model weights
(GGUF quants, full Hugging Face snapshots) into a runtime-independent
local archive along with tokenizer/config files, licenses, model
cards, and SHA256 checksums — so "I downloaded it once" becomes
"I can still run this in two years." (Offline smoke tests are on the
roadmap.)

> ## Status
>
> Published as a personal tool, not an actively
> maintained product. Issues and PRs are welcome but won't get fast
> turnaround. Pin a specific commit if you depend on a snapshot.

## Requirements

- Python 3.12+ and [`uv`](https://github.com/astral-sh/uv)
- Disk space for the model archive (models run tens to hundreds of GB;
  point the archive at a big disk or NAS)
- Optional: an inference runtime to run archived models
  (`ollama` and/or a `llama.cpp` build; smoke-test integration is a
  planned feature)

## Quick start

```bash
git clone https://github.com/bbirkinbine/llm-preserver.git
cd llm-preserver
uv sync                # install deps into a managed venv

uv run llm-preserver init ~/models   # create an archive
uv run llm-preserver discover 'qwen3 coder' ~/models   # know only a name? search hub + model tree, then pull
uv run llm-preserver pull unsloth/Qwen3-4B-GGUF ~/models --include '*Q4_K_M*'
uv run llm-preserver status ~/models # inventory: what's on the shelf
uv run llm-preserver show Qwen/Qwen3-4B ~/models   # one model's record
uv run llm-preserver verify ~/models # audit: every file present, every hash intact
```

- **`discover`** — covers the step before an exact repo id: searches
  the hub, walks the model tree (originals, quants, fine-tunes — hub
  facts only, the tool never ranks), and lands in the same pull flow.
  No browser needed.
- **`pull --plan`** — dry run: shows exactly what a pull would
  download (files, sizes, disk check, companion advisories) without
  moving a byte.
- **`pull --whole-repo`** — archives a repo's entire tree instead of
  selected files (the full-precision masters).
- **`verify`** — audits disk against the records: every recorded file
  present (*complete*), every SHA256 intact (*valid*). `--quick` skips
  hashing for a seconds-long structural check; the exit code makes it
  cron-friendly. `--staging` surfaces abandoned downloads an interrupted
  pull left behind (a hash-free `.staging/` scan).
- **`remove`** — the archive's one sanctioned deletion path: a whole
  model, or a `--include` pattern subset (a quant swap), with the
  record, files, and interrupted-pull staging kept consistent. Always
  previews before it deletes.
- **`views`** — makes runtimes able to run archived models *in place*:
  a disposable tree of symlinks and generated paperwork outside the
  archive, pointing into it (the archive stays read-only). Phase 1
  targets Ollama (best effort — Ollama has no supported external-store
  mode, and the command says so loudly).

### Using with Ollama

Two commands connect the archive to a local Ollama, one in each
direction:

- **Archive what you already run** — `discover --match-ollama
  <name[:tag]>` reads the model's SHA256 from Ollama's local store
  and checks which Hugging Face repos hold byte-identical GGUFs
  (same-named repos often carry different builds whose outputs
  diverge; the digest settles it). The last line of output is the
  exact `pull` command to paste:

  ```bash
  llm-preserver discover --match-ollama bge-m3:latest
  # ...
  # 1 byte-identical match — run this to archive it:
  #   gpustack/bge-m3-GGUF  —  181142 downloads · 2025-07-14
  #     llm-preserver pull gpustack/bge-m3-GGUF --include bge-m3-FP16.gguf
  ```

- **Run what you've archived** — `views --seed-store` builds a
  disposable Ollama-shaped store of symlinks pointing into the
  archive, so archived models `ollama list` and serve in place with
  zero bytes copied. The recommended day-to-day shape runs that view
  server alongside a normal Ollama install on a second port — the
  worked setup lives in [`docs/ollama-hybrid.md`](docs/ollama-hybrid.md).

Both are detailed in [`docs/cli.md`](docs/cli.md) (the
`discover --match-ollama` and `views` sections).

### Install the command on your PATH (optional, recommended)

`uv run` works only from the project directory. To run `llm-preserver`
from anywhere — which is also what the resume hint printed by
interrupted pulls assumes — install it once as a uv tool:

```bash
uv tool install --editable .   # from the project directory
llm-preserver status ~/models  # now works from any directory
```

`--editable` runs the live source tree, so pulling new commits needs
no reinstall (re-run with `--reinstall` only when dependencies
change). `uv tool update-shell` wires up PATH if the shim isn't found.

Full command reference — selection patterns, model grouping, roles,
re-pull/idempotency behavior, exit codes, gated-repo auth — in
[`docs/cli.md`](docs/cli.md).

Deciding *what* to pull for a given model —
why a quant alone is a one-way export, and what a backup you can
re-quantize or fine-tune from actually contains — is
[`docs/what-to-archive.md`](docs/what-to-archive.md).

## Repository layout

- `src/llm_preserver/` — the package (CLI + archive/manifest/download logic)
- `tests/` — pytest suite
- `docs/specs/` — design specs; `0000-product.md` is the product-level plan
- `docs/` — ADRs, runbooks, workflow docs

## Development

```bash
uv run pytest                 # tests
uv run ruff check .           # lint
uv run ruff format --check .  # format check
uv run mypy src/              # type-check
uv run pre-commit install     # wire the local gate (+ secret scan) to every commit
```

See [`CLAUDE.md`](CLAUDE.md) for the per-project agent contract (what
Claude Code should and shouldn't do in this repo) and
[`WORKFLOW.md`](WORKFLOW.md) for the spec-driven loop.

## Acknowledgements

This project was developed with the assistance of AI tools.

## License

MIT — see [`LICENSE`](LICENSE).
