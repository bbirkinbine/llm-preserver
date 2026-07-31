"""The views command (spec 0002): runtime views over the archive.

Read-only over the archive in every mode. Exit codes follow the house
contract: 0 built/instructions, 1 archive problem or nothing eligible,
2 user-input domain (unknown tool, bad dest), 130 interrupted.
"""

from pathlib import Path
from typing import Annotated

import typer

from llm_preserver.archive import ArchiveError
from llm_preserver.cli.app import ArchivePath, app, fail
from llm_preserver.render import clean_text
from llm_preserver.views import SUPPORTED_TOOLS, ViewBuildResult, ViewSourceScan, build_view
from llm_preserver.views.types import ViewError

_SEED_WARNING = (
    "warning: Ollama does not support external model stores — the "
    "seeded view is best-effort and an Ollama upgrade may break it; "
    "the archive itself is never written"
)


def _refuse(message: str) -> typer.Exit:
    """Print a user-input refusal to stderr and return an exit-2."""
    typer.echo(f"error: {clean_text(message, single_line=True)}", err=True)
    return typer.Exit(code=2)


def _echo_breakdown(scan: ViewSourceScan) -> None:
    """The scanned/eligible/skipped totals plus a reason per skip."""
    total = len(scan.models)
    eligible = sum(1 for model in scan.models if model.eligible)
    noun = "model" if total == 1 else "models"
    typer.echo(f"scanned {total} {noun}: {eligible} eligible, {total - eligible} skipped")
    for model in scan.models:
        for skip in model.skips:
            line = f"  {model.model_id}: {skip.path}: {skip.reason}"
            typer.echo(clean_text(line, single_line=True))


def _echo_multiline(text: str) -> None:
    """Echo generated multi-line text, each line control-scrubbed."""
    for line in text.rstrip("\n").split("\n"):
        typer.echo(clean_text(line, single_line=True))


@app.command()
def views(
    path: ArchivePath,
    dest: Annotated[
        Path,
        typer.Option(help="View directory to create or refresh (never inside the archive)."),
    ],
    tool: Annotated[
        str,
        typer.Option(help="Target runtime (phase 1: ollama)."),
    ] = "ollama",
    seed_store: Annotated[
        bool,
        typer.Option(
            "--seed-store",
            help="Seed the external store (blob symlinks + synthesized "
            "manifests) instead of printing instructions only. Best "
            "effort — Ollama does not support external stores.",
        ),
    ] = False,
) -> None:
    """Generate a disposable runtime view pointing into the archive."""
    if tool not in SUPPORTED_TOOLS:
        supported = ", ".join(SUPPORTED_TOOLS)
        raise typer.BadParameter(
            f"unknown tool {tool!r} — phase 1 supports: {supported}", param_hint="--tool"
        )
    try:
        result: ViewBuildResult = build_view(path, tool=tool, dest=dest, seed=seed_store)
    except ArchiveError as exc:
        raise fail(str(exc)) from exc
    except ViewError as exc:
        raise _refuse(str(exc)) from exc
    except KeyboardInterrupt:
        typer.echo("interrupted — view may be partial; re-run to refresh it", err=True)
        raise typer.Exit(code=130) from None
    _echo_breakdown(result.scan)
    if not any(model.eligible for model in result.scan.models):
        typer.echo(
            "error: no models eligible for an ollama view — it links "
            "GGUF files with recorded sha256s; nothing was written",
            err=True,
        )
        raise typer.Exit(code=1)
    if seed_store:
        typer.echo(_SEED_WARNING, err=True)
        blob_count = len({entry.blob_path for entry in result.entries})
        noun = "link" if blob_count == 1 else "links"
        registered = len(result.entries)
        reg_noun = "model" if registered == 1 else "models"
        typer.echo(
            clean_text(
                f"seeded {blob_count} blob {noun}, registered {registered} {reg_noun} at {dest}",
                single_line=True,
            )
        )
    _echo_multiline(result.instructions)
