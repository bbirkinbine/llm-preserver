"""Per-model and archive-wide audit orchestration (specs 0009, 0017)."""

import hashlib
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from llm_preserver.archive import iter_model_dirs, require_archive
from llm_preserver.layout import layout_state
from llm_preserver.model_scan import unrecorded_files
from llm_preserver.pull_record import write_manifest
from llm_preserver.records import MANIFEST_FILENAME, RECORD_FILENAME, load_record
from llm_preserver.verify.files import _check_recorded_files
from llm_preserver.verify.models import (
    ModelVerifyResult,
    ProgressEvents,
    VerifyReport,
)


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
    # Record-only, so the verdict costs no file I/O and a --quick audit
    # reaches it too (spec 0017 criterion 3).
    verdict = layout_state(model_id, record)
    result = ModelVerifyResult(
        model_id=model_id,
        state=state,
        layout=verdict.state,
        offending_repo=verdict.offending_repo,
        problems=problems,
        unhashed=unhashed,
        unrecorded=unrecorded_files(model_dir, record),
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
            manifest_path = model_dir / MANIFEST_FILENAME
            before = manifest_path.read_bytes() if manifest_path.is_file() else b""
            write_manifest(model_dir, record, record_sha256=disk_record_sha256)
            # Say so when it changed. Regenerating derived output is
            # routine; doing it silently hides that the offline
            # `sha256sum -c` check would have failed until this run.
            result.manifest_regenerated = manifest_path.read_bytes() != before
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
