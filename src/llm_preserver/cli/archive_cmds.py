"""Archive-reading commands: init, status, show.

These commands never touch the network; they read (or create) the
archive on local disk only.
"""

import os
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from llm_preserver.archive import (
    ArchiveError,
    ModelSummary,
    init_archive,
    inventory,
    require_archive,
)
from llm_preserver.cli.app import ArchivePath, app, fail
from llm_preserver.lineage import group_by_lineage
from llm_preserver.pull_preflight import human_size
from llm_preserver.records import (
    ID_COMPONENT_RE,
    RECORD_FILENAME,
    RECORD_SCHEMA_VERSION,
    ModelRecord,
    load_record,
    peek_record_schema_version,
)
from llm_preserver.render import clean_text, render_model_record


@app.command()
def init(path: ArchivePath) -> None:
    """Initialize an archive skeleton at PATH (idempotent)."""
    try:
        init_archive(path)
    except ArchiveError as exc:
        raise fail(str(exc)) from exc
    typer.echo(f"archive ready at {path}")
    if not os.environ.get("LLM_PRESERVER_ARCHIVE"):
        # POSIX `export` syntax on purpose — no shell detection; fish
        # etc. users translate. Absolute path so the hint survives cd.
        typer.echo(
            "hint: set LLM_PRESERVER_ARCHIVE to omit the archive path on"
            " future commands, e.g.\n"
            f'  export LLM_PRESERVER_ARCHIVE="{path.resolve()}"'
        )


def _roleless_cell(summary: ModelSummary) -> str:
    """Roles cell for a model with none: a visible "(no role)" bucket.

    Only for models with a readable record (spec 0003) — a missing or
    unreadable record means the roles are unknown, not absent.
    """
    return "-" if summary.missing_record or summary.record_error else "(no role)"


def _completeness(summary: ModelSummary) -> str:
    """Render a summary's completeness flags as a short cell."""
    problems = []
    if summary.missing_record:
        problems.append("no record")
    if summary.record_error:
        problems.append("record unreadable")
    if summary.newer_record_schema:
        problems.append("newer record schema")
    if summary.missing_license:
        problems.append("no license")
    if summary.missing_checksums:
        problems.append("missing checksums")
    return ", ".join(problems) if problems else "ok"


@app.command()
def status(path: ArchivePath) -> None:
    """Print an inventory table of every model in the archive."""
    try:
        require_archive(path)
        summaries = inventory(path)
    except ArchiveError as exc:
        raise fail(str(exc)) from exc
    if not summaries:
        typer.echo("archive is empty (no models)")
        return
    # Grouped by declared lineage (spec 0017 criterion 7): ADR 0003
    # made every repo its own directory, so the relationship a nested
    # path used to state has to be stated here instead. A base the
    # archive does not hold gets a row of its own — "you have the
    # conversion, not the model" is the answer worth seeing.
    rows: list[tuple[str, str, str, str, str]] = []
    for entry in group_by_lineage(summaries):
        indent = "  " if entry.depth else ""
        label = clean_text(entry.model_id, single_line=True)
        if not entry.archived or entry.summary is None:
            rows.append((f"({label})", "-", "-", "-", "not archived"))
            continue
        summary = entry.summary
        rows.append(
            (
                indent + label,
                ",".join(summary.formats) or "-",
                ",".join(summary.roles) or _roleless_cell(summary),
                human_size(summary.total_size),
                _completeness(summary),
            )
        )
    header = ("model", "formats", "roles", "size", "completeness")
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    for row in [header, *rows]:
        typer.echo("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)))


def _validation_summary(exc: ValidationError) -> str:
    """Describe a ValidationError without echoing untrusted input values."""
    errors = exc.errors(include_input=False, include_url=False)
    if not errors:
        return "validation failed"
    loc = ".".join(str(part) for part in errors[0]["loc"]) or "<root>"
    return f"{len(errors)} validation error(s); first at {loc}: {errors[0]['msg']}"


def _load_model_record(path: Path, model_id: str) -> ModelRecord:
    """Load one model's record for `show`, mapping failures to exits."""
    creator, sep, model = model_id.partition("/")
    if not sep or not ID_COMPONENT_RE.fullmatch(creator) or not ID_COMPONENT_RE.fullmatch(model):
        raise fail(f"model id must look like <creator>/<model>, got {model_id!r}")
    model_dir = path / "models" / creator / model
    if not model_dir.is_dir():
        raise fail(f"no model directory for {model_id} in {path}")
    if not (model_dir / RECORD_FILENAME).is_file():
        raise fail(f"{model_id} has no {RECORD_FILENAME}")
    try:
        return load_record(model_dir)
    except ValidationError as exc:
        claimed = peek_record_schema_version(model_dir)
        hint = (
            f" (record_schema_version {claimed} is newer than this tool's {RECORD_SCHEMA_VERSION})"
            if claimed is not None and claimed > RECORD_SCHEMA_VERSION
            else ""
        )
        raise fail(f"record for {model_id} is invalid{hint}: {_validation_summary(exc)}") from exc
    except (ValueError, OSError) as exc:
        raise fail(f"record for {model_id} is unreadable: {exc}") from exc


@app.command()
def show(model_id: Annotated[str, typer.Argument()], path: ArchivePath) -> None:
    """Print everything archived for one model (<creator>/<model>)."""
    try:
        require_archive(path)
    except ArchiveError as exc:
        raise fail(str(exc)) from exc
    record = _load_model_record(path, model_id)
    if record.record_schema_version > RECORD_SCHEMA_VERSION:
        typer.echo(
            f"warning: record schema {record.record_schema_version} is newer than this"
            f" tool's {RECORD_SCHEMA_VERSION}; fields it added are not shown",
            err=True,
        )
    typer.echo(render_model_record(record, file_header=False))
    _echo_derivatives(path, model_id)


def _echo_derivatives(path: Path, model_id: str) -> None:
    """Name the archived repos that declare ``model_id`` as their base.

    The record states lineage one way (this repo derives from that
    model); this is the other way, which only the archive as a whole
    knows. Together they answer "what forms of this model do I have"
    from one command — the question ADR 0003's flat layout took away
    from ``ls`` (spec 0017 criterion 7).
    """
    try:
        derived = [
            summary.model_id
            for summary in inventory(path)
            if summary.base_model == model_id and summary.model_id != model_id
        ]
    except ArchiveError:
        return
    if not derived:
        return
    typer.echo("Archived repos deriving from this one:")
    for child in sorted(derived):
        typer.echo(f"- {clean_text(child, single_line=True)}")
