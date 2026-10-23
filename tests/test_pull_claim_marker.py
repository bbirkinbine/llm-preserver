"""Spec 0025: the pull claim's marker — where it lives, what it says, when it goes.

Unit tests of ``llm_preserver.pull_claim`` against plain ``tmp_path``
leaves: the exclusive create and its location (criterion 1), the
five-field shape (criterion 2), release on every exit and only of this
run's own marker (criterion 4), a sibling leaf left alone (criterion
9), and the selection-time refusal of a hub file named like the marker
(plan-round decision Q4). How a *held* marker is judged lives in
``test_pull_claim_holders.py``; the same behavior through ``pull_model``
and the CLI lives in the lifecycle and CLI modules.

Expected red (test-first): ``llm_preserver.pull_claim`` does not exist,
and ``staging_cleanup`` has no ``discard_empty_staging_leaf`` — every
test here fails on that import.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pull_claim_shapes import (
    DEAD_PID,
    FIELDS,
    FOREIGN_MARKER,
    MARKER_NAME,
    THIS_HOST,
    holder,
    pin_host,
    plant,
)
from staging_shapes import REPO_ID

from llm_preserver.hub import PullUserError, RepoFile

SIBLING_ID = "bartowski/other-chat-GGUF"


@pytest.fixture
def leaf(tmp_path: Path) -> Path:
    """A not-yet-existing ``.staging/<owner>/<repo>/`` leaf."""
    return tmp_path / "archive" / ".staging" / "bartowski" / "tiny-chat-GGUF"


def claimed_marker(leaf: Path) -> dict[str, object]:
    """Claim ``leaf`` and return the marker exactly as it sat on disk."""
    from llm_preserver.pull_claim import claim_pull

    with claim_pull(leaf, REPO_ID):
        data: dict[str, object] = json.loads((leaf / MARKER_NAME).read_text("utf-8"))
    return data


# --- criterion 1: the claim -------------------------------------------------


def test_marker_filename_is_the_spec_name() -> None:
    from llm_preserver.pull_claim import PULL_MARKER_FILENAME

    assert PULL_MARKER_FILENAME == MARKER_NAME


def test_claim_holds_a_marker_inside_the_staging_leaf(leaf: Path) -> None:
    from llm_preserver.pull_claim import claim_pull

    with claim_pull(leaf, REPO_ID):
        assert (leaf / MARKER_NAME).is_file()


def test_claim_creates_the_missing_leaf_and_owner_dirs(leaf: Path) -> None:
    from llm_preserver.pull_claim import claim_pull

    assert not leaf.parent.exists()  # the claim has to make the path itself
    with claim_pull(leaf, REPO_ID):
        assert leaf.is_dir()


# --- criterion 2: the shape -------------------------------------------------


def test_marker_holds_exactly_the_five_holder_fields(leaf: Path) -> None:
    assert set(claimed_marker(leaf)) == FIELDS


def test_marker_names_this_repo_process_and_host(
    leaf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin_host(monkeypatch)

    data = claimed_marker(leaf)

    assert data["repo_id"] == REPO_ID
    assert data["pid"] == os.getpid()
    assert data["hostname"] == THIS_HOST


def test_marker_started_is_a_utc_iso8601_timestamp(leaf: Path) -> None:
    started = claimed_marker(leaf)["started"]

    assert isinstance(started, str)
    parsed = datetime.fromisoformat(started)
    assert parsed.utcoffset() == timedelta(0)


def test_marker_tool_field_is_a_nonempty_string(leaf: Path) -> None:
    tool = claimed_marker(leaf)["tool"]

    assert isinstance(tool, str)
    assert tool


# --- criterion 4: release ---------------------------------------------------


def test_release_removes_the_marker_and_the_emptied_leaf_and_owner(leaf: Path) -> None:
    from llm_preserver.pull_claim import claim_pull

    with claim_pull(leaf, REPO_ID):
        assert (leaf / MARKER_NAME).is_file()  # positive control: it was held

    assert not leaf.exists()
    assert not leaf.parent.exists()


def test_release_keeps_staged_bytes_and_their_leaf(leaf: Path) -> None:
    """Release is unlink + rmdir, never rmtree: resumable bytes survive."""
    from llm_preserver.pull_claim import claim_pull

    with claim_pull(leaf, REPO_ID):
        (leaf / "part.gguf").write_bytes(b"staged bytes")

    assert not (leaf / MARKER_NAME).exists()
    assert (leaf / "part.gguf").read_bytes() == b"staged bytes"


def test_release_runs_when_the_body_raises(leaf: Path) -> None:
    from llm_preserver.pull_claim import claim_pull

    held = False
    with pytest.raises(RuntimeError, match="mid-pull"), claim_pull(leaf, REPO_ID):
        held = (leaf / MARKER_NAME).is_file()
        raise RuntimeError("mid-pull")

    assert held
    assert not (leaf / MARKER_NAME).exists()


def test_release_runs_on_keyboard_interrupt(leaf: Path) -> None:
    from llm_preserver.pull_claim import claim_pull

    held = False
    with pytest.raises(KeyboardInterrupt), claim_pull(leaf, REPO_ID):
        held = (leaf / MARKER_NAME).is_file()
        raise KeyboardInterrupt

    assert held
    assert not (leaf / MARKER_NAME).exists()


def test_release_leaves_a_marker_another_run_wrote_in_its_place(leaf: Path) -> None:
    """After this run's leaf is gone, a new run may claim before the finally."""
    from llm_preserver.pull_claim import claim_pull

    with claim_pull(leaf, REPO_ID):
        ours = (leaf / MARKER_NAME).read_bytes()
        foreign = plant(leaf / MARKER_NAME, FOREIGN_MARKER)

    assert ours != foreign  # positive control: the swap changed the holder
    assert (leaf / MARKER_NAME).read_bytes() == foreign


def test_release_leaves_the_owner_dir_while_a_sibling_leaf_is_live(leaf: Path) -> None:
    from llm_preserver.pull_claim import claim_pull

    sibling = leaf.parent / "other-chat-GGUF" / MARKER_NAME
    sibling_bytes = plant(sibling, {**FOREIGN_MARKER, "repo_id": SIBLING_ID})

    with claim_pull(leaf, REPO_ID):
        assert (leaf / MARKER_NAME).is_file()

    assert not leaf.exists()  # differential: our leaf went, the owner stayed
    assert leaf.parent.is_dir()
    assert sibling.read_bytes() == sibling_bytes


# --- criterion 9: different repos -------------------------------------------


def test_two_repos_under_one_owner_hold_claims_at_once(leaf: Path) -> None:
    from llm_preserver.pull_claim import claim_pull

    sibling = leaf.parent / "other-chat-GGUF"
    with claim_pull(leaf, REPO_ID), claim_pull(sibling, SIBLING_ID):
        assert (leaf / MARKER_NAME).is_file()
        assert (sibling / MARKER_NAME).is_file()

    assert not leaf.parent.exists()


def test_stale_takeover_leaves_a_marker_that_changed_after_it_was_judged(
    leaf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Judged stale, then replaced by a live run before the unlink: kept."""
    from llm_preserver.pull_claim import PullInProgressError, claim_pull

    pin_host(monkeypatch)
    marker = leaf / MARKER_NAME
    plant(marker, holder(tmp_path))
    live = json.dumps(holder(tmp_path, pid=os.getpid(), started="2026-10-23T09:00:00+00:00"))
    real_kill = os.kill

    def kill_then_swap(pid: int, sig: int) -> None:
        if pid == DEAD_PID:
            marker.write_text(live, encoding="utf-8")  # a live run claims meanwhile
            raise ProcessLookupError(pid)
        real_kill(pid, sig)

    monkeypatch.setattr(os, "kill", kill_then_swap)

    with pytest.raises(PullInProgressError), claim_pull(leaf, REPO_ID):
        pass

    assert marker.read_text("utf-8") == live


# --- staging_cleanup: the success-path leaf discard -------------------------


def test_discard_empty_staging_leaf_removes_leaf_and_empty_owner(leaf: Path) -> None:
    from llm_preserver.staging_cleanup import discard_empty_staging_leaf

    leaf.mkdir(parents=True)

    discard_empty_staging_leaf(leaf)

    assert not leaf.exists()
    assert not leaf.parent.exists()


def test_discard_empty_staging_leaf_never_deletes_contents(leaf: Path) -> None:
    from llm_preserver.staging_cleanup import discard_empty_staging_leaf

    leaf.mkdir(parents=True)
    (leaf / "part.gguf").write_bytes(b"staged bytes")

    discard_empty_staging_leaf(leaf)

    assert (leaf / "part.gguf").read_bytes() == b"staged bytes"


# --- Q4: a hub file named like the marker -----------------------------------


def repo_file(path: str) -> RepoFile:
    return RepoFile(path=path, size=10, sha256=None)


@pytest.mark.parametrize("name", [MARKER_NAME, ".LLM-Preserver-Pull.JSON"])
def test_root_file_named_like_the_marker_is_refused(name: str) -> None:
    from llm_preserver.pull_claim import require_no_marker_collision

    with pytest.raises(PullUserError) as excinfo:
        require_no_marker_collision([repo_file("tiny-chat-Q4_K_M.gguf"), repo_file(name)])

    assert name in str(excinfo.value)


def test_nested_file_named_like_the_marker_is_allowed() -> None:
    """Only a root-level file lands on the marker; a nested one cannot."""
    from llm_preserver.pull_claim import require_no_marker_collision

    require_no_marker_collision([repo_file(f"sub/{MARKER_NAME}")])
