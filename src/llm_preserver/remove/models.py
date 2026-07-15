"""Removal data types, exceptions, and path-safety primitives.

Shared by the planning and execution halves of the package. The path
helpers enforce the one invariant that matters most for a delete
command: never follow a symlink out of the archive tree (archives may
be copied from elsewhere, so every path is untrusted).
"""

from dataclasses import dataclass, field
from pathlib import Path

from llm_preserver.records import ModelRecord


class RemoveError(Exception):
    """A removal fault the CLI maps to exit 1 (archive/usage domain).

    Covers a symlinked model directory or payload (never followed out
    of the archive) and pattern removal against a model with no
    readable record (record surgery needs a record).
    """


class RemoveUserError(Exception):
    """A user-input fault the CLI maps to exit 2 and echoes.

    A pattern that matches nothing, or one that matches every archived
    file (which would empty the model silently — the message points at
    plain ``remove`` instead).
    """


class ModelNotFound(Exception):
    """No model directory and no staging leftovers for the given id.

    The CLI maps this to exit 2 with the archived-ids self-correction
    listing (``reject_unknown_model``), matching ``verify --model``.
    """


@dataclass
class PlannedFile:
    """One file a removal will delete.

    Attributes:
        path: Model-dir-relative POSIX path (the paths ``show`` lists).
        size: Bytes — from the record when recorded, from disk when the
            file is unrecorded or the record is unreadable.
        unrecorded: True when the file is on disk but not named by the
            record (``verify``'s ``unrecorded`` class).
    """

    path: str
    size: int | None
    unrecorded: bool


@dataclass
class RemovePlan:
    """What a removal will delete, computed before any deletion.

    Attributes:
        model_id: The ``<creator>/<model>`` id being removed.
        whole_model: True for whole-model removal, False for a
            pattern-scoped subset.
        files: The files to delete (payload; never the record itself).
        staging_dir: The interrupted-pull staging directory to clear
            (whole-model only), or None.
        model_dir: The model directory, or None when only staging
            leftovers exist (an interrupted pull that never completed).
        record_readable: Whether the model's record loaded and
            validated. A whole-model removal proceeds either way.
        total_size: Sum of ``files`` sizes (unknown sizes count as 0).
        include: The ``--include`` patterns that shaped a pattern
            removal, for the re-run hint. Empty for whole-model.
        record: The loaded record, kept for pattern-mode record
            surgery. None for whole-model.
    """

    model_id: str
    whole_model: bool
    files: list[PlannedFile]
    staging_dir: Path | None
    model_dir: Path | None
    record_readable: bool
    total_size: int
    include: tuple[str, ...] = ()
    record: ModelRecord | None = field(default=None, repr=False)


def reached_through_symlink(base: Path, creator: str, name: str) -> bool:
    """Whether ``base/creator/name`` traverses a symlink at any level.

    A copied archive may plant a symlink as the container, the creator
    directory, or the leaf; following any of them would let a deletion
    escape the archive tree. Refusing all three keeps ``remove`` inside
    ``models/`` (and ``.staging/``) no matter how the tree was crafted.
    """
    return (
        base.is_symlink() or (base / creator).is_symlink() or (base / creator / name).is_symlink()
    )


def escapes_model_dir(model_dir: Path, rel_path: str) -> bool:
    """Whether deleting ``model_dir/rel_path`` would leave the model dir.

    The same symlink posture as verify (spec 0009) and every other
    archive surface: a recorded path is untrusted, and an intermediate
    symlink component could redirect an ``unlink`` at data *outside* the
    archive. Such a target is refused — the offending symlink itself is
    still swept away by the directory removal, which never follows it.
    """
    target = model_dir / rel_path
    try:
        return target.is_symlink() or not target.resolve().is_relative_to(model_dir.resolve())
    except OSError:
        return True  # unresolvable path: refuse to touch it
