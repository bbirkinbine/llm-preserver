# llm-preserver

Archives local LLMs against future access restrictions: pulls model
weights (GGUF quants, full Hugging Face snapshots) into a
runtime-independent local archive along with tokenizer/config files,
licenses, model cards, SHA256 checksums, and offline smoke tests — so
"I downloaded it once" becomes "I can still run this in two years."

> ## Status
>
> Published as a personal tool, not an actively
> maintained product. Issues and PRs are welcome but won't get fast
> turnaround. Pin a specific commit if you depend on a snapshot.

## Requirements

- Python 3.12+ and [`uv`](https://github.com/astral-sh/uv)
- Disk space for the model archive (models run tens to hundreds of GB;
  point the archive at a big disk or NAS)
- Optional: an inference runtime to smoke-test archived models
  (`ollama` and/or a `llama.cpp` build)

## Quick start

```bash
git clone https://github.com/bbirkinbine/llm-preserver.git
cd llm-preserver
uv sync                # install deps into a managed venv
uv run llm-preserver   # CLI entry point (not implemented yet — see docs/specs/)
```

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
