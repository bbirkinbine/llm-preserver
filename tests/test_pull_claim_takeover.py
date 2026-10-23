"""Spec 0025 review round: taking over and creating a claim, at its edges.

Continues ``test_pull_claim_holders.py``. Each case was reproduced by a
reviewer before it was fixed:

- a stale marker naming **this process's own pid** was judged running,
  so a container with a fixed hostname whose entrypoint is always pid 1
  refused itself after every hard kill. This run has not claimed yet,
  so a marker carrying its pid is provably someone else's leftover
  (criterion 6, widened by Brian 2026-10-23);
- a stale marker that cannot be deleted (another user's leaf) was
  refused as "kept changing underneath this run", naming nothing true;
- a Ctrl-C during the marker write leaked the half-written marker;
- one entry vanishing during the success-path cleanup stopped it early,
  silently, leaving the rest of the leaf behind.
"""

import json
import logging
import os
import shutil
from pathlib import Path

import pytest
from pull_claim_shapes import holder, install_kill, marker_path, pin_host, plant
from staging_shapes import REPO_ID, new_archive, staging_leaf

from llm_preserver.hub import PullError
from llm_preserver.pull_claim import claim_pull


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    return new_archive(tmp_path)


def test_a_marker_holding_this_processes_own_pid_is_stale(
    tmp_path: Path,
    archive: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pin_host(monkeypatch)
    install_kill(monkeypatch)
    marker = marker_path(archive)
    plant(marker, holder(tmp_path, pid=os.getpid()))

    with (
        caplog.at_level(logging.INFO, logger="llm_preserver"),
        claim_pull(staging_leaf(archive), REPO_ID),
    ):
        now = json.loads(marker.read_bytes())

    assert now["started"] != "2026-10-23T08:15:00+00:00"  # replaced, not reused
    assert any("cleared a stale pull marker" in r.getMessage() for r in caplog.records)
    assert not marker.exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_a_stale_marker_that_cannot_be_deleted_says_so(
    tmp_path: Path, archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin_host(monkeypatch)
    install_kill(monkeypatch)
    marker = marker_path(archive)
    raw = plant(marker, holder(tmp_path))  # dead pid, this host: stale
    leaf = marker.parent
    leaf.chmod(0o555)  # another user's leaf: readable, not writable
    try:
        with pytest.raises(PullError) as refused, claim_pull(leaf, REPO_ID):
            pass
    finally:
        leaf.chmod(0o755)

    message = str(refused.value)
    assert "could not be deleted" in message
    assert str(marker) in message
    assert "kept changing" not in message
    assert marker.read_bytes() == raw


def test_an_interrupt_during_the_marker_write_leaves_no_marker(
    archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted_fsync(fd: int) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "fsync", interrupted_fsync)

    with pytest.raises(KeyboardInterrupt), claim_pull(staging_leaf(archive), REPO_ID):
        pytest.fail("the claim must not be held after an interrupted write")

    assert not marker_path(archive).exists()


def test_success_cleanup_continues_past_an_entry_that_vanished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from llm_preserver.staging_cleanup import discard_staging_leaf

    leaf = tmp_path / "leaf"
    for name in ("a", "b", "c"):
        (leaf / name).mkdir(parents=True)
        (leaf / name / "f").write_bytes(b"x")
    (leaf / "keep.json").write_bytes(b"{}")
    real_rmtree = shutil.rmtree
    removed: list[str] = []

    def rmtree(path: Path, *args: object, **kwargs: object) -> None:
        removed.append(Path(path).name)
        if len(removed) == 1:
            real_rmtree(path)
            raise FileNotFoundError(2, "No such file or directory", str(path))
        real_rmtree(path)

    monkeypatch.setattr(shutil, "rmtree", rmtree)

    discard_staging_leaf(leaf, keep="keep.json")

    assert sorted(p.name for p in leaf.iterdir()) == ["keep.json"]
    assert len(removed) == 3  # positive control: the vanish was the first entry
