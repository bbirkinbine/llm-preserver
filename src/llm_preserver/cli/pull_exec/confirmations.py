"""The pull flow's y/N confirmations, and what a decline means.

Separate from ``prompts``, which drives the file-listing frames: a
confirmation is one question with two answers and no frame at all. The
two modules share only the rule that a human's answer is never a fault
(spec 0021) — an answered "no" and a Ctrl-D on a terminal both raise
``PullDeclined``, which ``run_pull`` prints plainly and exits 0 on.

Prompt classification keys on the strings ``pull_model`` composes — the
tool owns both sides of that seam.
"""

import sys

import typer

from llm_preserver.cli.window import is_interactive
from llm_preserver.hub import PullUserError
from llm_preserver.pull_decline import (
    EVERY_WEIGHT_DECLINED,
    SIZE_DECLINED,
    PullDeclined,
)
from llm_preserver.render import clean_text


def confirm_or_stop(prompt: str, assume_yes: bool) -> bool:
    """Confirm interactively; deterministic stop when stdin cannot answer.

    ``--yes`` auto-accepts the *size* confirmation only.

    click raises the same ``Abort`` for a Ctrl-D on a terminal and for
    an exhausted pipe, so the exception cannot tell them apart — the
    interactive verdict does, and it asks about **stdin**, because
    answerability is a question about who is typing (spec 0021). A
    human pressing Ctrl-D has answered: that is the decline the prompt's
    own ``n`` would raise, exit 0 with the same line, so one prompt
    means one thing however the answer arrived. Stdin that cannot answer
    is the scripted fault it has always been — ``PullUserError``, exit 2,
    naming the bypass, so scripted pulls never die with an undocumented
    exit 1 (spec 0004 adjudications).

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
        # click writes a prompt with no trailing newline and emits none
        # on EOF (it echoes one only for hidden input), so without this
        # the next line lands *on* the prompt: "files to pull (…): nothing
        # pulled: quit at the file listing". True on a real terminal, not
        # just under the runner — Ctrl-D leaves the cursor where it is.
        typer.echo()
        # Two prompts reach here and only two: the size confirmation and
        # the every-weight one (spec 0017 deleted the grouping question,
        # which is what the third branch used to serve — and its advice
        # named `--model`, a flag that now only exists to refuse). A
        # two-way split keeps the dead arm from being re-grown.
        if is_interactive(sys.stdin):
            raise PullDeclined(
                SIZE_DECLINED if is_size_confirm else EVERY_WEIGHT_DECLINED
            ) from None
        hint = (
            "re-run with --yes to accept the size confirmation"
            if is_size_confirm
            else "narrow --include, or run interactively"
        )
        raise PullUserError(f"confirmation needed but stdin is not interactive: {hint}") from None
