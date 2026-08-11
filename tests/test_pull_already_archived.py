"""Core behavior of spec 0014 — no prompt when nothing needs pulling.

A pull whose whole selection is already archived under a home that is
*not hub-derived* — the repo id itself (no ``base_model``), or an
explicit ``--model`` — plans first and, finding no work, exits 0
without asking any prompt; the per-file "already archived" INFO lines
and the "nothing to pull" summary still print. A *hub-derived* home
(the declared ``base_model``) always confirms before naming a
directory — the spec 0006 invariant, upheld at the 0014 review round.
Any plan with work to do — a new file, an adoption, a
``--refresh-docs`` replacement, archive drift — prompts exactly as
today. FakeHubClient from conftest; archives live in tmp_path, never
a real archive.
"""

import logging
from collections.abc import Callable
from pathlib import Path

import pytest

import llm_preserver.pull as pull
from llm_preserver.archive import init_archive

REPO_ID = "bartowski/tiny-chat-GGUF"
Q4_NAME = "tiny-chat-Q4_K_M.gguf"
Q4_BYTES = b"q4 weight bytes"
Q8_NAME = "tiny-chat-Q8_0.gguf"
Q8_BYTES = b"q8 weight bytes"
README_BYTES = b"# tiny-chat quantized\n"
# A third quant keeps a Q4+Q8 selection below the every-weight
# threshold, so overlap tests see only the grouping + size prompts.
THREE_QUANT_FILES = [
    (Q4_NAME, Q4_BYTES, True),
    (Q8_NAME, Q8_BYTES, True),
    ("tiny-chat-Q5_K_M.gguf", b"q5 weight bytes", True),
    ("README.md", README_BYTES, False),
]


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / "archive"
    init_archive(root)
    return root


def default_home(archive_root: Path) -> Path:
    # The conftest default repo declares base_model=acme/tiny-chat over
    # a GGUF tree, so under ADR 0003 the destination is the typed repo id.
    return archive_root / "models" / "bartowski" / "tiny-chat-GGUF"


def repo_home(archive_root: Path) -> Path:
    # With base_model=None the repo is its own home — the only default
    # home the prompt skip applies to (it is not hub-derived).
    return archive_root / "models" / "bartowski" / "tiny-chat-GGUF"


def do_pull(archive_root: Path, client, **kwargs) -> Path:
    kwargs.setdefault("include", ["*Q4_K_M*"])
    kwargs.setdefault("confirm", lambda prompt: True)
    return pull.pull_model(archive_root, REPO_ID, client, **kwargs)


def recording_confirm(prompts: list[str]) -> Callable[[str], bool]:
    def confirm(prompt: str) -> bool:
        prompts.append(prompt)
        return True

    return confirm


def refuse_confirm(prompt: str) -> bool:
    raise AssertionError(f"confirm must not be called on the nothing-to-do path: {prompt!r}")


def test_fully_archived_repull_asks_no_prompts(archive, fake_hub_factory):
    client = fake_hub_factory(base_model=None)
    do_pull(archive, client)
    prompts: list[str] = []

    result = do_pull(archive, client, confirm=recording_confirm(prompts))

    assert prompts == []  # no grouping question, no size confirmation
    assert result == repo_home(archive)


def test_fully_archived_repull_still_logs_already_archived_lines(archive, fake_hub_factory, caplog):
    # Spec 0014: only the prompt disappears — the per-file INFO lines
    # and the "nothing to pull" summary keep printing, and no confirm
    # answer is consumed along the way.
    client = fake_hub_factory(base_model=None)
    do_pull(archive, client)

    with caplog.at_level(logging.INFO):
        do_pull(archive, client, confirm=refuse_confirm)

    assert "already archived" in caplog.text
    assert "nothing to pull" in caplog.text
    assert str(repo_home(archive)) in caplog.text
    # Per-file lines first, summary last — the order the spec states.
    assert caplog.text.index("already archived") < caplog.text.index("nothing to pull")


def test_fully_archived_repull_succeeds_when_confirm_would_answer_no(archive, fake_hub_factory):
    # y and N both reach the same no-op, so the answer must not be
    # asked for — a confirm answering "no" cannot derail the exit.
    client = fake_hub_factory(base_model=None)
    do_pull(archive, client)

    result = do_pull(archive, client, confirm=lambda prompt: False)

    assert result == repo_home(archive)
    assert (repo_home(archive) / "gguf" / Q4_NAME).read_bytes() == Q4_BYTES


def test_fully_archived_all_weights_repull_asks_no_prompts(archive, fake_hub_factory):
    # A selection covering every weight normally adds the every-weight
    # confirmation; on the nothing-to-do path that question is skipped
    # along with grouping and size — no answer could change the no-op.
    client = fake_hub_factory(base_model=None)
    do_pull(archive, client, include=["*.gguf"])

    result = do_pull(archive, client, include=["*.gguf"], confirm=refuse_confirm)

    assert result == repo_home(archive)


def test_refresh_docs_with_changed_doc_still_prompts(archive, fake_hub_factory):
    # --refresh-docs with a changed upstream doc counts as work to do.
    do_pull(archive, fake_hub_factory())
    changed = fake_hub_factory(
        files=[
            (Q4_NAME, Q4_BYTES, True),
            (Q8_NAME, Q8_BYTES, True),
            ("README.md", b"# tiny-chat quantized, revised edition\n", False),
        ]
    )
    prompts: list[str] = []

    do_pull(archive, changed, refresh_docs=True, confirm=recording_confirm(prompts))

    # One question now, not two: ADR 0003 removed the grouping confirm,
    # so only the plan's own question is asked.
    assert len(prompts) == 1
    assert prompts[0].startswith("pull ")


def test_archive_drift_repull_still_prompts(archive, fake_hub_factory):
    # A recorded file missing on disk is archive drift: the re-download
    # is work to do, so the pull prompts as today.
    client = fake_hub_factory()
    do_pull(archive, client)
    target = default_home(archive) / "gguf" / Q4_NAME
    target.chmod(0o644)
    target.unlink()
    prompts: list[str] = []

    do_pull(archive, client, confirm=recording_confirm(prompts))

    # One question now, not two: ADR 0003 removed the grouping confirm,
    # so only the plan's own question is asked.
    assert len(prompts) == 1
    assert prompts[0].startswith("pull ")
    assert target.read_bytes() == Q4_BYTES
