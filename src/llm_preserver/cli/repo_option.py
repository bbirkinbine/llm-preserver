"""The ``--repo`` option and its ``--model`` alias (spec 0017).

ADR 0003 changed what a model directory *is*: one source repo, named
``<owner>/<repo>``. The scoping flag follows the vocabulary — it is
``--repo`` — but ``--model`` keeps working so an existing cron line or
script does not break on the rename.

Two separate Typer options rather than one option with two names: click
does not record *which* spelling was used, so a single two-name option
could neither print the deprecation note nor keep ``--help`` free of
the old name.
"""

import typer

ALIAS_NOTE = "note: --model is now --repo on this command; --model is still accepted"

_BOTH_GIVEN = (
    "error [user input]: --repo and --model are two spellings of the same option; "
    "pass one, not both "
    "(--model is the deprecated spelling)"
)


def resolve_repo_alias(repo: str | None, model: str | None) -> str | None:
    """Collapse ``--repo`` and its ``--model`` alias into one value.

    Prints the deprecation note to stderr when the alias is used, so a
    run that already says ``--repo`` stays byte-identical to a scoped
    run from before the rename.

    Args:
        repo: The ``--repo`` value, or None.
        model: The deprecated ``--model`` value, or None.

    Returns:
        The scoping repo id, or None when neither was given.

    Raises:
        typer.Exit: Code 2 when both spellings are given — user input
            the command cannot resolve (spec 0009's exit-2 domain).
    """
    if repo is not None and model is not None:
        typer.echo(_BOTH_GIVEN, err=True)
        raise typer.Exit(code=2)
    if model is not None:
        typer.echo(ALIAS_NOTE, err=True)
        return model
    return repo
