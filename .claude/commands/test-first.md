---
description: Invoke the test-first subagent to write failing pytest tests from a spec. Never implements.
argument-hint: [path to spec, or blank for latest]
---

Invoke the `test-first` subagent.

Spec selection:

- If `$ARGUMENTS` is a path to a markdown file under `docs/specs/`, pass that path.
- Otherwise, find the most recent spec — the file in `docs/specs/` with the highest `NNNN-` prefix (excluding `README.md`).

The subagent writes failing tests only. It does NOT implement. It returns: the test file paths it wrote, the failing-test output it captured, and a one-line summary per test describing the behavior pinned down.

Surface its output verbatim. Confirm the tests fail with the expected failure mode (NotImplementedError, AttributeError on a missing function, AssertionError — not ImportError on a typo) before moving to implementation.
