"""Verify's result types and state vocabulary (specs 0009, 0017)."""

from collections.abc import Callable
from dataclasses import dataclass, field

DRIFT_STATES = frozenset({"incomplete", "invalid", "no-record", "record-unreadable"})
LAYOUT_STATES = frozenset({"ok", "unmigrated"})
"""Layout verdicts, reported *alongside* the fixity state (spec 0017).

Deliberately a separate field rather than a sixth ``state`` value: the
two answer different questions, and collapsing them would erase one.
Spec 0009's complete-vs-valid vocabulary has to survive — a ``--quick``
run must stay distinguishable from a completed hash run, and a hashless
record must still say ``complete``, never ``valid`` — so the layout
word is appended (``valid, unmigrated``), matching how a drift state
already renders. Precedence lives in the exit code: drift outranks
layout, 5 over 1 (adjudicated 2026-08-11)."""


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
        model_id: ``<owner>/<repo>`` as laid out on disk.
        state: The *fixity* verdict, independent of layout —
            ``valid`` (full run, everything hashed clean),
            ``complete`` (quick run, or a record carrying no hashes),
            ``incomplete`` (recorded files missing or truncated),
            ``invalid`` (all present, but a hash/size mismatch or an
            unreadable payload), ``no-record``, or
            ``record-unreadable``.
        layout: The ADR 0003 verdict — ``ok``, or ``unmigrated`` when
            the directory holds another repo's files. Reported beside
            ``state``, never in place of it.
        offending_repo: The repo id contradicting the directory's path,
            or None when the layout is sound. Set whenever the layout
            is wrong — including when a drift state won the ``state``
            field — so losing the precedence contest never hides the
            layout problem.
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
    layout: str = "ok"
    offending_repo: str | None = None
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

    @property
    def unmigrated(self) -> bool:
        """True when any model's layout is wrong (the exit-1 signal)."""
        return any(result.layout == "unmigrated" for result in self.models)
