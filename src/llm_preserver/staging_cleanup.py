"""Removing a pull's staging directory once it has served its purpose.

Deleting the transfer client's leftover bookkeeping is not part of
preserving a model. By the time this runs the payload is archived,
hashed, and recorded — so a delete that cannot finish is a tidiness
problem, not a failed pull. The entry point here is best-effort: it
reports what it could not remove and never raises (spec 0019, after a
host dark wake broke an SMB durable handle mid-transfer and turned a
complete 160 GiB pull into exit 3).

``model_scan`` owns the read-only view of ``.staging/``. This module is
where *pull* deletes inside it; ``remove`` has its own path
(``remove.execute``), which is scoped to a model the human named.

The pull's claim marker (spec 0025) is the one entry the success path
leaves: ``pull_claim`` removes it last, then calls
:func:`discard_empty_staging_leaf` here. While the marker stands, no
second pull of the repo can claim the leaf, so nothing this module
deletes can belong to a live run.
"""

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def discard_staging_leaf(leaf: Path, *, keep: str) -> None:
    """Delete everything in a staging leaf except the entry named ``keep``.

    ``keep`` is the claim marker. The leaf and the marker go when the
    claim is released, after this returns, so the marker is the last
    thing to leave: a same-repo pull that started during this cleanup
    would otherwise claim the leaf and have its in-flight files deleted
    under it (spec 0025 review round).

    Never raises. A failure logs one WARNING that leads with the
    archive being complete, because the alarming reading — "my model is
    damaged" — is the wrong one, and the human deserves to be told so
    at the moment they read it rather than after they go looking.

    The message does not promise the leftover will clear itself. It
    will not: nothing in the tool removes a staging leaf except the
    pull that filled it and ``remove``, so a leaf that outlives its
    pull outlives it until a human deletes it (see TODO →
    ``verify --staging --clean``).

    Args:
        leaf: The ``.staging/<creator>/<model>/`` directory to empty.
        keep: The one entry to leave in place.
    """
    try:
        entries = [entry for entry in leaf.iterdir() if entry.name != keep]
    except FileNotFoundError:
        return
    except OSError as exc:
        _warn_leaf_left(leaf, exc, keep)
        return
    for entry in entries:
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink(missing_ok=True)
        except FileNotFoundError:
            continue  # gone already: the rest of the leaf still goes
        except OSError as exc:
            _warn_leaf_left(leaf, exc, keep)
            return


def _warn_leaf_left(leaf: Path, exc: OSError, keep: str) -> None:
    logger.warning(
        "the archive is complete and recorded; could not remove the pull's "
        "staging directory at %s (errno %s: %s). It holds the transfer "
        "client's bookkeeping, not archived data — remove it by hand when "
        "convenient, unless a pull of this repo is running (its directory "
        "then holds %s); `verify --staging` lists it until you do",
        leaf,
        exc.errno,
        exc.strerror,
        keep,
    )


def discard_empty_staging_leaf(leaf: Path) -> None:
    """Remove a staging leaf and its parent, but only while they are empty.

    Releasing a claim (spec 0025) must not delete anything a pull left
    to resume from, so this is ``os.rmdir`` and never ``rmtree``: a leaf
    still holding staged bytes, or another run's marker, stays. Never
    raises.

    Args:
        leaf: The ``.staging/<creator>/<model>/`` directory to remove.
    """
    try:
        leaf.rmdir()
    except OSError:
        return
    _discard_empty_creator_dir(leaf.parent)


def _discard_empty_creator_dir(creator_dir: Path) -> None:
    """Remove the leaf's parent when the leaf was its last entry.

    ``os.rmdir`` semantics and never ``rmtree`` (the spec 0017 rule):
    rmdir refuses a non-empty directory rather than descending, so a
    sibling model's staged bytes cannot be caught by this. A failure
    means the directory still holds something, which is the ordinary
    case and not worth a warning.
    """
    try:
        creator_dir.rmdir()
    except OSError:
        return
