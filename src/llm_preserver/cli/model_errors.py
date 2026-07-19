"""Shared CLI handling of ``<creator>/<model>`` arguments.

Extracted from the verify command (spec 0009) when ``remove``
(spec 0010) needed the same id validation and the same
unknown-model self-correction: exit 2 is the user-input domain, and
the archived-ids listing lets a typo self-correct without a separate
``status`` round-trip.
"""

from collections.abc import Sequence
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


def _reject_unknown(
    path_arg: Path, model: str, ids: Sequence[str], *, subject: str, header: str
) -> typer.Exit:
    """Exit 2 for an unknown id, listing the ids that do exist.

    The listing lets a typo self-correct without a separate lookup
    round-trip (spec 0009). ``subject`` names what was not found and
    ``header`` labels the listed alternatives, so ``models/`` and
    ``.staging/`` callers share one shape over different namespaces.
    """
    typer.echo(
        clean_text(f"error: no {subject} for {model} in {path_arg}", single_line=True),
        err=True,
    )
    if ids:
        typer.echo(header, err=True)
        for model_id in ids:
            typer.echo(clean_text(f"  {model_id}", single_line=True), err=True)
    return typer.Exit(code=2)


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
    return _reject_unknown(
        path_arg,
        model,
        [summary.model_id for summary in inventory(path_arg)],
        subject="model directory",
        header="archived models:",
    )


def reject_unknown_staging_model(
    path_arg: Path, model: str, staging_ids: Sequence[str]
) -> typer.Exit:
    """Error for a ``--staging`` id matching no leftover: exit 2.

    Under ``verify --staging`` the id namespace is the staging tree, not
    ``models/`` (a first-ever interrupted pull has no model directory at
    all — spec 0012). Lists the leftover ids present so the same
    self-correction works over the staging namespace.

    Args:
        path_arg: The archive root, for the message.
        model: The id that matched no staging leftover.
        staging_ids: The ``<creator>/<model>`` ids present in
            ``.staging/``.

    Returns:
        The ``typer.Exit`` for the caller to raise.
    """
    return _reject_unknown(
        path_arg,
        model,
        list(staging_ids),
        subject="staging leftovers",
        header="abandoned downloads in .staging/:",
    )
