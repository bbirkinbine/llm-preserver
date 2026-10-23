"""Spec 0025 through ``pull_model``: a held claim stops a second pull of the same repo.

Criterion 5 (refused before planning's hub call and before any prompt),
6 (a provably dead same-host holder is replaced), 9 (a different repo
never sees the claim, including one pulled *while* this one runs), and
the plan-round decision that the unconverted-archive gate runs before
the claim (Q2). Each absence — no hub call, no prompt, no ``.staging/``
— is paired with a control run in the same test showing the thing
appears when the claim allows it, so none can pass with the guard
deleted.

Expected red (test-first): ``pull_claim`` does not exist (planting
tests fail on its import); the rest fail on AssertionError because no
marker is ever held. Split from ``test_pull_claim_lifecycle.py``
(300-line rule).
"""

import logging
import os
from collections.abc import Callable
from pathlib import Path

import pytest
from migrate_shapes import init_archive_dir, pure_rename_archive
from pull_claim_shapes import (
    DEAD_PID,
    MARKER_NAME,
    OTHER_USER_PID,
    ObservingHub,
    held_by_this_run,
    holder,
    install_kill,
    marker_path,
    pin_host,
    plant,
    read_or_none,
)
from staging_shapes import CREATOR, REPO_ID, creator_dir, do_pull, model_dir, new_archive

import llm_preserver.pull as pull
from llm_preserver.layout import UnmigratedArchiveError

SIBLING_ID = f"{CREATOR}/other-chat-GGUF"


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    return new_archive(tmp_path)


def recording_confirm(prompts: list[str]) -> Callable[[str], bool]:
    def confirm(prompt: str) -> bool:
        prompts.append(prompt)
        return True

    return confirm


# --- criterion 5 ------------------------------------------------------------


def test_running_same_host_claim_refuses_before_hub_and_prompts(
    archive: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from llm_preserver.pull_claim import PullInProgressError

    pin_host(monkeypatch)
    raw = plant(marker_path(archive), holder(tmp_path, pid=os.getpid()))
    client = ObservingHub(marker_path(archive))
    prompts: list[str] = []

    with pytest.raises(PullInProgressError):
        do_pull(archive, client, confirm=recording_confirm(prompts))

    assert client.repo_info_calls == []
    assert prompts == []
    assert client.download_calls == []
    assert marker_path(archive).read_bytes() == raw

    # Control: the same client and confirm, once the holder is gone.
    marker_path(archive).unlink()
    do_pull(archive, client, confirm=recording_confirm(prompts))
    assert client.repo_info_calls == [REPO_ID]
    assert prompts


def test_other_users_live_pid_refuses_the_pull(
    archive: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from llm_preserver.pull_claim import PullInProgressError

    pin_host(monkeypatch)
    install_kill(monkeypatch)
    raw = plant(marker_path(archive), holder(tmp_path, pid=OTHER_USER_PID))
    client = ObservingHub(marker_path(archive))

    with pytest.raises(PullInProgressError):
        do_pull(archive, client)

    assert client.repo_info_calls == []
    assert marker_path(archive).read_bytes() == raw


# --- criterion 6 ------------------------------------------------------------


def test_dead_same_host_claim_is_cleared_and_the_pull_proceeds(
    archive: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pin_host(monkeypatch)
    install_kill(monkeypatch)
    plant(marker_path(archive), holder(tmp_path))
    client = ObservingHub(marker_path(archive))

    with caplog.at_level(logging.INFO, logger="llm_preserver"):
        assert do_pull(archive, client) == model_dir(archive)

    assert held_by_this_run(client.at_repo_info[0])  # replaced, not ignored
    assert not marker_path(archive).exists()
    cleared = [
        r for r in caplog.records if r.levelno == logging.INFO and str(DEAD_PID) in r.getMessage()
    ]
    assert len(cleared) == 1


# --- criterion 9 ------------------------------------------------------------


def test_a_live_sibling_claim_under_the_same_owner_is_ignored(
    archive: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin_host(monkeypatch)
    sibling = creator_dir(archive) / "other-chat-GGUF" / MARKER_NAME
    sibling_raw = plant(sibling, holder(tmp_path, repo_id=SIBLING_ID, pid=os.getpid()))
    client = ObservingHub(marker_path(archive))

    assert do_pull(archive, client) == model_dir(archive)

    assert held_by_this_run(client.at_repo_info[0])
    assert sibling.read_bytes() == sibling_raw
    assert creator_dir(archive).is_dir()


def test_a_different_repo_pulled_mid_transfer_both_complete(archive: Path) -> None:
    """Two concurrent pulls under one owner: the second runs while the first holds."""
    observed: list[bool] = []

    def pull_sibling_now() -> None:
        if observed:
            return
        sibling = ObservingHub(marker_path(archive), repo_id=SIBLING_ID)
        pull.pull_model(
            archive, SIBLING_ID, sibling, include=["*Q4_K_M*"], confirm=lambda prompt: True
        )
        # The first pull's claim is still held after the sibling's release.
        observed.append(held_by_this_run(read_or_none(marker_path(archive))))

    client = ObservingHub(marker_path(archive), on_download=pull_sibling_now)

    assert do_pull(archive, client) == model_dir(archive)

    assert observed == [True]
    assert (archive / "models" / SIBLING_ID / "model-record.json").is_file()
    assert (model_dir(archive) / "model-record.json").is_file()
    assert not creator_dir(archive).exists()


# --- Q2: the conversion gate runs before the claim --------------------------


def test_unconverted_archive_refuses_without_creating_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = init_archive_dir(tmp_path / "old")
    pure_rename_archive(old)
    client = ObservingHub(marker_path(old))

    with pytest.raises(UnmigratedArchiveError):
        do_pull(old, client)

    assert client.repo_info_calls == []
    assert not (old / ".staging").exists()

    # Control: on a converted archive the same pull does claim.
    fresh = new_archive(tmp_path / "fresh")
    control = ObservingHub(marker_path(fresh))
    do_pull(fresh, control)
    assert held_by_this_run(control.at_repo_info[0])
