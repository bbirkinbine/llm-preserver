"""Typer CLI: init, status, show.

All commands take the archive path explicitly (no global config, spec
0001) and operate only inside it.
"""

import re
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
from llm_preserver.records import (
    RECORD_FILENAME,
    RECORD_SCHEMA_VERSION,
    ModelRecord,
    load_record,
    peek_record_schema_version,
)
from llm_preserver.render import clean_text, render_model_record

_ID_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
"""One segment of a ``<creator>/<model>`` id.

Hub namespaces and repo names match this; anything else (``..``,
absolute paths, extra slashes) could address files outside the
archive's ``models/`` tree and is rejected before path construction.
"""

app = typer.Typer(
    name="llm-preserver",
    help="Archive local LLMs for long-term offline use.",
    no_args_is_help=True,
)

ArchivePath = Annotated[Path, typer.Argument(help="Archive root directory.")]


def _fail(message: str) -> typer.Exit:
    """Print an error to stderr and return a nonzero Exit to raise."""
    typer.echo(f"error: {clean_text(message, single_line=True)}", err=True)
    return typer.Exit(code=1)


@app.command()
def init(path: ArchivePath) -> None:
    """Initialize an archive skeleton at PATH (idempotent)."""
    try:
        init_archive(path)
    except ArchiveError as exc:
        raise _fail(str(exc)) from exc
    typer.echo(f"archive ready at {path}")


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
        raise _fail(str(exc)) from exc
    if not summaries:
        typer.echo("archive is empty (no models)")
        return
    rows = [
        (
            clean_text(summary.model_id, single_line=True),
            ",".join(summary.formats) or "-",
            ",".join(summary.roles) or "-",
            str(summary.total_size),
            _completeness(summary),
        )
        for summary in summaries
    ]
    header = ("model", "formats", "roles", "size (bytes)", "completeness")
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
    if not sep or not _ID_COMPONENT.fullmatch(creator) or not _ID_COMPONENT.fullmatch(model):
        raise _fail(f"model id must look like <creator>/<model>, got {model_id!r}")
    model_dir = path / "models" / creator / model
    if not model_dir.is_dir():
        raise _fail(f"no model directory for {model_id} in {path}")
    if not (model_dir / RECORD_FILENAME).is_file():
        raise _fail(f"{model_id} has no {RECORD_FILENAME}")
    try:
        return load_record(model_dir)
    except ValidationError as exc:
        claimed = peek_record_schema_version(model_dir)
        hint = (
            f" (record_schema_version {claimed} is newer than this tool's {RECORD_SCHEMA_VERSION})"
            if claimed is not None and claimed > RECORD_SCHEMA_VERSION
            else ""
        )
        raise _fail(f"record for {model_id} is invalid{hint}: {_validation_summary(exc)}") from exc
    except (ValueError, OSError) as exc:
        raise _fail(f"record for {model_id} is unreadable: {exc}") from exc


@app.command()
def show(path: ArchivePath, model_id: Annotated[str, typer.Argument()]) -> None:
    """Print everything archived for one model (<creator>/<model>)."""
    try:
        require_archive(path)
    except ArchiveError as exc:
        raise _fail(str(exc)) from exc
    record = _load_model_record(path, model_id)
    if record.record_schema_version > RECORD_SCHEMA_VERSION:
        typer.echo(
            f"warning: record schema {record.record_schema_version} is newer than this"
            f" tool's {RECORD_SCHEMA_VERSION}; fields it added are not shown",
            err=True,
        )
    typer.echo(render_model_record(record, file_header=False))
