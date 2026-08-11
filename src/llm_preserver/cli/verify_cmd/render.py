"""Result, summary, and staging line rendering for verify."""

from collections import Counter

import typer

from llm_preserver.model_scan import StagingLeftover
from llm_preserver.pull_preflight import human_size
from llm_preserver.render import clean_text
from llm_preserver.verify import ModelVerifyResult, VerifyReport

_STATE_LABELS = {"no-record": "no record", "record-unreadable": "record unreadable"}

_MIGRATE_REMEDY = "run 'migrate' to give it its own directory"
_LAYOUT_DETAIL = "record and files disagree with the directory"


def _echo_result(result: ModelVerifyResult) -> None:
    """Print one model's result line plus its per-file detail lines.

    Called as each model completes — this stream is the progress
    display for long runs as well as the report body.
    """
    label = _STATE_LABELS.get(result.state, result.state)
    if result.layout == "unmigrated":
        # Appended, never substituted: the fixity word answers "did the
        # bytes check out" and must survive, or a --quick run becomes
        # indistinguishable from a completed hash run and 0009's
        # complete-vs-valid distinction is erased (adjudicated
        # 2026-08-11).
        label = f"{label}, unmigrated"
    typer.echo(clean_text(f"{result.model_id}  {label}", single_line=True))
    if result.layout == "unmigrated":
        # Printed whenever the layout is wrong, including when a drift
        # state won the verdict — losing the precedence contest must
        # not hide the layout problem (spec 0017 criterion 3).
        # Leg-neutral wording: layout_state convicts either on a foreign
        # artifact source *or* on the record's own hub_id, and "holds
        # files from X" states the reverse of the truth in the second
        # case.
        typer.echo(
            clean_text(
                f"  {_LAYOUT_DETAIL}"
                + (f": {result.offending_repo}" if result.offending_repo else "")
                + f"; {_MIGRATE_REMEDY}",
                single_line=True,
            )
        )
    for problem in result.problems:
        typer.echo(clean_text(f"  {problem.path}: {problem.detail}", single_line=True))
    for rel_path in result.unhashed:
        typer.echo(clean_text(f"  unhashed (no recorded sha256): {rel_path}", single_line=True))
    for rel_path in result.unrecorded:
        typer.echo(
            clean_text(f"  unrecorded (on disk, not in record): {rel_path}", single_line=True)
        )
    if result.manifest_error is not None:
        # A warning, not drift: the payload verdict above stands; only
        # the sidecar refresh failed (e.g. a read-only-mounted archive).
        typer.echo(
            clean_text(f"  manifest not refreshed: {result.manifest_error}", single_line=True),
            err=True,
        )


def _summary_line(report: VerifyReport) -> str:
    """Archive-wide totals, e.g. ``2 models: 1 valid, 1 incomplete``."""
    counts = Counter(result.state for result in report.models)
    order = ("valid", "complete", "incomplete", "invalid", "no-record", "record-unreadable")
    parts = [
        f"{counts[state]} {_STATE_LABELS.get(state, state)}" for state in order if counts[state]
    ]
    # Counted separately because layout is a separate axis: a model is
    # both "valid" and "unmigrated", so it belongs in both tallies.
    unmigrated = sum(1 for result in report.models if result.layout == "unmigrated")
    if unmigrated:
        parts.append(f"{unmigrated} unmigrated")
    total = len(report.models)
    noun = "model" if total == 1 else "models"
    return f"{total} {noun}: {', '.join(parts)}"


def _staging_line(left: StagingLeftover) -> str:
    """One ``--staging`` report line for a leftover."""
    noun = "file" if left.file_count == 1 else "files"
    return f"{left.model_id}  {human_size(left.total_bytes)}, {left.file_count} partial {noun}"
