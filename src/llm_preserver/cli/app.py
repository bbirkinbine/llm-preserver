"""Shared CLI plumbing: the Typer app, archive-path argument, fail helper.

All commands take the archive path explicitly or via the
``LLM_PRESERVER_ARCHIVE`` environment variable (spec 0003) and operate
only inside it.
"""

from pathlib import Path
from typing import Annotated

import typer

from llm_preserver.render import clean_text

app = typer.Typer(
    name="llm-preserver",
    help="Archive local LLMs for long-term offline use.",
    no_args_is_help=True,
)

ArchivePath = Annotated[
    Path,
    typer.Argument(
        envvar="LLM_PRESERVER_ARCHIVE",
        help="Archive root directory (falls back to $LLM_PRESERVER_ARCHIVE).",
    ),
]


def fail(message: str) -> typer.Exit:
    """Print an error to stderr and return a nonzero Exit to raise."""
    typer.echo(f"error: {clean_text(message, single_line=True)}", err=True)
    return typer.Exit(code=1)
