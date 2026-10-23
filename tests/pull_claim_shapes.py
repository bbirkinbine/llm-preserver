"""Shared shapes for the spec 0025 same-repo pull-claim tests.

Mirrors ``staging_shapes.py``: plain helpers, no fixtures. Nothing here
imports ``llm_preserver.pull_claim`` at module level, so a test that
does not need the new module fails on its own assertion rather than on
a collection error, and the red output shows one failure mode per test.

Markers are planted, never mocked: a test writes the bytes another run
would have left at ``.staging/<owner>/<repo>/.llm-preserver-pull.json``
and lets the real exclusive create lose against them. Liveness is the
one seam faked (``os.kill``), because a dead pid cannot be manufactured
portably and a foreign-user pid cannot be manufactured at all. Every
archive lives in ``tmp_path``; never a real one.
"""

import builtins
import errno
import io
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import DEFAULT_HUB_FILES, FakeHubClient
from staging_shapes import REPO_ID, staging_leaf

MARKER_NAME = ".llm-preserver-pull.json"
"""The spec's marker filename, spelled out so helpers need no import."""

FIELDS = {"tool", "repo_id", "hostname", "pid", "started"}
THIS_HOST = "host-a"
OTHER_HOST = "host-b"
DEAD_PID = 987_651
"""A pid the faked ``os.kill`` reports as gone (``ProcessLookupError``)."""
OTHER_USER_PID = 987_652
"""A pid the faked ``os.kill`` reports as another user's (``PermissionError``)."""
STARTED = "2026-10-23T08:15:00+00:00"


def marker_path(root: Path) -> Path:
    """Where a pull of ``REPO_ID`` into ``root`` keeps its claim."""
    return staging_leaf(root) / MARKER_NAME


def read_or_none(path: Path) -> bytes | None:
    """The file's bytes, or None when it is absent."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def held_by_this_run(raw: bytes | None) -> bool:
    """True when ``raw`` is a marker naming this test process as holder."""
    if raw is None:
        return False
    data = json.loads(raw)
    return isinstance(data, dict) and data.get("pid") == os.getpid()


def pin_host(monkeypatch: pytest.MonkeyPatch, host: str = THIS_HOST) -> None:
    """Fix the hostname the claim compares holders against (the plan's seam)."""
    import llm_preserver.pull_claim as pull_claim

    monkeypatch.setattr(pull_claim, "_this_host", lambda: host)


def real_marker(scratch: Path) -> dict[str, Any]:
    """The marker this tool writes, captured from a real claim.

    Planted holders are derived from this rather than from a hand-typed
    dict, so the tests never guess the ``tool`` value — they only change
    the one field each test is about.
    """
    from llm_preserver.pull_claim import PULL_MARKER_FILENAME, claim_pull

    leaf = scratch / "probe" / "owner" / "repo"
    with claim_pull(leaf, REPO_ID):
        data: dict[str, Any] = json.loads((leaf / PULL_MARKER_FILENAME).read_text("utf-8"))
    return data


def holder(scratch: Path, **overrides: Any) -> dict[str, Any]:
    """A well-formed marker from a dead pid on this host — stale by default.

    The default is the one shape the claim may replace, so a test that
    changes one field and sees a refusal knows that field caused it.
    """
    data = real_marker(scratch)
    data.update(repo_id=REPO_ID, hostname=THIS_HOST, pid=DEAD_PID, started=STARTED)
    data.update(overrides)
    return data


def plant(path: Path, content: bytes | dict[str, Any]) -> bytes:
    """Write a marker another run left behind; return its exact bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = content if isinstance(content, bytes) else json.dumps(content).encode("utf-8")
    path.write_bytes(raw)
    return raw


FOREIGN_MARKER = {
    "tool": "llm-preserver",
    "repo_id": REPO_ID,
    "hostname": OTHER_HOST,
    "pid": 4242,
    "started": STARTED,
}
"""Another run's marker for tests that only need *some* other holder.

Release must leave any marker that is not this run's own, so the exact
``tool`` value is irrelevant here; a literal keeps these tests free of
the new module and lets them fail on their own assertions today.
"""


def install_kill(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Fake ``os.kill`` liveness; return the list of pids it was asked about."""
    real = os.kill
    calls: list[int] = []

    def fake(pid: int, sig: int) -> None:
        calls.append(pid)
        if pid == DEAD_PID:
            raise ProcessLookupError(errno.ESRCH, "No such process")
        if pid == OTHER_USER_PID:
            raise PermissionError(errno.EPERM, "Operation not permitted")
        real(pid, sig)

    monkeypatch.setattr(os, "kill", fake)
    return calls


def estale_on_read(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    """Make every read-open of ``target`` raise ``ESTALE`` (NFS/SMB stale handle).

    Creates still reach the real call, so the exclusive create loses with
    ``EEXIST`` exactly as it would on the share; only the follow-up read
    fails. Covers ``os.open`` and both spellings of ``open`` so the test
    does not depend on how the implementation reads the file.
    """
    real_os_open = os.open
    real_io_open = io.open

    def is_target(file: Any) -> bool:  # Any: open() accepts paths and fds
        try:
            return Path(os.fsdecode(file)) == target
        except TypeError:
            return False

    def stale() -> OSError:
        return OSError(errno.ESTALE, "Stale NFS file handle", str(target))

    def fake_os_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if is_target(path) and not flags & os.O_CREAT:
            raise stale()
        return real_os_open(path, flags, *args, **kwargs)

    def fake_io_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if is_target(file) and not any(flag in mode for flag in "wax"):
            raise stale()
        return real_io_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(os, "open", fake_os_open)
    monkeypatch.setattr(io, "open", fake_io_open)
    monkeypatch.setattr(builtins, "open", fake_io_open)


class ObservingHub(FakeHubClient):
    """``FakeHubClient`` that reads the claim marker at every hub call.

    ``at_repo_info`` / ``at_download`` hold what the marker file
    contained (or None) at the moment planning and each transfer reached
    the hub — the observable proof of *when* the claim is held, with no
    mock of the filesystem. ``on_download`` runs after the read and
    before the bytes are served, so a test can act mid-transfer.
    """

    def __init__(
        self,
        marker: Path,
        *,
        on_download: Callable[[], None] | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("files", DEFAULT_HUB_FILES)
        kwargs.setdefault("base_model", None)
        super().__init__(**kwargs)
        self.marker = marker
        self.on_download = on_download
        self.at_repo_info: list[bytes | None] = []
        self.at_download: list[bytes | None] = []

    def repo_info(self, repo_id: str, revision: str | None = None) -> Any:
        self.at_repo_info.append(read_or_none(self.marker))
        return super().repo_info(repo_id, revision)

    def download(self, repo_id: str, filename: str, revision: str, dest_dir: Any) -> Path:
        self.at_download.append(read_or_none(self.marker))
        if self.on_download is not None:
            self.on_download()
        return super().download(repo_id, filename, revision, dest_dir)
