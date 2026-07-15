"""Shared CLI handling of ``<creator>/<model>`` arguments.

Extracted from the verify command (spec 0009) when ``remove``
(spec 0010) needed the same id validation and the same
unknown-model self-correction: exit 2 is the user-input domain, and
the archived-ids listing lets a typo self-correct without a separate
``status`` round-trip.
"""

from pathlib import Path

import typer

from llm_preserver.archive import inventory
from llm_preserver.cli.app import fail
from llm_preserver.records import ID_COMPONENT_RE
from llm_preserver.render import clean_text


def split_model_id(model: str) -> tuple[str, str]:
    """Validate a ``<creator>/<model>`` argument before any path use.

    Args:
        model: The user-supplied model id.

    Returns:
        The ``(creator, name)`` pair.

    Raises:
        typer.Exit: Exit 1 (via ``fail``) when the id does not match
            the strict component pattern — nothing outside
            ``models/`` must ever be addressable from user input.
    """
    creator, sep, name = model.partition("/")
    if not sep or not ID_COMPONENT_RE.fullmatch(creator) or not ID_COMPONENT_RE.fullmatch(name):
        raise fail(f"model id must look like <creator>/<model>, got {model!r}")
    return creator, name


def reject_unknown_model(path_arg: Path, model: str) -> typer.Exit:
    """Error for a model id that matches no model directory: exit 2.

    Prints the archive's model ids so a typo self-corrects without a
    separate ``status`` round-trip (spec 0009).

    Args:
        path_arg: The archive root, for the message.
        model: The id that matched nothing.

    Returns:
        The ``typer.Exit`` for the caller to raise.
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
