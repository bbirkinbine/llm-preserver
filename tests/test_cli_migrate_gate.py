"""The pre-migration content gate — spec 0017 pass 2, criteria 23-25.

An archive still holding pre-ADR-0003 content must convert before it
accepts new content. ``pull`` and ``remove`` refuse (exit 2) naming
``migrate`` and how many directories are affected; ``status``, ``show``,
``verify`` and ``views`` keep working, so the archive stays inspectable
and runnable for the whole conversion window.

Criterion 25 is the reason the gate exists at all: a pull writing the
new layout into an unconverted archive would create ``unsloth/X-GGUF/``
while the same repo's files still sit in ``Qwen/X/gguf/``, turning every
later migration into a merge. The live archive has zero such collisions
today and this gate is what keeps it that way.

The gate keys on **content** — directories failing
``layout.layout_state`` — not on the archive marker: the marker bump is
pass 3, and criterion 24 keeps a half-converted archive at v1 anyway, so
content is the only signal available during the window the gate covers.

Expected red (test-first): nothing gates ``pull`` or ``remove`` yet, so
both run their normal course.
"""

import re
from pathlib import Path

import pytest
from migrate_shapes import (
    Q4,
    Q4_REL,
    SPLIT_DIR_ID,
    build_directory,
    init_archive_dir,
    output_of,
    pure_rename_archive,
    split_archive,
    tree_snapshot,
)
from typer.testing import CliRunner

from llm_preserver.cli import app
from llm_preserver.records import MANIFEST_FILENAME

runner = CliRunner()

COUNT_PHRASE = re.compile(r"\b3\s+(\w+\s+)?director", re.IGNORECASE)
"""The refusal states how many directories are affected (criterion 23)."""


class RefusingHubClient:
    """Hub seam that fails the test if a gated pull reaches the network.

    The gate is archive state, so it must land before any hub call —
    which also keeps this test hermetic.
    """

    def repo_info(self, repo_id: str) -> None:
        raise AssertionError(f"the content gate must refuse before any hub call (got {repo_id})")

    def download(self, repo_id: str, filename: str, revision: str, dest_dir: Path) -> None:
        raise AssertionError("download must not run on an unmigrated archive")


@pytest.fixture
def no_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Swap the CLI's hub-client seam for one that refuses to be used."""
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", RefusingHubClient)


@pytest.fixture
def unmigrated(tmp_path: Path) -> Path:
    """An archive with exactly three directories holding foreign content."""
    root = init_archive_dir(tmp_path)
    pure_rename_archive(root)
    split_archive(root)
    build_directory(root, "beta/coder", [("gguf", "other/coder-GGUF", {Q4_REL: Q4})])
    return root


def test_pull_refuses_on_an_unmigrated_archive(unmigrated: Path, no_hub: None) -> None:
    result = runner.invoke(
        app, ["pull", "acme/tiny-chat", str(unmigrated), "--include", "*Q4*", "--yes"]
    )

    assert result.exit_code == 2
    assert "migrate" in output_of(result)


def test_the_pull_refusal_counts_the_affected_directories(unmigrated: Path, no_hub: None) -> None:
    """ "How many" is what tells the human whether this is a five-minute
    fix or a weekend — the refusal has to state it, not just name the
    command."""
    result = runner.invoke(
        app, ["pull", "acme/tiny-chat", str(unmigrated), "--include", "*Q4*", "--yes"]
    )

    assert COUNT_PHRASE.search(output_of(result)) is not None


def test_a_refused_pull_writes_nothing(unmigrated: Path, no_hub: None) -> None:
    before = tree_snapshot(unmigrated)

    runner.invoke(app, ["pull", "acme/tiny-chat", str(unmigrated), "--include", "*Q4*", "--yes"])

    assert tree_snapshot(unmigrated) == before


def test_remove_refuses_on_an_unmigrated_archive(unmigrated: Path) -> None:
    """``remove`` relocates content too — a pattern removal rewrites the
    record whose ``source_repo`` the migration plan is derived from."""
    result = runner.invoke(app, ["remove", SPLIT_DIR_ID, str(unmigrated), "--yes"])

    assert result.exit_code == 2
    out = output_of(result)
    assert "migrate" in out
    assert COUNT_PHRASE.search(out) is not None


def test_a_refused_remove_deletes_nothing(unmigrated: Path) -> None:
    before = tree_snapshot(unmigrated)

    runner.invoke(app, ["remove", SPLIT_DIR_ID, str(unmigrated), "--yes"])

    assert tree_snapshot(unmigrated) == before


def test_status_still_works_on_an_unmigrated_archive(unmigrated: Path) -> None:
    """The archive stays inspectable throughout the conversion window."""
    result = runner.invoke(app, ["status", str(unmigrated)])

    assert result.exit_code == 0
    assert "Qwen/tiny-chat" in output_of(result)


def test_show_still_works_on_an_unmigrated_archive(unmigrated: Path) -> None:
    result = runner.invoke(app, ["show", SPLIT_DIR_ID, str(unmigrated)])

    assert result.exit_code == 0
    assert SPLIT_DIR_ID in output_of(result)


def test_verify_still_audits_an_unmigrated_archive(unmigrated: Path) -> None:
    """Criterion 23 scopes the gate to commands that add or relocate
    content. ``verify`` refreshes ``manifest-sha256.txt`` and must not
    block itself — its exit 1 here is the ``unmigrated`` verdict from
    pass 1, not a refusal to run."""
    result = runner.invoke(app, ["verify", str(unmigrated)])

    assert result.exit_code == 1
    out = output_of(result)
    assert "unmigrated" in out
    assert (unmigrated / "models" / "Qwen" / "tiny-chat" / MANIFEST_FILENAME).is_file()


def test_views_still_builds_from_an_unmigrated_archive(unmigrated: Path, tmp_path: Path) -> None:
    """Runnable, not just inspectable: a curator mid-conversion can still
    load a model."""
    result = runner.invoke(app, ["views", str(unmigrated), "--dest", str(tmp_path / "view")])

    assert result.exit_code == 0
    assert "eligible" in output_of(result)


def test_a_migrated_archive_lets_remove_through_again(unmigrated: Path) -> None:
    """The gate closes only while there is content to convert — proving
    it is keyed on content, not on a flag that stays set."""
    migrated = runner.invoke(app, ["migrate", str(unmigrated), "--yes"])
    assert migrated.exit_code == 0

    result = runner.invoke(app, ["remove", SPLIT_DIR_ID, str(unmigrated), "--yes"])

    assert result.exit_code == 0
    assert not (unmigrated / "models" / "bartowski" / "tiny-chat-GGUF").exists()


def test_the_core_gate_refuses_before_any_hub_call(unmigrated: Path) -> None:
    """Mutation survivor (review, 2026-08-11): deleting
    ``require_migrated_archive`` from ``pull_prepare`` left the whole
    suite green, because only the CLI half of the gate was covered. The
    core half is the one that protects a direct API caller."""
    import pytest

    from llm_preserver.layout import UnmigratedArchiveError
    from llm_preserver.pull_prepare import prepare_pull

    class RefusingClient:
        def repo_info(self, repo_id: str):
            raise AssertionError("the gate must refuse before any hub call")

    with pytest.raises(UnmigratedArchiveError):
        prepare_pull(
            unmigrated,
            "acme/tiny-chat",
            RefusingClient(),
            include=["*"],
            confirm=lambda prompt: True,
        )
