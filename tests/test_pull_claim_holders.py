"""Spec 0025: how a claim judges a marker that is already there.

Criteria 2 and 5-8 at the unit level. Every planted marker starts from
``holder()`` — well-formed, this host, a dead pid — which is the one
shape the claim may replace (``test_dead_holder_on_this_host_is_replaced``
proves it). Each refusal test changes exactly one thing about that
shape, so a refusal is caused by that change and cannot be satisfied by
a claim that refuses everything. Refused markers are checked byte for
byte afterward: never stale, never deleted (criterion 2).

The chmod-000 test is skipped as root (root ignores file modes); the
``ESTALE`` test covers the same refusal everywhere, including CI.

Expected red (test-first): ``llm_preserver.pull_claim`` does not exist.
"""

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pull_claim_shapes import (
    DEAD_PID,
    MARKER_NAME,
    OTHER_HOST,
    OTHER_USER_PID,
    STARTED,
    THIS_HOST,
    estale_on_read,
    holder,
    install_kill,
    pin_host,
    plant,
)
from staging_shapes import REPO_ID

from llm_preserver.archive import init_archive
from llm_preserver.hub import PullEnvError
from llm_preserver.model_scan import staging_leftovers


@pytest.fixture
def leaf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A leaf to claim, on a host pinned to ``THIS_HOST``."""
    pin_host(monkeypatch)
    return tmp_path / "archive" / ".staging" / "bartowski" / "tiny-chat-GGUF"


def claim_or_refusal(leaf: Path) -> Exception | None:
    """Try to claim ``leaf``; return the refusal, or None if it was claimed."""
    from llm_preserver.pull_claim import claim_pull

    try:
        with claim_pull(leaf, REPO_ID):
            pass
    except PullEnvError as exc:
        return exc
    return None


def test_in_progress_refusal_is_a_local_environment_error() -> None:
    from llm_preserver.pull_claim import PullInProgressError

    assert issubclass(PullInProgressError, PullEnvError)


# --- criterion 5: a running holder ------------------------------------------


def test_running_holder_on_this_host_refuses_naming_everything(
    leaf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from llm_preserver.pull_claim import PullInProgressError

    install_kill(monkeypatch)
    marker = leaf / MARKER_NAME
    raw = plant(marker, holder(tmp_path, pid=os.getpid()))

    refusal = claim_or_refusal(leaf)

    assert isinstance(refusal, PullInProgressError)
    message = str(refusal)
    for fact in (REPO_ID, THIS_HOST, str(os.getpid()), STARTED, str(marker)):
        assert fact in message, fact
    assert marker.read_bytes() == raw


# --- criterion 6: stale on this host ----------------------------------------


def test_dead_holder_on_this_host_is_replaced(
    leaf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from llm_preserver.pull_claim import claim_pull

    kills = install_kill(monkeypatch)
    plant(leaf / MARKER_NAME, holder(tmp_path))

    with caplog.at_level(logging.INFO, logger="llm_preserver"), claim_pull(leaf, REPO_ID):
        now = json.loads((leaf / MARKER_NAME).read_text("utf-8"))

    assert now["pid"] == os.getpid()
    assert DEAD_PID in kills
    cleared = [
        r for r in caplog.records if r.levelno == logging.INFO and str(DEAD_PID) in r.getMessage()
    ]
    assert len(cleared) == 1


def test_other_users_pid_counts_as_running(
    leaf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from llm_preserver.pull_claim import PullInProgressError

    kills = install_kill(monkeypatch)
    raw = plant(leaf / MARKER_NAME, holder(tmp_path, pid=OTHER_USER_PID))

    refusal = claim_or_refusal(leaf)

    assert OTHER_USER_PID in kills  # it was asked, and the answer was "alive"
    assert isinstance(refusal, PullInProgressError)
    assert (leaf / MARKER_NAME).read_bytes() == raw


# --- criterion 7: another host ----------------------------------------------


def test_foreign_host_holder_is_never_judged_stale(
    leaf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from llm_preserver.pull_claim import PullInProgressError

    kills = install_kill(monkeypatch)
    marker = leaf / MARKER_NAME
    ancient = "2001-01-01T00:00:00+00:00"  # age never expires a claim
    raw = plant(marker, holder(tmp_path, hostname=OTHER_HOST, started=ancient))

    refusal = claim_or_refusal(leaf)

    assert isinstance(refusal, PullInProgressError)
    assert kills == []  # a pid from another host means nothing here
    message = str(refusal)
    assert OTHER_HOST in message
    assert str(marker) in message
    assert "delete" in message.lower()
    assert marker.read_bytes() == raw

    # Differential: the same dead pid under this host's name is replaced.
    twin = leaf.parent / "twin"
    plant(twin / MARKER_NAME, holder(tmp_path, started=ancient))
    assert claim_or_refusal(twin) is None
    assert kills == [DEAD_PID]


# --- criterion 2: anything not this shape is refused, never deleted ---------


def _without(field: str) -> Callable[[dict[str, Any]], bytes]:
    def mutate(base: dict[str, Any]) -> bytes:
        return json.dumps({k: v for k, v in base.items() if k != field}).encode()

    return mutate


def _with(**changes: Any) -> Callable[[dict[str, Any]], bytes]:
    return lambda base: json.dumps({**base, **changes}).encode()


MALFORMED: dict[str, Callable[[dict[str, Any]], bytes]] = {
    "empty": lambda base: b"",
    "not-json": lambda base: b"{not json",
    "json-list": lambda base: json.dumps([base]).encode(),
    "wrong-tool": _with(tool="some-other-tool"),
    "no-tool": _without("tool"),
    "no-repo-id": _without("repo_id"),
    "no-hostname": _without("hostname"),
    "no-pid": _without("pid"),
    "no-started": _without("started"),
    "pid-zero": _with(pid=0),
    "pid-negative": _with(pid=-1),
    "pid-bool": _with(pid=True),
    "pid-string": _with(pid=str(DEAD_PID)),
    "pid-too-large": _with(pid=2**31),
    # 4000 bytes, under the size cap: valid JSON, but not an object.
    "deeply-nested": lambda base: b"[" * 2000 + b"]" * 2000,
    "extra-field": _with(note="anything else"),
    "repo-id-empty": _with(repo_id=""),
    "hostname-empty": _with(hostname=""),
    "started-empty": _with(started=""),
    # The same five valid fields, padded with whitespace past 4 KiB.
    "oversized": lambda base: json.dumps(base).encode()[:-1] + b" " * 4200 + b"}",
    # Valid JSON then trailing padding: a capped read alone would parse it.
    "oversized-trailing": lambda base: json.dumps(base).encode() + b" " * 4200,
    "started-not-a-date": _with(started="not a date"),
}


@pytest.mark.parametrize("variant", sorted(MALFORMED))
def test_malformed_marker_is_refused_and_left_byte_identical(
    variant: str, leaf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kills = install_kill(monkeypatch)
    marker = leaf / MARKER_NAME
    raw = plant(marker, MALFORMED[variant](holder(tmp_path)))

    refusal = claim_or_refusal(leaf)

    assert refusal is not None, "a malformed marker was treated as stale"
    assert str(marker) in str(refusal)
    assert "delete that file" in str(refusal)  # the remedy, not "wait"
    assert marker.read_bytes() == raw
    assert kills == []  # never a liveness probe on an unvalidated pid


def test_symlinked_marker_is_refused_and_its_target_untouched(
    leaf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_kill(monkeypatch)
    target = tmp_path / "elsewhere.json"
    raw = plant(target, holder(tmp_path))  # stale-shaped: replaceable if followed
    leaf.mkdir(parents=True)
    (leaf / MARKER_NAME).symlink_to(target)

    refusal = claim_or_refusal(leaf)

    assert refusal is not None
    assert str(leaf / MARKER_NAME) in str(refusal)
    assert (leaf / MARKER_NAME).is_symlink()
    assert target.read_bytes() == raw


# --- criterion 8: a marker that cannot be read ------------------------------


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_unreadable_marker_is_a_clean_refusal_naming_the_path(
    leaf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_kill(monkeypatch)
    marker = leaf / MARKER_NAME
    raw = plant(marker, holder(tmp_path))  # stale-shaped: readable, it would go
    marker.chmod(0o000)
    try:
        refusal = claim_or_refusal(leaf)
    finally:
        marker.chmod(0o644)

    assert refusal is not None
    assert str(marker) in str(refusal)
    assert "delete that file" in str(refusal)
    assert marker.read_bytes() == raw


def test_stale_handle_marker_is_a_clean_refusal_naming_the_path(
    leaf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_kill(monkeypatch)
    marker = leaf / MARKER_NAME
    plant(marker, holder(tmp_path))
    # Differential: the same stale-shaped marker, readable, is replaced.
    twin = leaf.parent / "twin"
    plant(twin / MARKER_NAME, holder(tmp_path))
    assert claim_or_refusal(twin) is None

    estale_on_read(monkeypatch, marker)
    refusal = claim_or_refusal(leaf)

    assert refusal is not None
    assert str(marker) in str(refusal)
    assert "delete that file" in str(refusal)
    assert marker.exists()


# --- criterion 11: verify --staging still sees a running pull ---------------


def test_a_leaf_holding_only_a_marker_is_a_staging_leftover(tmp_path: Path) -> None:
    from llm_preserver.pull_claim import PULL_MARKER_FILENAME

    root = tmp_path / "archive"
    init_archive(root)
    plant(root / ".staging" / "bartowski" / "tiny-chat-GGUF" / PULL_MARKER_FILENAME, b"{}")

    leftovers = staging_leftovers(root)

    assert [(x.model_id, x.file_count) for x in leftovers] == [(REPO_ID, 1)]
