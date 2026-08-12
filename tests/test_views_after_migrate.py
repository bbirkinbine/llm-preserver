"""Runtime views survive a migration — spec 0017 pass 5, criterion 18.

The spec refuses to assume this one. Migration moves every model
directory, so a view tree built beforehand points at paths that no
longer exist; whether re-running ``views`` *repairs* that tree depends
on a containment check written for a different purpose
(``_prune_stale_blob_links`` keeps only links resolving into the
archive, and a dangling link's resolution is not obviously still inside
it). Criterion 18 says to find out by testing rather than by reading.

Nothing here needs Ollama installed: the view tree is symlinks and
small JSON, and what matters is where the links point.
"""

import json
from pathlib import Path

import pytest
from migrate_shapes import (
    Q4_REL,
    RENAME_DIR_ID,
    RENAME_TARGET_ID,
    init_archive_dir,
    pure_rename_archive,
)

from llm_preserver.migrate import execute_migration, plan_migration
from llm_preserver.views import VIEW_MARKER_FILENAME, build_view


def links_in(dest: Path) -> list[Path]:
    """Every symlink in a built view tree, in sorted order."""
    return sorted(p for p in dest.rglob("*") if p.is_symlink())


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    root = init_archive_dir(tmp_path)
    pure_rename_archive(root)
    return root


def test_a_view_built_before_migration_points_into_the_old_directory(
    archive: Path, tmp_path: Path
) -> None:
    # The premise: without this, the repair test below proves nothing.
    dest = tmp_path / "view"

    build_view(archive, dest=dest, seed=True)

    targets = [str(link.readlink()) for link in links_in(dest)]
    assert any(RENAME_DIR_ID.split("/")[1] in target for target in targets)


def test_rerunning_views_after_migration_repoints_every_link(archive: Path, tmp_path: Path) -> None:
    dest = tmp_path / "view"
    build_view(archive, dest=dest, seed=True)
    execute_migration(archive, plan_migration(archive))

    build_view(archive, dest=dest, seed=True)

    links = links_in(dest)
    assert links, "the rebuilt view should still carry blob links"
    for link in links:
        assert link.resolve().is_file(), f"{link} dangles after the rebuild"


def test_no_link_still_points_at_the_pre_migration_path(archive: Path, tmp_path: Path) -> None:
    # The criterion-18 question stated directly: a stale link that the
    # prune pass fails to notice would leave the tree half-broken and
    # only fail at model-load time.
    dest = tmp_path / "view"
    build_view(archive, dest=dest, seed=True)
    old_dir = archive / "models" / RENAME_DIR_ID
    execute_migration(archive, plan_migration(archive))

    build_view(archive, dest=dest, seed=True)

    for link in links_in(dest):
        assert old_dir not in link.readlink().parents


def test_the_rebuilt_links_point_into_the_publishers_new_directory(
    archive: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "view"
    build_view(archive, dest=dest, seed=True)
    execute_migration(archive, plan_migration(archive))

    build_view(archive, dest=dest, seed=True)

    new_payload = (archive / "models" / RENAME_TARGET_ID / Q4_REL).resolve()
    assert any(link.resolve() == new_payload for link in links_in(dest))


def test_the_view_marker_still_names_this_archive_after_migration(
    archive: Path, tmp_path: Path
) -> None:
    # The marker is what gives a rebuild delete/prune rights over the
    # dest; migration must not invalidate it, or the repair itself is
    # refused.
    dest = tmp_path / "view"
    build_view(archive, dest=dest, seed=True)
    execute_migration(archive, plan_migration(archive))

    build_view(archive, dest=dest, seed=True)

    marker = json.loads((dest / VIEW_MARKER_FILENAME).read_text(encoding="utf-8"))
    assert Path(marker["archive_root"]).resolve() == archive.resolve()
