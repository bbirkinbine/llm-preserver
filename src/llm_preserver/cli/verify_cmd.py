"""The verify command (spec 0009): audit disk against records.

Read-only over payloads and records; the one write is the regenerable
``manifest-sha256.txt`` sidecar. Never touches the network. Exit codes
are the cron contract: 0 clean, 1 archive/usage, 2 unknown --model,
5 drift, 130 interrupted.
"""

import sys
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, TextIO

import typer

from llm_preserver.archive import ArchiveError, inventory
from llm_preserver.cli.app import ArchivePath, app, fail
from llm_preserver.pull_preflight import human_size
from llm_preserver.records import ID_COMPONENT_RE
from llm_preserver.render import clean_text
from llm_preserver.verify import (
    ModelVerifyResult,
    ProgressEvents,
    VerifyReport,
    verify_archive,
)

_STATE_LABELS = {"no-record": "no record", "record-unreadable": "record unreadable"}

_RENDER_INTERVAL_SECONDS = 0.5


class ProgressRenderer:
    """Live status on stderr while verify walks and hashes.

    Renders only when the stream is a terminal: a human staring at a
    multi-gigabyte hash gets a ``checking`` line per model and an
    in-place byte counter per file (adjudicated 2026-07-13 — silence
    during a long hash reads as a hang). Cron and piped runs see no
    progress output at all, so the report and exit-code contract stay
    byte-identical to a progress-free run.
    """

    def __init__(self, stream: TextIO, now: Callable[[], float] = time.monotonic) -> None:
        self._stream = stream
        self._enabled = stream.isatty()
        self._now = now
        self._file_label: str | None = None
        self._file_total: int | None = None
        self._file_done = 0
        self._last_render = 0.0
        self._last_width = 0

    def on_model_start(self, model_id: str, file_count: int, recorded_bytes: int) -> None:
        """One ``checking …`` line per model, before its files run."""
        if not self._enabled:
            return
        self.finish_line()
        noun = "file" if file_count == 1 else "files"
        line = f"checking {model_id} ({file_count} {noun}, {human_size(recorded_bytes)} recorded)"
        self._stream.write(clean_text(line, single_line=True) + "\n")
        self._stream.flush()

    def on_file_start(self, rel_path: str, size: int | None) -> None:
        """Arm the in-place byte counter for the file about to hash."""
        if not self._enabled:
            return
        self._file_label = clean_text(rel_path, single_line=True)
        self._file_total = size
        self._file_done = 0
        self._render_file_line()

    def on_file_bytes(self, count: int) -> None:
        """Advance the byte counter; redraw at most twice a second."""
        if not self._enabled or self._file_label is None:
            return
        self._file_done += count
        if self._now() - self._last_render >= _RENDER_INTERVAL_SECONDS:
            self._render_file_line()

    def finish_line(self) -> None:
        """Terminate any in-place counter line before normal output."""
        if not self._enabled:
            return
        if self._last_width:
            self._stream.write("\n")
            self._stream.flush()
        self._file_label = None
        self._last_width = 0

    def _render_file_line(self) -> None:
        total = f" / {human_size(self._file_total)}" if self._file_total is not None else ""
        text = f"  hashing {self._file_label}: {human_size(self._file_done)}{total}"
        # Carriage return + pad to the previous width: same-line update
        # with no ANSI beyond what every terminal handles.
        self._stream.write("\r" + text.ljust(self._last_width))
        self._stream.flush()
        self._last_width = len(text)
        self._last_render = self._now()


def _echo_result(result: ModelVerifyResult) -> None:
    """Print one model's result line plus its per-file detail lines.

    Called as each model completes — this stream is the progress
    display for long runs as well as the report body.
    """
    label = _STATE_LABELS.get(result.state, result.state)
    typer.echo(clean_text(f"{result.model_id}  {label}", single_line=True))
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
    total = len(report.models)
    noun = "model" if total == 1 else "models"
    return f"{total} {noun}: {', '.join(parts)}"


def _reject_unknown_model(path_arg: Path, model: str) -> typer.Exit:
    """Error for a --model that matches no model directory: exit 2.

    Prints the archive's model ids so a typo self-corrects without a
    separate ``status`` round-trip (spec 0009).
    """
    typer.echo(
        clean_text(f"error: no model directory for {model} in {path_arg}", single_line=True),
        err=True,
    )
    model_ids = [summary.model_id for summary in inventory(path_arg)]
    if model_ids:
        typer.echo("archived models:", err=True)
        for model_id in model_ids:
            typer.echo(clean_text(f"  {model_id}", single_line=True), err=True)
    return typer.Exit(code=2)


@app.command()
def verify(
    path: ArchivePath,
    model: Annotated[
        str | None,
        typer.Option(help="Audit only this <creator>/<model> instead of the whole archive."),
    ] = None,
    quick: Annotated[
        bool,
        typer.Option(
            "--quick",
            help="Existence and size checks only — no hashing, no manifest refresh.",
        ),
    ] = False,
) -> None:
    """Audit the archive against its records (complete vs valid)."""
    renderer = ProgressRenderer(sys.stderr)

    def emit(result: ModelVerifyResult) -> None:
        renderer.finish_line()
        _echo_result(result)

    try:
        if model is not None:
            creator, sep, name = model.partition("/")
            if (
                not sep
                or not ID_COMPONENT_RE.fullmatch(creator)
                or not ID_COMPONENT_RE.fullmatch(name)
            ):
                raise fail(f"model id must look like <creator>/<model>, got {model!r}")
        if quick:
            typer.echo("quick check: hashes were not checked (existence and size only)")
        report = verify_archive(
            path,
            model=model,
            quick=quick,
            on_result=emit,
            events=ProgressEvents(
                on_model_start=renderer.on_model_start,
                on_file_start=renderer.on_file_start,
                on_file_bytes=renderer.on_file_bytes,
            ),
        )
    except KeyboardInterrupt:
        renderer.finish_line()
        typer.echo("interrupted — audit incomplete", err=True)
        raise typer.Exit(code=130) from None
    except ArchiveError as exc:
        raise fail(str(exc)) from exc
    if model is not None and not report.models:
        raise _reject_unknown_model(path, model)
    if not report.models:
        typer.echo("archive is empty (no models)")
        return
    typer.echo(_summary_line(report))
    if report.drifted:
        raise typer.Exit(code=5)
