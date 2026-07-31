"""The views command (spec 0002): runtime views over the archive.

Read-only over the archive in every mode. Exit codes follow the house
contract: 0 built/instructions, 1 archive problem or nothing eligible,
2 user-input domain (unknown tool, bad dest), 130 interrupted.
"""

import os
from pathlib import Path
from typing import Annotated

import typer

from llm_preserver.archive import ArchiveError
from llm_preserver.cli.app import ArchivePath, app, fail
from llm_preserver.render import clean_text
from llm_preserver.views import (
    SUPPORTED_TOOLS,
    ViewBuildResult,
    ViewEntry,
    ViewSourceScan,
    build_view,
)
from llm_preserver.views.types import ViewError

_SEED_WARNING = (
    "warning: this view works but is not an officially supported "
    "Ollama setup — an Ollama upgrade may break it, in which case "
    "re-run --seed-store to regenerate; the archive itself is never "
    "written and never at risk"
)


def _refuse(message: str) -> typer.Exit:
    """Print a user-input refusal to stderr and return an exit-2."""
    typer.echo(f"error: {clean_text(message, single_line=True)}", err=True)
    return typer.Exit(code=2)


def _echo_breakdown(scan: ViewSourceScan, entries: list[ViewEntry]) -> None:
    """Usable and not-usable models, clearly separated.

    Live-use adjudication (Brian, 2026-07-31): the flat skip list
    buried the two usable models under ten repeated snapshot reasons,
    and companion-file skips made usable models look broken. Usable
    models lead (with their run-ready minted names after seeding),
    not-usable models get one deduplicated reason each, and companion
    skips stay out of the display entirely.
    """
    total = len(scan.models)
    usable = [model for model in scan.models if model.eligible]
    noun = "model" if total == 1 else "models"
    typer.echo(
        f"scanned {total} {noun}: {len(usable)} eligible (usable with "
        f"ollama), {total - len(usable)} skipped"
    )
    names_by_model: dict[str, list[str]] = {}
    for entry in entries:
        names_by_model.setdefault(entry.model_id, []).append(entry.name)
    if usable:
        typer.echo("usable:")
    for model in usable:
        names = names_by_model.get(model.model_id)
        if names:
            line = f"  {model.model_id} → {', '.join(names)}"
        else:
            count = len(model.eligible)
            file_noun = "file" if count == 1 else "files"
            line = f"  {model.model_id}  ({count} GGUF {file_noun})"
        typer.echo(clean_text(line, single_line=True))
        for skip in model.skips:
            if skip.kind == "problem":
                detail = f"    skipped {skip.path}: {skip.reason}"
                typer.echo(clean_text(detail, single_line=True))
    not_usable = [model for model in scan.models if not model.eligible]
    if not_usable:
        typer.echo("not usable:")
    for model in not_usable:
        reasons: list[str] = []
        for skip in model.skips:
            if skip.kind != "companion" and skip.reason not in reasons:
                reasons.append(skip.reason)
        summary = "; ".join(reasons) or "no linkable files"
        typer.echo(clean_text(f"  {model.model_id}: {summary}", single_line=True))


def _echo_multiline(text: str) -> None:
    """Echo generated multi-line text, each line control-scrubbed."""
    for line in text.rstrip("\n").split("\n"):
        typer.echo(clean_text(line, single_line=True))


@app.command()
def views(
    path: ArchivePath,
    dest: Annotated[
        Path | None,
        typer.Option(
            help="View directory to create or refresh (never inside the "
            "archive). Falls back to $LLM_PRESERVER_VIEWS/<tool>."
        ),
    ] = None,
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
    if dest is None:
        # Mirrors the archive path's env fallback: a views *root*, one
        # subdirectory per tool, so later adapters never collide.
        views_root = os.environ.get("LLM_PRESERVER_VIEWS")
        if not views_root:
            raise typer.BadParameter(
                "no --dest given and $LLM_PRESERVER_VIEWS is not set", param_hint="--dest"
            )
        dest = Path(views_root) / tool
    try:
        result: ViewBuildResult = build_view(path, tool=tool, dest=dest, seed=seed_store)
    except ArchiveError as exc:
        raise fail(str(exc)) from exc
    except ViewError as exc:
        raise _refuse(str(exc)) from exc
    except KeyboardInterrupt:
        typer.echo("interrupted — view may be partial; re-run to refresh it", err=True)
        raise typer.Exit(code=130) from None
    _echo_breakdown(result.scan, result.entries)
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
