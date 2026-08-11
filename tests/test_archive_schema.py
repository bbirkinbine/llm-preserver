"""Tests for the archive marker's schema version — spec 0017 pass 1.

ADR 0003 moves both version numbers: archive ``schema_version`` 1 -> 2,
because a tool that only knows the nested layout must refuse a
converted archive rather than misread it.

Read paths deliberately keep working on a v1 archive: pass 1 only
bumps the number and teaches ``verify`` the ``unmigrated`` verdict.
The content gate that refuses ``pull`` / ``remove`` on a v1 archive
(criteria 23-25) is pass 2 and is not asserted here.
"""

import json
from pathlib import Path

import pytest

from llm_preserver.archive import (
    MARKER_FILENAME,
    SCHEMA_VERSION,
    ArchiveError,
    init_archive,
    require_archive,
)


def write_marker(root: Path, version: int) -> None:
    """Lay down an archive skeleton marked at ``version``."""
    for name in ("models", "runtimes", "manifests"):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / MARKER_FILENAME).write_text(
        json.dumps({"tool": "llm-preserver", "schema_version": version}),
        encoding="utf-8",
    )


def test_archive_schema_version_is_two() -> None:
    # Raised in the pass that makes `pull` write one-directory-per-repo
    # (adjudicated 2026-08-11): a marker claiming v2 over v1 content
    # would be worse than no marker at all.
    assert SCHEMA_VERSION == 2


def test_init_writes_a_marker_at_the_current_schema(tmp_path: Path) -> None:
    init_archive(tmp_path)

    marker = json.loads((tmp_path / MARKER_FILENAME).read_text(encoding="utf-8"))

    assert marker["schema_version"] == SCHEMA_VERSION


def test_an_archive_marked_newer_than_this_tool_is_still_refused(tmp_path: Path) -> None:
    # Regression guard for the ADR 0001 rule the bump must not break:
    # this passes today and must keep passing after the bump, which is
    # why it is written against SCHEMA_VERSION rather than a literal.
    write_marker(tmp_path, SCHEMA_VERSION + 1)

    with pytest.raises(ArchiveError):
        require_archive(tmp_path)


def test_a_v1_archive_is_still_readable(tmp_path: Path) -> None:
    # Criterion 23 keeps status / show / verify / views working on an
    # unconverted archive, so the marker check must not refuse v1 —
    # otherwise migrate could never read the archive it exists to fix.
    write_marker(tmp_path, 1)

    require_archive(tmp_path)  # no raise


def test_init_does_not_flip_an_existing_v1_marker(tmp_path: Path) -> None:
    # Criterion 24: the marker flips 1 -> 2 only after a full successful
    # migration, which makes the flip the single durable signal that
    # migration finished. A second `init` over a v1 archive must not
    # forge that signal.
    import json

    from llm_preserver.archive import MARKER_FILENAME, init_archive

    init_archive(tmp_path)
    marker_path = tmp_path / MARKER_FILENAME
    marker_path.write_text(json.dumps({"tool": "llm-preserver", "schema_version": 1}))

    init_archive(tmp_path)

    assert json.loads(marker_path.read_text())["schema_version"] == 1
