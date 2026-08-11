"""Per-file checking: existence, then size, then hash (spec 0009).

Payload hashes go through the ``llm_preserver.hashing`` seam as a
late-bound module attribute (``hashing.sha256_of``) so tests can count
and fault-inject every hash call.
"""

from pathlib import Path

from llm_preserver import hashing
from llm_preserver.records import ModelRecord
from llm_preserver.verify.models import FileProblem, ProgressEvents


def _check_recorded_files(
    model_dir: Path, record: ModelRecord, quick: bool, events: ProgressEvents
) -> tuple[list[FileProblem], list[str], bool, bool]:
    """Check every recorded file.

    Returns:
        ``(problems, unhashed, any_missing, any_hashed)`` —
        ``any_hashed`` is False when no file's digest was checked, so
        the caller can withhold the ``valid`` label (a model whose
        record carries no hashes was never validated, only found
        complete).
    """
    problems: list[FileProblem] = []
    unhashed: list[str] = []
    any_missing = False
    any_hashed = False
    base = model_dir.resolve()
    for artifact in record.artifacts:
        for entry in artifact.files:
            target = model_dir / entry.path
            # Same symlink posture as every other archive surface (the
            # walk, the record load, the unrecorded scan): pull never
            # writes symlinks, so one where a payload should be is
            # out-of-band drift — and following it would read (and on
            # mismatch, print the hash of) files outside the archive.
            try:
                escapes = target.is_symlink() or not target.resolve().is_relative_to(base)
            except OSError as exc:
                problems.append(FileProblem(entry.path, f"unresolvable path: {exc}"))
                continue
            if escapes:
                problems.append(
                    FileProblem(
                        entry.path,
                        "recorded path is (or crosses) a symlink leaving the model"
                        " directory; refusing to read it",
                    )
                )
                continue
            if not target.is_file():
                any_missing = True
                problems.append(FileProblem(entry.path, "recorded but missing from disk"))
                continue
            try:
                disk_size = target.stat().st_size
            except OSError as exc:
                problems.append(FileProblem(entry.path, f"unreadable: {exc}"))
                continue
            if entry.size is not None and disk_size != entry.size:
                problems.append(
                    FileProblem(
                        entry.path,
                        f"size mismatch: record says {entry.size} bytes, disk has {disk_size}",
                    )
                )
                continue
            if entry.sha256 is None:
                unhashed.append(entry.path)
                continue
            if quick:
                continue
            if events.on_file_start is not None:
                events.on_file_start(entry.path, entry.size)
            try:
                disk_hash = hashing.sha256_of(target, progress=events.on_file_bytes)
            except OSError as exc:
                problems.append(FileProblem(entry.path, f"unreadable: {exc}"))
                continue
            any_hashed = True
            if disk_hash != entry.sha256.lower():
                problems.append(
                    FileProblem(
                        entry.path,
                        f"hash mismatch: record says {entry.sha256}, disk hashes to {disk_hash}",
                    )
                )
    return problems, unhashed, any_missing, any_hashed
