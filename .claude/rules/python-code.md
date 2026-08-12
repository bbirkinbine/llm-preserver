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
- **Never *count* lines through the shared `combined_output` helper.**
  It returns `result.output + result.stderr`, and this click version
  already folds stderr into `result.output` — so every stderr line
  appears twice. Substring asserts (`x in out`) are unaffected, which is
  why the helper is safe everywhere else and the trap is invisible until
  someone asserts `len(matches) == 1`. Count on
  `click.unstyle(result.output)` alone. Cost an implementation round on
  spec 0017: a note printed once looked like it printed twice, and
  "fixing" the product would have moved a diagnostic off stderr for no
  reason. Verify a suspected double-print by invoking the CLI directly
  and printing `result.output` and `result.stderr` separately before
  changing any product code.

## Platform-conditional APIs

- **Reach a platform-only API through `getattr`, never a direct
  attribute reference.** `mypy` type-checks against the platform it
  runs on, so `os.chflags(...)` and `st.st_flags` pass locally on macOS
  and fail CI on Linux with `Module has no attribute`. Bind once at
  module scope (`_chflags = getattr(os, "chflags", None)`) and let every
  caller degrade to a no-op where the mechanism is absent. Bitten by
  spec 0017's BSD file-flag handling — the local gate was green and the
  PR's first CI run was red.
- Reproduce CI's view before pushing: `uv run mypy --platform linux src/`.
  Cheaper than a round trip through Actions.
- Note the related coverage gap: tests guarded by
  `skipif(not hasattr(os, "chflags"))` do not run on Linux CI at all, so
  a platform-only code path can be type-clean and completely unexercised
  there. Say so in the test module rather than letting a green CI imply
  coverage it does not have.

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
