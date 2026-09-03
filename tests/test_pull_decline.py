"""``PullDeclined`` is a decline, not a fault — spec 0021 (the type).

Dispatch is by exception type, never by matching message text (spec
0020's rule), and the type deliberately sits *outside* the ``PullError``
hierarchy: the fault-domain mapper walks that hierarchy, so a decline
that inherited from it could be silently reclassified into a fault
domain by any future ``isinstance`` branch. The first test asserts that
directly, so nobody can later "simplify" the class into the tree.

The second half pins ``confirm_or_stop``'s split. click raises the same
``Abort`` for a Ctrl-D on a terminal and for an exhausted pipe, so the
exception cannot tell the two apart — the interactive verdict does, and
it is asked about **stdin**, because answerability is a question about
who is typing.

``PullDeclined`` is imported inside each test body, following
``conftest``'s rule that collection of unrelated tests must never depend
on an implementation module existing.
"""

import sys

import pytest
import typer

import llm_preserver.cli.pull_exec.confirmations as confirmations
import llm_preserver.cli.pull_exec.prompts as prompts

# The strings ``pull_model`` composes; ``confirm_or_stop`` classifies a
# prompt by them (the tool owns both sides of that seam).
SIZE_PROMPT = "pull 2 of 2 files (37 B to download) from bartowski/tiny-chat-GGUF?"
EVERY_WEIGHT_PROMPT = (
    "selection covers every weight file in bartowski/tiny-chat-GGUF; pull them all?"
)


def abort(*_args, **_kwargs):
    raise typer.Abort


def test_a_decline_is_not_a_pull_error_so_the_fault_mapper_cannot_reach_it():
    from llm_preserver.cli.pull_exec.plumbing import PULL_FAULT_DOMAINS
    from llm_preserver.hub import PullError
    from llm_preserver.pull_decline import PullDeclined

    declined = PullDeclined("nothing pulled: quit at the file listing")

    assert not issubclass(PullDeclined, PullError)
    assert not any(isinstance(declined, domain) for domain, _label, _code in PULL_FAULT_DOMAINS)


def test_an_aborted_size_confirmation_splits_on_the_interactive_verdict(monkeypatch):
    from llm_preserver.hub import PullUserError
    from llm_preserver.pull_decline import PullDeclined

    monkeypatch.setattr(typer, "confirm", abort)

    monkeypatch.setattr(confirmations, "is_interactive", lambda stream: True)
    with pytest.raises(PullDeclined) as declined:
        confirmations.confirm_or_stop(SIZE_PROMPT, False)

    monkeypatch.setattr(confirmations, "is_interactive", lambda stream: False)
    with pytest.raises(PullUserError) as fault:
        confirmations.confirm_or_stop(SIZE_PROMPT, False)

    assert str(declined.value) == "nothing pulled: size confirmation declined"
    assert "--yes" in str(fault.value)


def test_an_aborted_every_weight_confirmation_declines_with_its_own_advice(monkeypatch):
    # Ctrl-D prints the line an answered "n" would print — one prompt,
    # one meaning, however the answer arrived.
    from llm_preserver.pull_decline import PullDeclined

    monkeypatch.setattr(typer, "confirm", abort)
    monkeypatch.setattr(confirmations, "is_interactive", lambda stream: True)

    with pytest.raises(PullDeclined) as declined:
        confirmations.confirm_or_stop(EVERY_WEIGHT_PROMPT, False)

    assert str(declined.value).startswith("nothing pulled:")
    assert "narrow --include and re-run" in str(declined.value)


def test_the_confirmation_answerability_verdict_asks_about_stdin(monkeypatch):
    from llm_preserver.pull_decline import PullDeclined

    asked = []

    def spy(stream):
        asked.append(stream)
        return True

    monkeypatch.setattr(typer, "confirm", abort)
    monkeypatch.setattr(confirmations, "is_interactive", spy)

    with pytest.raises(PullDeclined):
        confirmations.confirm_or_stop(SIZE_PROMPT, False)

    assert asked == [sys.stdin]


def test_ctrl_c_at_a_prompt_is_the_same_decline_as_ctrl_d(monkeypatch):
    """SIGINT at a prompt declines; SIGINT during a transfer still exits 130.

    click folds ``KeyboardInterrupt`` into the same ``Abort`` it raises
    for EOF, so the two cannot be told apart at this seam — a fact of
    the library, not a choice this code makes. Accepted rather than
    worked around (adjudicated 2026-09-03): ``remove`` has said exactly
    this since spec 0010 ("Ctrl-C at an interactive prompt is a
    decline, not a fault"), and no bytes have moved yet at either
    prompt. ``run_pull``'s own ``except KeyboardInterrupt`` still owns
    the mid-transfer case, where bytes *have* moved, at exit 130.

    Driven through typer's **vendored** click — ``typer.prompt`` comes
    from ``typer._click.termui``, a different module from the ``click``
    package's, so patching the latter would prove nothing and quietly
    fall through to a real read.
    """
    import typer._click.termui as vendored_termui

    from llm_preserver.pull_decline import PullDeclined

    def interrupt(_text=""):
        raise KeyboardInterrupt

    monkeypatch.setattr(vendored_termui, "visible_prompt_func", interrupt)
    monkeypatch.setattr(prompts, "is_interactive", lambda stream: True)
    monkeypatch.setattr(confirmations, "is_interactive", lambda stream: True)

    with pytest.raises(PullDeclined) as at_listing:
        prompts._ask()
    with pytest.raises(PullDeclined) as at_confirm:
        confirmations.confirm_or_stop(SIZE_PROMPT, False)

    assert str(at_listing.value) == "nothing pulled: quit at the file listing"
    assert str(at_confirm.value) == "nothing pulled: size confirmation declined"
