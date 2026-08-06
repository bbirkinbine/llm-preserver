"""Frame and prompt plumbing shared by the discover stages.

Split out of ``flow`` when spec 0015 replaced accumulate-paging with
windowed frames and the stage logic outgrew the 300-line rule. Mirrors
the ``cli/pull_exec`` split: plumbing here, stage logic next door.
"""

import typer

from llm_preserver.discover import DiscoveryPage, Option, parse_pick
from llm_preserver.hub import PullUserError

# Frame separator: each window is its own frame, so scrollback — where
# the terminal has any — needs an unmissable boundary marking where the
# current frame starts (live-use feedback 2026-07-13).
FRAME_RULE = "─" * 72

# Consecutive invalid inputs before refusing: a human mistypes once or
# twice; an endless invalid stream is a pipe, and discovery is
# interactive-only (spec non-goal) — refuse deterministically instead
# of livelooping (review adjudication 2026-07-13).
MAX_INVALID_PICKS = 5

_NON_INTERACTIVE = (
    f"{MAX_INVALID_PICKS} invalid picks in a row: discover needs an "
    "interactive terminal — scripts should use pull with exact repo ids"
)


def begin_frame() -> None:
    """Mark the start of a freshly rendered window in the scrollback."""
    typer.echo("")
    typer.echo(FRAME_RULE)


def offered_keys(page: DiscoveryPage) -> list[str]:
    """The non-numeric keys this page accepts, quit last.

    Only keys the page actually honors are listed: naming ``b`` on a
    first frame, where there is nothing to step back to, would
    advertise a pick that gets rejected.
    """
    keys = []
    if page.more_available:
        keys.append("m")
    if page.back_available:
        keys.append("b")
    keys.append("q")
    return keys


def prompt_pick(page: DiscoveryPage, prompt_text: str) -> Option:
    """Prompt until the input is a valid pick; EOF quits cleanly.

    Discovery is inherently interactive (scripted discovery is a spec
    non-goal), so exhausted stdin is treated as quitting, not an
    error — and a never-ending invalid stream is refused after a few
    tries rather than looping forever. The prompt text is
    stage-supplied: discovery is open-ended graph browsing, not a
    fixed-step wizard, so each prompt must say what the stage is and
    name the pick that ends it.

    Args:
        page: The window being answered.
        prompt_text: What to ask, already sanitized by the stage.

    Returns:
        The picked option; a quit option when stdin is exhausted.

    Raises:
        PullUserError: After ``MAX_INVALID_PICKS`` invalid inputs in a
            row, which means the input is not a human.
    """
    invalid = 0
    while True:
        try:
            raw = typer.prompt(prompt_text)
        except typer.Abort:
            return Option(key="q", kind="quit", summary=None)
        pick = parse_pick(raw, page)
        if pick is not None:
            return pick
        invalid += 1
        if invalid >= MAX_INVALID_PICKS:
            raise PullUserError(_NON_INTERACTIVE)
        keys = offered_keys(page)
        # "or one of q" reads as a typo when only quit is left on the
        # last window of an exhausted listing (review round 2026-08-06).
        offer = f"one of {'/'.join(keys)}" if len(keys) > 1 else keys[0]
        typer.echo(f"not a listed pick — enter a listed number or {offer}")


def prompt_archive_mode() -> bool | None:
    """Ask how to archive the selected repo; None means quit.

    Live-use gap (2026-07-13): discover only handed off into
    pick-files mode, marching users through a 56-file listing when
    the repo was an original whose whole tree is the artifact
    (spec 0004).

    Returns:
        True for a whole-repo snapshot, False for selective picking,
        None to quit.

    Raises:
        PullUserError: After ``MAX_INVALID_PICKS`` invalid answers.
    """
    invalid = 0
    while True:
        try:
            raw = typer.prompt(
                "archive how? 1 = pick files, 2 = whole-repo snapshot "
                "(originals/masters: usually 2; quant repos: usually 1; q = quit)"
            )
        except typer.Abort:
            return None
        text = raw.strip().lower()
        if text == "q":
            return None
        if text == "1":
            return False
        if text == "2":
            return True
        invalid += 1
        if invalid >= MAX_INVALID_PICKS:
            raise PullUserError(_NON_INTERACTIVE)
        typer.echo("enter 1, 2, or q")
