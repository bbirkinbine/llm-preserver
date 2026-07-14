"""Archive fixity audit (spec 0009): complete versus valid, BagIt-style.

The record enumerates *expected* files; this module checks disk
against it, per file in cheap-first order — existence, then size, then
hash — failing fast so a missing or truncated file never pays for a
hash. Payloads and records are never modified; the one write is the
regenerable ``manifest-sha256.txt`` sidecar, refreshed after a full
(hashing) audit of any model with a readable record.

Payload hashes go through the ``llm_preserver.hashing`` seam as a
late-bound module attribute (``hashing.sha256_of``) so tests can count
and fault-inject every hash call. The record file's own manifest
digest deliberately does not: it must reflect the on-disk bytes, is
size-capped at load, and is not part of the payload-hash contract.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from llm_preserver import hashing
from llm_preserver.archive import iter_model_dirs, require_archive
from llm_preserver.pull_record import MANIFEST_FILENAME, write_manifest
from llm_preserver.records import (
    RECORD_FILENAME,
    RENDERED_FILENAME,
    ModelRecord,
    load_record,
)

DRIFT_STATES = frozenset({"incomplete", "invalid", "no-record", "record-unreadable"})

_TOOL_OWNED = frozenset({RECORD_FILENAME, RENDERED_FILENAME, MANIFEST_FILENAME})


@dataclass
class ProgressEvents:
    """Optional live-progress hooks fired while the audit runs.

    The core fires them; rendering (and TTY policy) is the caller's.
    All hooks are optional — an unset hook is simply skipped.

    Attributes:
        on_model_start: ``(model_id, file_count, recorded_bytes)``
            before a model's files are checked.
        on_file_start: ``(rel_path, recorded_size)`` before a file is
            hashed (full runs only — quick never hashes).
        on_file_bytes: Chunk byte counts while a file streams through
            the hash — the feed a byte counter renders from.
    """

    on_model_start: Callable[[str, int, int], None] | None = None
    on_file_start: Callable[[str, int | None], None] | None = None
    on_file_bytes: Callable[[int], None] | None = None


@dataclass(frozen=True)
class FileProblem:
    """One recorded file that failed its check.

    Attributes:
        path: Model-dir-relative path from the record.
        detail: What failed, naming expected versus actual.
    """

    path: str
    detail: str


@dataclass
class ModelVerifyResult:
    """Audit outcome for one model directory.

    Attributes:
        model_id: ``<creator>/<model>`` as laid out on disk.
        state: ``valid`` (full run, everything hashed clean),
            ``complete`` (quick run, everything present at recorded
            size), ``incomplete`` (recorded files missing or truncated),
            ``invalid`` (all present, but a hash/size mismatch or an
            unreadable payload), ``no-record``, or
            ``record-unreadable``.
        problems: The failing files, one entry each.
        unhashed: Recorded files with no SHA256 to check (existence and
            size verified only) — never counted valid, never a mismatch.
        unrecorded: On-disk files no record lists (informational;
            tool-owned generated files are exempt).
        manifest_error: Why the sidecar refresh failed, or None. A
            warning, not drift — a read-only-mounted archive is a
            legitimate preservation posture and its payloads still
            verify (adjudicated 2026-07-13).
    """

    model_id: str
    state: str
    problems: list[FileProblem] = field(default_factory=list)
    unhashed: list[str] = field(default_factory=list)
    unrecorded: list[str] = field(default_factory=list)
    manifest_error: str | None = None


@dataclass
class VerifyReport:
    """Archive-wide audit outcome.

    Attributes:
        quick: True when hashes were skipped (existence + size only).
        models: One result per audited model, sorted by model id.
    """

    quick: bool
    models: list[ModelVerifyResult] = field(default_factory=list)

    @property
    def drifted(self) -> bool:
        """True when any model is in a drift state (the exit-5 signal)."""
        return any(result.state in DRIFT_STATES for result in self.models)


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


def _unrecorded_files(model_dir: Path, record: ModelRecord) -> list[str]:
    """On-disk files no record lists, exempting tool-owned generated files."""
    recorded = {entry.path for artifact in record.artifacts for entry in artifact.files}
    found = []
    for path in model_dir.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(model_dir).as_posix()
        if rel not in recorded and rel not in _TOOL_OWNED:
            found.append(rel)
    return sorted(found)


def _verify_model(
    model_dir: Path, model_id: str, quick: bool, events: ProgressEvents
) -> ModelVerifyResult:
    """Audit one model directory against its record."""
    if not (model_dir / RECORD_FILENAME).is_file():
        return ModelVerifyResult(model_id=model_id, state="no-record")
    try:
        record = load_record(model_dir)
    except (ValidationError, ValueError, OSError):
        return ModelVerifyResult(model_id=model_id, state="record-unreadable")
    if events.on_model_start is not None:
        entries = [entry for artifact in record.artifacts for entry in artifact.files]
        events.on_model_start(model_id, len(entries), sum(entry.size or 0 for entry in entries))
    problems, unhashed, any_missing, any_hashed = _check_recorded_files(
        model_dir, record, quick, events
    )
    if quick:
        state = "incomplete" if problems else "complete"
    elif any_missing:
        state = "incomplete"
    elif problems:
        state = "invalid"
    elif not any_hashed:
        # Nothing was hashed (a hashless cache import, or an empty
        # record): the model is complete, but "valid" would overclaim
        # (adjudicated 2026-07-13).
        state = "complete"
    else:
        state = "valid"
    result = ModelVerifyResult(
        model_id=model_id,
        state=state,
        problems=problems,
        unhashed=unhashed,
        unrecorded=_unrecorded_files(model_dir, record),
    )
    if not quick:
        # Refresh the sidecar for every readable record, drifted models
        # included — it derives from the record, which stays the truth.
        # The record line hashes the on-disk bytes: a re-serialization
        # need not match the file, and `sha256sum -c` would reject it.
        # A failed refresh (read-only mount, full disk) is a warning,
        # never a crash and never drift — the payload verdict stands.
        try:
            disk_record_sha256 = hashlib.sha256(
                (model_dir / RECORD_FILENAME).read_bytes()
            ).hexdigest()
            write_manifest(model_dir, record, record_sha256=disk_record_sha256)
        except OSError as exc:
            result.manifest_error = str(exc)
    return result


def verify_archive(
    root: Path,
    model: str | None = None,
    quick: bool = False,
    on_result: Callable[[ModelVerifyResult], None] | None = None,
    events: ProgressEvents | None = None,
) -> VerifyReport:
    """Audit the archive (or one model) against its records.

    Args:
        root: The archive root.
        model: Optional ``<creator>/<model>`` id restricting the audit
            to one model directory; None audits every model.
        quick: Skip hashing — existence and size only. Quick runs
            report ``complete``/``incomplete`` (never ``valid``) and
            never write or refresh a manifest sidecar.
        on_result: Optional callback invoked with each model's result
            as it completes — the streaming report body.
        events: Optional live-progress hooks (model start, file start,
            hash byte counts) so a caller can keep a long run visibly
            alive; rendering policy is entirely the caller's.

    Returns:
        The archive-wide report, models sorted by model id.

    Raises:
        ArchiveError: If ``root`` is not a usable archive or its
            ``models/`` tree is unsafe to walk.
        KeyboardInterrupt: Propagated untouched mid-audit; a model's
            sidecar is only written after its checks complete, so an
            interrupt never leaves a partial or refreshed manifest.
    """
    require_archive(root)
    hooks = events if events is not None else ProgressEvents()
    report = VerifyReport(quick=quick)
    for model_id, model_dir in iter_model_dirs(root):
        if model is not None and model_id != model:
            continue
        result = _verify_model(model_dir, model_id, quick, hooks)
        report.models.append(result)
        if on_result is not None:
            on_result(result)
    return report
