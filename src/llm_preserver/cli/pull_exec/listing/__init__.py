"""Rendering for pull's interactive file listing (spec 0018): data → lines.

Pure by design: no terminal, no ``typer``, no I/O. The loop in
``pull_exec.prompts`` decides *which* frame to print and asks
``cli.window`` how much room it has; everything about what a frame says
lives here, where one pytest can check the arithmetic without a TTY.

Two modules, split at the 300-line rule when the review round grew the
original file: ``rows`` renders what the listing is *about* — grouping
files by directory, sizing them, scrubbing hub text — and ``frame``
renders what surrounds those rows: footers, key hints, the pattern
prompt, and the note explaining a key that does nothing here. The
public API is flat, so callers import from the package and never need
to know which half a name lives in.
"""

from .frame import (
    RESERVED_KEYS,
    ROLLUP_KEYS,
    example_pattern,
    footer_line,
    offered_keys,
    pattern_prompt,
    unavailable_note,
    window_keys,
)
from .rows import (
    ListingGroup,
    fits,
    flat_header,
    flat_lines,
    group_files,
    kind_note,
    rollup_lines,
    summary_header,
)

__all__ = [
    "RESERVED_KEYS",
    "ROLLUP_KEYS",
    "ListingGroup",
    "example_pattern",
    "fits",
    "flat_header",
    "flat_lines",
    "footer_line",
    "group_files",
    "kind_note",
    "offered_keys",
    "pattern_prompt",
    "rollup_lines",
    "summary_header",
    "unavailable_note",
    "window_keys",
]
