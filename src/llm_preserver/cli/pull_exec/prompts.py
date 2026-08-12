"""Interactive prompts the pull flow asks: confirmations, file picking.

Prompt classification keys on the strings ``pull_model`` composes —
the tool owns both sides of that seam.

Spec 0018 turned the file listing into two frames. It used to echo
every file in the repo, which put 171 rows into a 24-line terminal on
the live run that triggered the spec — one stage after ``discover``'s
own windowed frames, and unrecoverable under ``screen``'s default
scrollback. Now an overflowing listing opens on a directory roll-up and
keeps every file one ``f`` away, paged with ``m``/``b``.

Three rules hold the shape together:

- **A pipe prints everything, flat.** A pipe has no scroll problem; it
  has a file. So the interactivity verdict is taken *before* any budget
  is asked for — ``resolve_window_size`` would answer with the fixed
  non-TTY window, which is right for discover and wrong here.
- **A listing that fits is untouched.** No roll-up, no keys, no
  footer — the frame that shipped. The roll-up answers a wall, so it
  appears only when there is one.
- **Offered keys only.** A character that is not offered is part of a
  pattern, so the key line is also the disambiguation. Keys match the
  raw stripped input *before* the comma split: ``f`` is the key, ``f,``
  is the pattern list ``["f"]``.
"""

import sys
from collections.abc import Sequence

import typer

from llm_preserver.cli.pull_exec.listing import (
    RESERVED_KEYS,
    ROLLUP_KEYS,
    example_pattern,
    fits,
    flat_header,
    flat_lines,
    footer_line,
    group_files,
    offered_keys,
    pattern_prompt,
    rollup_lines,
    summary_header,
    unavailable_note,
    window_keys,
)
from llm_preserver.cli.window import is_interactive, resolve_window_size, resolve_window_width
from llm_preserver.hub import PullUserError, RepoInfo
from llm_preserver.render import clean_text
from llm_preserver.text_window import fit_by_cost, wrapped_height

# The leading * matters: patterns match the full repo path, so bare
# "Q4_K_M*" matches nothing (live mispull, 2026-07-12).
PATTERN_PROMPT = "files to pull (comma-separated patterns, e.g. *Q4_K_M* or *.gguf,*mmproj*)"


def confirm_or_stop(prompt: str, assume_yes: bool) -> bool:
    """Confirm interactively; deterministic stop when stdin cannot answer.

    ``--yes`` auto-accepts the *size* confirmation only — grouping is an
    identity decision that needs an explicit ``--model`` value, never a
    blanket yes. When the prompt cannot be answered (non-interactive
    stdin, exhausted piped input), click raises ``Abort``; that becomes
    a ``PullUserError`` (exit 2) naming the bypass, so scripted pulls
    never die with an undocumented exit 1 (spec 0004 adjudications).
    Prompt classification keys on the strings ``pull_model`` composes —
    the tool owns both sides of this seam.
    """
    cleaned = clean_text(prompt, single_line=True)
    is_size_confirm = cleaned.startswith("pull ")
    if assume_yes and is_size_confirm:
        return True
    try:
        return bool(typer.confirm(cleaned))
    # typer vendors click, so catch its own Abort, not the click
    # package's (they are different classes).
    except typer.Abort:
        if is_size_confirm:
            hint = "re-run with --yes to accept the size confirmation"
        elif "every weight" in cleaned:
            hint = "narrow --include, or run interactively"
        else:
            hint = "pass --model <creator>/<model> to choose the canonical model directory"
        raise PullUserError(f"confirmation needed but stdin is not interactive: {hint}") from None


def _ask(prompt: str = PATTERN_PROMPT) -> str:
    """Ask for patterns, returning the raw answer for key matching."""
    return str(typer.prompt(prompt, default="", show_default=False))


def _patterns(raw: str) -> list[str]:
    """Split an answer into the include patterns the pull will use."""
    return [pattern.strip() for pattern in raw.split(",") if pattern.strip()]


def _answer_frame(prompt: str, *, on_rollup: bool, active: Sequence[str]) -> str:
    """Ask until the answer is a pattern or a key this frame acts on.

    A reserved key that does nothing here re-prompts with one line
    saying why, rather than falling through as a pattern. It used to
    fall through, and ``m`` on the last page — after seventeen frames
    had advertised ``m = more`` — matched nothing and printed a 210-row
    "available files" wall on a 24-row terminal, a bigger wall than the
    one this spec removed (review round, 2026-08-12).

    The frame is not reprinted, matching discover's ``prompt_pick``:
    the key line is one line above, and reprinting would cost a screen
    to say one sentence.

    Args:
        prompt: The pattern prompt for this frame.
        on_rollup: Whether the roll-up frame is showing, which decides
            how an inactive key is explained.
        active: The keys this frame acts on, in display order.

    Returns:
        The raw answer, for the caller to match keys and split.
    """
    while True:
        answer = _ask(prompt)
        key = answer.strip()
        if key not in RESERVED_KEYS or key in active:
            return answer
        typer.echo(unavailable_note(key, on_rollup=on_rollup, offered=active))


def _chrome(width: int | None, *texts: str, prompt: str = PATTERN_PROMPT) -> int:
    """Physical lines a frame spends on everything that is not a row.

    The prompt is charged too — click renders it as ``{text}: `` and it
    is 76 characters, so a narrow terminal pays two lines for it, and a
    frame naming one of the repo's own directories in its example pays
    for the longer text. Each caller passes the *widest* form its
    chrome can take, following ``tree_chrome_lines``: a frame must
    never be sized against a shorter shape than the one it prints.
    """
    return sum(wrapped_height(text, width) for text in texts) + wrapped_height(f"{prompt}: ", width)


def _echo_all(lines: list[str]) -> None:
    """Print a frame's lines in order."""
    for line in lines:
        typer.echo(line)


def prompt_for_selection(info: RepoInfo, repo_id: str) -> list[str]:
    """List the repo's files and prompt for include patterns.

    Takes the already-fetched metadata — one metadata call per pull
    (spec 0003), shared with ``pull_model`` via its ``repo_info`` seam.
    Nothing here reaches the network, so paging is free in both
    directions.

    Args:
        info: The repo metadata whose ``files`` are being chosen from.
        repo_id: The hub id, for the header. Hub-supplied text, same
            trust class as the file paths, so it is scrubbed like them.

    Returns:
        The include patterns the human typed, stripped and split on
        commas; empty when they answered with nothing.

    Raises:
        PullUserError: The human answered ``q`` at a frame offering it.
    """
    stream = sys.stdout
    flat = flat_lines(info.files)
    # A pipe gets the whole listing, unwindowed and unchanged.
    if not is_interactive(stream):
        typer.echo(flat_header(repo_id))
        _echo_all(flat)
        return _patterns(_ask())

    width = resolve_window_width(stream)
    if fits(flat, resolve_window_size(stream, _chrome(width, flat_header(repo_id))), width):
        typer.echo(flat_header(repo_id))
        _echo_all(flat)
        return _patterns(_ask())

    return _windowed_selection(info, repo_id, flat, width)


def _windowed_selection(
    info: RepoInfo, repo_id: str, flat: list[str], width: int | None
) -> list[str]:
    """Drive the roll-up and expanded frames until a pattern is typed.

    The frame chain is the plan-round adjudication: the flat listing has
    already failed to fit, so offer the roll-up when it has directories
    to summarize *and* itself fits; otherwise open straight onto the
    paged listing, where ``s`` is not offered because there is nothing
    to go back to. One rule covers both gaps — a repo of root files with
    no directories, and a roll-up too tall for the screen.
    """
    header = summary_header(repo_id, info.files)
    rollup = rollup_lines(group_files(info.files))
    total = len(flat)
    # The roll-up is worth a frame only when it actually collapses
    # something. This was written as `any("/" in path)` — provably dead
    # then, because directories were the only thing that collapsed and
    # a repo without them produced roll-up lines identical to its flat
    # lines. Adding shard-set grouping made it a live condition that
    # said the wrong thing: a flat repo of 96 shards collapses to 14
    # lines and still had no directory, so the frame it needed most was
    # withheld (live use, 2026-08-12). Comparing the two listings asks
    # the question directly and cannot go stale the next time a group
    # kind is added.
    collapses = len(rollup) < len(flat)
    groups = group_files(info.files)
    # The roll-up's example names one of the repo's own directories:
    # its whole purpose is to put those names on screen, and typing one
    # un-globbed matches nothing.
    rollup_prompt = pattern_prompt(example_pattern(groups))
    rollup_budget = resolve_window_size(
        sys.stdout, _chrome(width, header, ROLLUP_KEYS, prompt=rollup_prompt)
    )
    offer_rollup = collapses and fits(rollup, rollup_budget, width)

    costs = [wrapped_height(line, width) for line in flat]
    widest_keys = window_keys(more=True, back=True, summary=offer_rollup)
    # Both indices at their largest: `first` grows as you page, so
    # charging the chrome for `showing 1-…` under-sizes every later
    # frame by the digits it gained. Measured at 42 columns on the
    # spec's own repo: `showing 1-171 of 171 — more (m) · back (b)` is
    # 42 characters and `showing 100-105 of 171 …` is 44, so the frame
    # printed 25 physical rows against a 24-row screen — the headline
    # criterion, failing in the width band nobody looked at (review
    # round, 2026-08-12, found independently by both reviewers).
    widest_footer = footer_line(total, total, total, more=True, back=True)
    window_budget = resolve_window_size(
        sys.stdout, _chrome(width, header, widest_footer, widest_keys)
    )

    showing_rollup = offer_rollup
    start = 0
    history: list[int] = []
    while True:
        if showing_rollup:
            typer.echo(header)
            _echo_all(rollup)
            typer.echo(ROLLUP_KEYS)
            answer = _answer_frame(rollup_prompt, on_rollup=True, active=["f", "q"])
            key = answer.strip()
            if key == "q":
                raise PullUserError("nothing pulled: quit at the file listing")
            if key == "f":
                # Keep the offset: the spec calls the two frames a
                # toggle, and a toggle that dumps you back at page one
                # costs eight keypresses to undo (review round).
                showing_rollup = False
                continue
            return _patterns(answer)

        end = fit_by_cost(costs, start, window_budget)
        more, back = end < total, bool(history)
        active = offered_keys(more=more, back=back, summary=offer_rollup)
        typer.echo(header)
        _echo_all(flat[start:end])
        typer.echo(footer_line(start + 1, end, total, more=more, back=back))
        typer.echo(window_keys(more=more, back=back, summary=offer_rollup))
        answer = _answer_frame(PATTERN_PROMPT, on_rollup=False, active=active)
        key = answer.strip()
        if key == "q":
            raise PullUserError("nothing pulled: quit at the file listing")
        if key == "m" and more:
            history.append(start)
            start = end
            continue
        if key == "b" and back:
            start = history.pop()
            continue
        if key == "s" and offer_rollup:
            showing_rollup = True
            continue
        return _patterns(answer)
