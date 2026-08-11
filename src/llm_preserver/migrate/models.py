"""Migration plan types and fault domains (spec 0017, ADR 0003).

Mirrors ``remove``'s plan/execute/models split: a plan is inert data a
human can read before anything moves, and the two exception types keep
"the archive is in a state I refuse to touch" separate from "you asked
for something I cannot do".
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


class MigrateError(Exception):
    """The archive is in a state migration refuses to act on.

    Collisions, unreadable records, a ``source_repo`` that names no
    usable repo, a recorded path escaping the archive, a
    cross-filesystem move. Always raised *before* anything moves when
    the plan can see it — the whole run refuses rather than converting
    half an archive and stopping at the first bad directory.
    """


class MigrateUserError(MigrateError):
    """The request itself cannot be satisfied (bad ``--repo``, bad dest)."""


@dataclass
class MigrateEvents:
    """Optional progress hooks fired while a migration runs.

    A seam, not a printer: the core stays silent and testable, and the
    CLI decides whether a human is watching. Silence during a bulk move
    over a network share reads as a hang (live use, 2026-08-11 — the
    same finding spec 0009 made about long hashes).

    Attributes:
        on_directory_start: ``(model_id, file_count, total_bytes)`` as
            each directory begins converting.
        on_file: ``(rel_path, size)`` as each file is about to move or
            copy — the unit that actually takes time in ``--to`` mode.
    """

    on_directory_start: Callable[[str, int, int], None] | None = None
    on_file: Callable[[str, int], None] | None = None


@dataclass(frozen=True)
class ArtifactMove:
    """One artifact's files relocating to the repo that published them.

    Attributes:
        repo_id: The source repo the files came from — the id whose
            directory they are moving into.
        target_dir: Absolute destination model directory.
        files: Model-dir-relative paths, unchanged by the move. This is
            why recorded hashes survive: only the directory above them
            changes.
        total_size: Recorded bytes moving, for the preview.
    """

    repo_id: str
    target_dir: Path
    files: list[str]
    total_size: int


@dataclass
class DirectoryMigration:
    """Everything one model directory needs done to it.

    Attributes:
        model_id: The directory's current ``<owner>/<repo>`` id.
        model_dir: Its absolute path.
        kind: ``rename`` when every artifact is foreign and shares one
            source (the directory becomes that repo's), or ``split``
            when the directory keeps files of its own.
        moves: One entry per foreign artifact.
        removed_dirs: Directories the run will ``os.rmdir`` once
            drained — never ``rmtree``, and never promised for a
            directory that will still hold something.
    """

    model_id: str
    model_dir: Path
    kind: str
    moves: list[ArtifactMove] = field(default_factory=list)
    removed_dirs: list[Path] = field(default_factory=list)


@dataclass
class MigratePlan:
    """The whole conversion, derived from disk and never stored.

    Attributes:
        units: One per affected directory, sorted by model id.
        warnings: Non-fatal notes for the preview (e.g. a directory
            that cannot be emptied because something unrecorded sits
            in it).
    """

    units: list[DirectoryMigration] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        """Recorded bytes that will move."""
        return sum(move.total_size for unit in self.units for move in unit.moves)

    @property
    def is_empty(self) -> bool:
        """True when the archive already obeys ADR 0003."""
        return not self.units
