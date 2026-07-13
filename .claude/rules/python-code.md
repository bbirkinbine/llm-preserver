---
paths:
  - "src/**/*.py"
  - "tests/**/*.py"
---

# Python code conventions

- **Files ≤ 300 lines.** Split aggressively; one concept per file. The
  `python-module-split` skill auto-invokes when a file approaches this.
- **Type hints required** on every function signature. `Any` requires a
  comment justifying it.
- **No bare `except:`**. Catch specific exceptions or `Exception` with a
  re-raise/log.
- **Docstrings:** Google-style. One-liner for trivial helpers; full
  args/returns/raises for public functions.
- **Imports:** absolute imports inside the package; relative only inside
  `__init__.py`.
- **Logging:** follow the project choice in `CLAUDE.md` / `pyproject.toml`.
  `structlog` is a good default for services; stdlib `logging` is fine for
  small libraries and CLIs. Avoid `print` for non-CLI diagnostics.

## Asserting on CLI output in tests

- **`click.unstyle()` the output before any substring assert on CLI
  output** (help screens, usage errors, anything rich renders). rich
  emits ANSI style codes when it detects a color-capable environment —
  GitHub Actions qualifies, local pytest does not — so a plain
  substring assert passes locally and fails only in CI. Bitten twice
  (spec 0005 `--all` rejection test; the `-h` help tests): the codes
  land mid-substring and split the text being asserted.
- Reproduce the CI rendering locally with
  `FORCE_COLOR=1 GITHUB_ACTIONS=true TERM=xterm-256color uv run pytest ...`
  before pushing a fix.

## External-reference provenance (implement phase)

Any value or claim whose correctness depends on matching an external
authority — listed in the spec's `## External references` section — must
be populated by `WebFetch` in-session with the source URL + retrieval
date + license pinned in a header comment near where the value is
defined. Reconstructing such values from training is the fabrication
failure the spec template warns against — if the source isn't fetchable,
the spec's provenance is wrong; fix the spec, not the code.

Copyleft-licensed sources (GPL/AGPL/LGPL) are consult-only in a
permissive repo: do not copy their content verbatim and do not check the
project into `vendor/`. See `docs/specs/README.md` `## External
references` for the categories this covers and the license
compatibility rules.
