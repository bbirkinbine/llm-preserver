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
from llm_preserver.records import load_record

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
    # a GGUF tree, so the default home under model=None is acme/tiny-chat.
    return archive_root / "models" / "acme" / "tiny-chat"


def repo_home(archive_root: Path) -> Path:
    # With base_model=None the repo is its own home — the only default
    # home the prompt skip applies to (it is not hub-derived).
    return archive_root / "models" / "bartowski" / "tiny-chat-GGUF"


def do_pull(archive_root: Path, client, **kwargs) -> Path:
    kwargs.setdefault("include", ["*Q4_K_M*"])
    kwargs.setdefault("model", None)
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


def test_hub_derived_home_still_asks_grouping_when_fully_archived(archive, fake_hub_factory):
    # The 0006 invariant, upheld at the 0014 review round: a home taken
    # from hub metadata (the declared base_model) never names an
    # archive directory without a human yes — even when the plan would
    # find nothing to do. A hostile base_model plus a name+size-matched
    # hashless file must not earn a silent "already archived" exit 0.
    client = fake_hub_factory()  # declares base_model=acme/tiny-chat
    do_pull(archive, client)
    downloads_after_seed = len(client.download_calls)
    prompts: list[str] = []

    result = do_pull(archive, client, confirm=recording_confirm(prompts))

    assert len(prompts) == 1  # the grouping question, and only it
    assert "group" in prompts[0]
    assert "acme/tiny-chat" in prompts[0]
    assert result == default_home(archive)
    assert len(client.download_calls) == downloads_after_seed  # still a no-op


def test_fully_archived_repull_with_model_override_asks_no_prompts(archive, fake_hub_factory):
    # Spec 0014: the early exit is unified across paths — --model
    # (which never asked the grouping question) now also skips the
    # every-weight confirmation when there is nothing to do.
    client = fake_hub_factory()
    do_pull(archive, client, include=["*.gguf"], model="acme/tiny-chat")

    result = do_pull(
        archive, client, include=["*.gguf"], model="acme/tiny-chat", confirm=refuse_confirm
    )

    assert result == default_home(archive)


def test_partial_overlap_asks_grouping_then_size(archive, fake_hub_factory):
    # One archived quant plus one new quant: there is work to do, so
    # the check is file-level, never repo-level — prompts fire in
    # today's order, grouping first, size last.
    client = fake_hub_factory(files=THREE_QUANT_FILES)
    do_pull(archive, client)
    prompts: list[str] = []

    do_pull(archive, client, include=["*Q4_K_M*", "*Q8_0*"], confirm=recording_confirm(prompts))

    assert len(prompts) == 2
    assert "group" in prompts[0]
    assert "acme/tiny-chat" in prompts[0]
    assert prompts[1].startswith("pull ")
    assert (default_home(archive) / "gguf" / Q8_NAME).is_file()


def test_adopt_only_repull_still_asks_grouping(archive, fake_hub_factory):
    # Adoption downloads nothing but writes a record; the grouping
    # answer decides where that record lands — never skipped.
    client = fake_hub_factory(files=[(Q4_NAME, Q4_BYTES, True), (Q8_NAME, Q8_BYTES, True)])
    on_disk = default_home(archive) / "gguf" / Q4_NAME
    on_disk.parent.mkdir(parents=True)
    on_disk.write_bytes(Q4_BYTES)
    prompts: list[str] = []

    result = do_pull(archive, client, confirm=recording_confirm(prompts))

    assert len(prompts) == 1  # grouping only: adopt-only skips the size prompt
    assert "group" in prompts[0]
    assert client.download_calls == []
    assert load_record(result).artifacts  # the adoption was recorded


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

    assert len(prompts) == 2
    assert "group" in prompts[0]
    assert prompts[1].startswith("pull ")


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

    assert len(prompts) == 2
    assert "group" in prompts[0]
    assert prompts[1].startswith("pull ")
    assert target.read_bytes() == Q4_BYTES
