"""What surrounds the rows: footers, key hints, prompts, and notes.

Split from ``rows`` when spec 0018's review round grew the module past
the 300-line rule. The division is what a frame *says about itself*
versus what its rows say: nothing here reads a ``RepoFile``.

Two ideas hold this together. **Offered keys only** — a character the
frame does not act on is not advertised, and ``offered_keys`` is the
single source both the hint line and the "that does nothing here" note
read, so the two can never drift apart. And **a reserved key is always
a key** inside a windowed listing: pressing an inactive one re-prompts
rather than falling through as a glob that matches nothing, which is
how ``m`` on the last page used to earn a 210-row error wall.
"""

from collections.abc import Sequence

from llm_preserver.cli.pull_exec.listing.rows import ListingGroup
from llm_preserver.render import clean_text


def footer_line(first: int, last: int, total: int, *, more: bool, back: bool) -> str:
    """Say which rows are on screen, out of how many exist, and what works.

    Unlike discover's footer, ``total`` here is a true total: the whole
    file list arrived in the single metadata call the pull already made
    (spec 0003), so nothing is being discovered as the human pages.

    Args:
        first: 1-based index of the window's first row.
        last: 1-based index of its last row, inclusive.
        total: Files in the repo.
        more: Whether rows remain after this window.
        back: Whether an earlier window exists.

    Returns:
        The footer line. Callers sizing a frame must charge the
        *widest* form this can take — both indices at their largest —
        since ``first`` grows as the human pages.
    """
    text = f"showing {first}-{last} of {total}"
    if more:
        text += " — more (m)"
    if back:
        text += " · back (b)"
    return text


ROLLUP_KEYS = "f = list every file (paged), q = quit"

# Characters the windowed listing answers to. All five are keys in
# both frames whether or not they currently do something: pressing an
# inactive one re-prompts (see ``unavailable_note``) rather than being
# read as a pattern that matches nothing. Discover's ``prompt_pick``
# has always re-prompted; this brings the listing in line with it.
RESERVED_KEYS = ("f", "m", "b", "s", "q")

# fnmatch metacharacters. A directory name carrying one cannot be
# turned into a working example pattern — `*UD-Q4[x]*` is a character
# class, not a literal — so the generic example stands instead of
# teaching a pattern that would match nothing.
_GLOB_METACHARACTERS = "*?[]"


def example_pattern(groups: Sequence[ListingGroup]) -> str | None:
    """A pattern from this repo's own roll-up, ready to type.

    The roll-up's whole point is to put group names in front of the
    human, and the most natural response is to type one — which matches
    nothing for a directory, because patterns are matched against the
    full repo path and need wildcards. Showing a real one in the prompt
    teaches the idiom with a name already on screen (review round,
    2026-08-12).

    A shard-set group is already a glob (``model-*.safetensors``), so
    it is offered verbatim; a directory is wrapped in ``*``.

    Args:
        groups: The roll-up's groups, in hub order.

    Returns:
        A ready-to-paste pattern, or None when no group yields one — in
        which case the generic example stands.
    """
    for group in groups:
        if group.is_shard_set:
            return clean_text(group.name, single_line=True)
        if not group.is_directory:
            continue
        name = clean_text(group.name, single_line=True)
        if name and not any(character in name for character in _GLOB_METACHARACTERS):
            return f"*{name}*"
    return None


def pattern_prompt(example: str | None = None) -> str:
    """The pattern prompt, optionally showing one from this repo.

    Args:
        example: A ready-to-type pattern, or None for the generic
            example.

    Returns:
        The prompt text, without click's trailing ``": "``.
    """
    # The leading * matters: patterns match the full repo path, so bare
    # "Q4_K_M*" matches nothing (live mispull, 2026-07-12).
    lead = example or "*Q4_K_M*"
    return f"files to pull (comma-separated patterns, e.g. {lead} or *.gguf,*mmproj*)"


def unavailable_note(key: str, *, on_rollup: bool, offered: Sequence[str]) -> str:
    """Explain why a reserved key does nothing on this frame.

    Args:
        key: The reserved key the human pressed.
        on_rollup: Whether the roll-up frame is showing.
        offered: The keys that do work here, in display order.

    Returns:
        One line: the reason, then what does work. The frame is not
        reprinted — the key line is directly above it.
    """
    if on_rollup:
        reason = "press f to list the files first" if key in ("m", "b") else "already the summary"
    elif key == "f":
        reason = "already listing every file"
    elif key == "m":
        reason = "no further pages"
    elif key == "b":
        reason = "this is the first page"
    else:
        reason = "no summary for this repo — every file is at the top level"
    return f"{reason} — {', '.join(offered)}, or type a pattern"


def window_keys(*, more: bool, back: bool, summary: bool) -> str:
    """Key hints for an expanded window — offered keys only, quit last.

    Offering a key that does nothing is worse than not offering it: the
    same characters are patterns when they are not keys, so the hint
    line is also the disambiguation (spec 0018, adjudication 5).

    Args:
        more: Whether rows remain after this window.
        back: Whether an earlier window exists.
        summary: Whether a roll-up frame exists to return to.

    Returns:
        The comma-separated hint line.
    """
    hints = {
        "m": "m = more",
        "b": "b = back a page",
        "s": "s = summary",
        "q": "q = quit",
    }
    return ", ".join(hints[key] for key in offered_keys(more=more, back=back, summary=summary))


def offered_keys(*, more: bool, back: bool, summary: bool) -> list[str]:
    """Which keys an expanded window currently answers to, in display order.

    The single source of what is offered: ``window_keys`` renders these
    as hints, and the loop names the same set back when a reserved key
    that is *not* in it gets pressed. Deriving one from the other by
    string-splitting a rendered line would let the hint and the note
    drift apart.

    Args:
        more: Whether rows remain after this window.
        back: Whether an earlier window exists.
        summary: Whether a roll-up frame exists to return to.

    Returns:
        The active key letters, quit last.
    """
    keys = []
    if more:
        keys.append("m")
    if back:
        keys.append("b")
    if summary:
        keys.append("s")
    keys.append("q")
    return keys
