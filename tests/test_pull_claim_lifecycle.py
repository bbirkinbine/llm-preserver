"""Spec 0025 through ``pull_model``: when the claim is held, and that it always goes.

Criterion 1 (claimed before planning reaches the hub or reads the
record) and criterion 4 (released on every exit) observed through the
real pull with ``ObservingHub``, which reads the marker file at each hub
call. Every "the marker is gone afterward" assertion is paired with
"this run's marker was there mid-run" — today no marker is ever written,
so an absence check alone would pass with the feature deleted.

Expected red (test-first): ``pull_model`` takes no claim, so the
mid-run observations read None and fail on AssertionError. Planting
tests (refusal, stale, sibling) live in ``test_pull_claim_refusal.py``.
Everything is ``tmp_path`` + the fake hub; no network, no real archive.
"""

import errno
import json
import logging
import shutil
from pathlib import Path
from typing import Any

import pytest
from pull_claim_shapes import (
    FOREIGN_MARKER,
    ObservingHub,
    held_by_this_run,
    marker_path,
    read_or_none,
)
from staging_shapes import (
    INCLUDE_Q4,
    Q4_BYTES,
    Q4_NAME,
    REPO_ID,
    creator_dir,
    do_pull,
    failing_rmtree,
    model_dir,
    new_archive,
    staging_leaf,
)

import llm_preserver.pull as pull
import llm_preserver.pull_prepare as pull_prepare
from llm_preserver.hub import PullEnvError, PullHubError
from llm_preserver.model_scan import staging_leftovers
from llm_preserver.pull_decline import PullDeclined


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    return new_archive(tmp_path)


def observer(archive: Path, **kwargs: Any) -> ObservingHub:
    return ObservingHub(marker_path(archive), **kwargs)


def assert_tidy(archive: Path) -> None:
    """Marker released and the emptied leaf and owner dir gone."""
    assert not marker_path(archive).exists()
    assert not staging_leaf(archive).exists()
    assert not creator_dir(archive).exists()


# --- criterion 1: claimed before planning -----------------------------------


def test_marker_is_held_when_planning_calls_the_hub(archive: Path) -> None:
    client = observer(archive)

    do_pull(archive, client)

    assert held_by_this_run(client.at_repo_info[0])


def test_marker_is_held_before_the_record_is_read(
    archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With pre-fetched metadata (discover, listing) no hub call happens first."""
    info = ObservingHub(marker_path(archive)).repo_info(REPO_ID)
    seen: list[bytes | None] = []
    real_load = pull_prepare.load_existing_record

    def spying_load(directory: Path) -> Any:
        seen.append(read_or_none(marker_path(archive)))
        return real_load(directory)

    monkeypatch.setattr(pull_prepare, "load_existing_record", spying_load)
    client = observer(archive)

    do_pull(archive, client, repo_info=info)

    assert client.repo_info_calls == []
    assert seen
    assert held_by_this_run(seen[0])


def test_a_running_pull_leaf_is_listed_as_a_staging_leftover(archive: Path) -> None:
    """Criterion 11: before the first byte, the marker alone makes the leaf visible."""
    seen: list[list[str]] = []
    client = observer(
        archive,
        on_download=lambda: seen.append([x.model_id for x in staging_leftovers(archive)]),
    )

    do_pull(archive, client)

    assert seen[0] == [REPO_ID]


# --- criterion 4: released on every exit ------------------------------------


def test_success_releases_the_claim_and_tidies_staging(archive: Path) -> None:
    client = observer(archive)

    assert do_pull(archive, client) == model_dir(archive)

    assert held_by_this_run(client.at_download[0])
    assert_tidy(archive)


def test_no_op_return_releases_the_claim(archive: Path) -> None:
    do_pull(archive, observer(archive))
    client = observer(archive)

    do_pull(archive, client)

    assert client.download_calls == []  # the 0014 no-op path
    assert held_by_this_run(client.at_repo_info[0])
    assert_tidy(archive)


def test_metadata_only_return_releases_the_claim(archive: Path) -> None:
    do_pull(archive, observer(archive))
    client = observer(archive)

    do_pull(archive, client, roles=["chat"])

    assert client.download_calls == []
    assert held_by_this_run(client.at_repo_info[0])
    assert_tidy(archive)


def test_declined_size_prompt_releases_the_claim(archive: Path) -> None:
    client = observer(archive)

    with pytest.raises(PullDeclined):
        do_pull(archive, client, confirm=lambda prompt: not prompt.startswith("pull "))

    assert held_by_this_run(client.at_repo_info[0])
    assert_tidy(archive)


def test_declined_every_weight_prompt_releases_the_claim(archive: Path) -> None:
    client = observer(archive)

    with pytest.raises(PullDeclined):
        do_pull(
            archive, client, include=["*.gguf"], confirm=lambda prompt: "every weight" not in prompt
        )

    assert held_by_this_run(client.at_repo_info[0])
    assert_tidy(archive)


def test_hub_error_mid_transfer_releases_the_claim_and_keeps_staged_bytes(
    archive: Path,
) -> None:
    client = observer(
        archive,
        download_error=PullHubError("hub returned 500; retry later"),
        fail_after_downloads=1,
    )

    with pytest.raises(PullHubError):
        do_pull(archive, client, include=["*.gguf"])

    assert held_by_this_run(client.at_download[1])
    assert not marker_path(archive).exists()
    assert (staging_leaf(archive) / Q4_NAME).read_bytes() == Q4_BYTES  # resumable


def test_local_filesystem_failure_releases_the_claim(
    archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_save(*args: Any, **kwargs: Any) -> None:
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(pull, "save_record", refuse_save)
    client = observer(archive)

    with pytest.raises(PullEnvError):
        do_pull(archive, client)

    assert held_by_this_run(client.at_download[0])
    assert not marker_path(archive).exists()


def test_ctrl_c_mid_transfer_releases_the_claim_and_resume_works(archive: Path) -> None:
    client = observer(archive, download_error=KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        do_pull(archive, client)

    assert held_by_this_run(client.at_download[0])
    assert not marker_path(archive).exists()
    # The spec 0007 resume command is the same pull; nothing blocks it.
    assert do_pull(archive, observer(archive)) == model_dir(archive)


def test_failed_leaf_cleanup_still_releases_the_claim(
    archive: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Spec 0019 residue stays; a tidiness failure never reads as a running pull."""
    leaf = staging_leaf(archive)
    monkeypatch.setattr(shutil, "rmtree", failing_rmtree(leaf))
    client = observer(archive)

    with caplog.at_level(logging.WARNING):
        assert do_pull(archive, client) == model_dir(archive)

    assert held_by_this_run(client.at_download[0])
    assert not marker_path(archive).exists()
    assert leaf.is_dir()  # the residue the human removes by hand
    warnings = [
        r for r in caplog.records if r.levelno >= logging.WARNING and str(leaf) in r.getMessage()
    ]
    assert len(warnings) == 1


# --- criterion 4: release only removes this run's own marker ---------------


def test_success_cleanup_keeps_the_claim_until_the_leaf_is_empty(
    archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The marker leaves last: no same-repo run can claim mid-cleanup.

    Removing the leaf with one rmtree deleted the marker first and the
    client's bookkeeping after it, a window in which a second pull could
    claim the leaf and lose its in-flight files (spec 0025 review round).
    """
    leaf = staging_leaf(archive)
    real_rmtree = shutil.rmtree
    held_after: list[bool] = []

    def rmtree_then_look(path: Any, *args: Any, **kwargs: Any) -> None:
        real_rmtree(path, *args, **kwargs)
        if leaf == Path(path) or leaf in Path(path).parents:
            held_after.append(held_by_this_run(read_or_none(marker_path(archive))))

    monkeypatch.setattr(shutil, "rmtree", rmtree_then_look)
    import llm_preserver.pull as pull_module

    real_discard = pull_module.discard_staging_leaf
    held_when_emptied: list[bool] = []

    def discard_then_look(path: Path, **kwargs: Any) -> None:
        real_discard(path, **kwargs)
        held_when_emptied.append(held_by_this_run(read_or_none(marker_path(archive))))

    monkeypatch.setattr(pull_module, "discard_staging_leaf", discard_then_look)
    client = observer(archive)

    do_pull(archive, client)

    assert held_after, "positive control: the success path removed staging content"
    assert all(held_after), "the marker went before the leaf's contents"
    assert held_when_emptied == [True], "the cleanup removed the marker itself"
    assert not marker_path(archive).exists()
    assert not leaf.exists()


def test_release_on_error_keeps_a_marker_swapped_in_mid_pull(archive: Path) -> None:
    foreign = json.dumps(FOREIGN_MARKER).encode("utf-8")
    client = observer(
        archive,
        on_download=lambda: marker_path(archive).write_bytes(foreign),
        download_error=PullHubError("hub returned 500; retry later"),
    )

    with pytest.raises(PullHubError):
        do_pull(archive, client, include=INCLUDE_Q4)

    assert held_by_this_run(client.at_download[0])
    assert marker_path(archive).read_bytes() == foreign
