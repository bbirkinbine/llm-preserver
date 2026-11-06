"""Recovery lines printed beside a pull's fault-domain error.

Split out of ``flow`` (spec 0026 pass 1) so the pull flow stays under
the file-size cap as it grows: these are the lines that turn a stop into
a next step, and each is chosen by the exception's *type*, never by its
message text.
"""

from pathlib import Path

import typer

from llm_preserver.cli.resume_hint import compose_doc_refresh_hint
from llm_preserver.hub import PullDocRefreshError, PullError, PullInvalidIdError
from llm_preserver.ollama_store import ollama_shape_hint


def echo_recovery_hints(
    exc: PullError,
    repo_id: str,
    path: Path,
    *,
    include: list[str],
    select_all: bool,
    roles: tuple[str, ...],
    base_model: str | None,
    hf_logging: bool,
    revision: str | None,
    progress: bool = False,
) -> None:
    """Print the recovery command a pull error has one for, on stderr.

    Args:
        exc: The pull error about to be mapped to its exit.
        repo_id: The repo the pull targeted.
        path: The archive root.
        include: The patterns this run used (possibly picked
            interactively, which is why the command must be composed).
        select_all: Whether the run was ``--whole-repo``.
        roles: Roles the run was assigning.
        base_model: The run's ``--base-model`` assertion.
        hf_logging: Whether ``--hf-logging`` was in effect.
        revision: The commit a ``--revision`` resolved to, or None.
        progress: Whether ``--progress`` was in effect (spec 0026).
    """
    if isinstance(exc, PullDocRefreshError):
        # Spec 0020: this stop fires inside prepare_pull — before the
        # on_transfer_start seam that composes the 0007 hint — so the
        # human is told to re-run with a flag and has no command to
        # append it to. Compose it here, from the shape this run already
        # holds.
        recovery = compose_doc_refresh_hint(
            repo_id,
            path,
            include=include,
            select_all=select_all,
            roles=roles,
            base_model=base_model,
            hf_logging=hf_logging,
            progress=progress,
            revision=revision,
        )
        if recovery is not None:
            typer.echo(recovery, err=True)
    if isinstance(exc, PullInvalidIdError):
        # Spec 0013: an Ollama-shaped id gets the deterministic recovery
        # command appended to the 0011 error. Detection lives in the
        # error path only; the id is never rewritten and the exit code is
        # unchanged.
        shape_hint = ollama_shape_hint(repo_id)
        if shape_hint is not None:
            typer.echo(shape_hint, err=True)
