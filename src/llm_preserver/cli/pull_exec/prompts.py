"""The file listing the pull flow prints, and the patterns it asks for.

The y/N confirmations live in ``confirmations``; this module owns the
frames and the one free-text prompt they share.

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
    FLAT_KEYS,
    PATTERN_PROMPT,
    RESERVED_KEYS,
    ROLLUP_KEYS,
    chrome_lines,
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
from llm_preserver.pull_decline import QUIT_LINE, PullDeclined
from llm_preserver.text_window import fit_by_cost, wrapped_height


def _ask(prompt: str = PATTERN_PROMPT) -> str:
    """Ask for patterns, returning the raw answer for key matching.

    The sole prompt call for all three listing paths — pipe, fits, and
    every windowed frame — so the ``Abort`` split lives here once. It
    had no handler at all until spec 0021, which is why Ctrl-D here
    escaped every ``except`` in ``run_pull`` and died with click's bare
    ``Aborted!`` at exit 1: the one undocumented exit in the flow.
    """
    try:
        return str(typer.prompt(prompt, default="", show_default=False))
    # typer vendors click, so catch its own Abort (a different class).
    except typer.Abort:
        # click writes a prompt with no trailing newline and emits none
        # on EOF (it echoes one only for hidden input), so without this
        # the next line lands *on* the prompt: "files to pull (…): nothing
        # pulled: quit at the file listing". True on a real terminal, not
        # just under the runner — Ctrl-D leaves the cursor where it is.
        typer.echo()
        if is_interactive(sys.stdin):
            raise PullDeclined(QUIT_LINE) from None
        raise PullUserError(
            "file selection needed but stdin is not interactive: "
            "pass --include <pattern> or --whole-repo"
        ) from None


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
        PullDeclined: The human quit — ``q`` at a frame offering it, or
            an aborted prompt (Ctrl-D) with an interactive stdin.
        PullUserError: The prompt could not be answered because stdin
            is not interactive; the message names ``--include`` and
            ``--whole-repo`` as the bypass.
    """
    stream = sys.stdout
    flat = flat_lines(info.files)
    # A pipe gets the whole listing, unwindowed and unchanged.
    if not is_interactive(stream):
        typer.echo(flat_header(repo_id))
        _echo_all(flat)
        return _patterns(_ask())

    width = resolve_window_width(stream)
    # FLAT_KEYS is charged here, not merely printed below: a listing
    # that fits only *without* the key line must take the windowed path
    # instead of printing one physical row past the screen — spec 0018's
    # headline defect, which is reintroduced by echoing an uncharged
    # line. The chrome and the frame must name the same shape.
    if fits(
        flat,
        resolve_window_size(stream, chrome_lines(width, flat_header(repo_id), FLAT_KEYS)),
        width,
    ):
        typer.echo(flat_header(repo_id))
        _echo_all(flat)
        typer.echo(FLAT_KEYS)
        # _ask, never _answer_frame: q is the only key this frame
        # offers, so f/m/b/s stay patterns here. Re-prompting on them
        # would advertise by refusal what the key line does not offer.
        answer = _ask()
        if answer.strip() == "q":
            raise PullDeclined(QUIT_LINE)
        return _patterns(answer)

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
        sys.stdout, chrome_lines(width, header, ROLLUP_KEYS, prompt=rollup_prompt)
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
        sys.stdout, chrome_lines(width, header, widest_footer, widest_keys)
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
                raise PullDeclined(QUIT_LINE)
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
            raise PullDeclined(QUIT_LINE)
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
