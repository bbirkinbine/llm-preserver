"""Compose copy-paste ``pull`` commands for a human left without one.

Two composers, one quoting path.

Spec 0007: when a pull's shape was assembled interactively — the
discover handoff or pull's interactive file listing — the exact direct
``pull`` command exists nowhere the user can retrieve it (shell history
holds only the ``discover`` invocation). ``compose_resume_hint``
composes that command as one shell-safe line for ``run_pull`` to print
before the first byte transfers and again when Ctrl-C interrupts the
transfer. Pull is idempotent over already-archived files (the skip
matrix in ``pull_plan``), so the printed command is a true continue, not
a re-download.

Spec 0020: a planning stop on a changed *documentation* file names
``--refresh-docs`` as the way out and fires before any transfer starts,
so the 0007 hint has not been composed yet and the human is told a flag
with nothing to append it to. ``compose_doc_refresh_hint`` composes the
same command with that flag pinned on.

Both go through ``_compose_pull_command``, which owns the validation and
the scrub-then-quote — load-bearing security behavior (spec 0007's
review round), and not something to reimplement per caller.
"""

import shlex
from collections.abc import Sequence
from pathlib import Path

from llm_preserver.hub_discovery import looks_like_repo_id
from llm_preserver.render import clean_text

RESUME_HINT_LEAD_IN = "to continue this pull later"
DOC_REFRESH_LEAD_IN = "to replace every changed documentation file and finish this pull"


def _compose_pull_command(
    repo_id: str,
    archive_path: Path,
    *,
    include: Sequence[str] = (),
    select_all: bool = False,
    roles: Sequence[str] = (),
    base_model: str | None = None,
    refresh_docs: bool = False,
    hf_logging: bool = False,
) -> str | None:
    """Compose the direct ``pull`` command replaying a pull's shape.

    The archive path prints resolved to absolute: the pasted command
    must work from any working directory and in a shell without
    ``$LLM_PRESERVER_ARCHIVE`` set. Every part is control-character
    scrubbed *then* shell-quoted, so the printed line is byte-faithful
    to what will parse and a pattern like ``*Q4_K_M*`` cannot glob on
    paste. ``--yes`` never rides — the re-run asks its own size
    confirmation, which usefully shows how much is left to download.

    Args:
        repo_id: Exact hub repo id the pull targets. Hub-supplied via
            discover, so it is validated here: a value not shaped like
            a repo id (e.g. leading ``-``) must never become a future
            argv token, where shell quoting cannot protect it.
        archive_path: Archive root as the CLI received it.
        include: fnmatch patterns; each rides as a repeated --include.
        select_all: Whole-repo snapshot mode (--whole-repo).
        roles: Roles assigned at pull time.
        base_model: Curator-asserted lineage, replayed like a role.
        refresh_docs: Whether --refresh-docs rides along.
        hf_logging: Whether --hf-logging rides along.

    Returns:
        The command line alone, no lead-in — or None for a repo id that
        fails validation (no command beats a booby-trapped one).
    """
    if not looks_like_repo_id(repo_id) or "/" not in repo_id:
        return None
    parts = ["llm-preserver", "pull", repo_id, str(archive_path.resolve())]
    if select_all:
        parts.append("--whole-repo")
    for pattern in include:
        parts.extend(["--include", pattern])
    for role in roles:
        parts.extend(["--role", role])
    if base_model is not None:
        # Curator judgment, exactly like --role: a resumed pull that
        # dropped it would silently lose the lineage assertion.
        parts.extend(["--base-model", base_model])
    if refresh_docs:
        parts.append("--refresh-docs")
    if hf_logging:
        parts.append("--hf-logging")
    return " ".join(shlex.quote(clean_text(part, single_line=True)) for part in parts)


def compose_resume_hint(
    repo_id: str,
    archive_path: Path,
    *,
    include: Sequence[str] = (),
    select_all: bool = False,
    roles: Sequence[str] = (),
    base_model: str | None = None,
    refresh_docs: bool = False,
    hf_logging: bool = False,
) -> str | None:
    """Compose the one-line direct ``pull`` command that resumes this pull.

    Args:
        repo_id: Exact hub repo id the pull targets.
        archive_path: Archive root as the CLI received it.
        include: fnmatch patterns; each rides as a repeated --include.
        select_all: Whole-repo snapshot mode (--whole-repo).
        roles: Roles assigned at pull time.
        base_model: Curator-asserted lineage, replayed like a role.
        refresh_docs: Whether --refresh-docs was in effect.
        hf_logging: Whether --hf-logging was in effect; it rides along
            because the stalled-transfer scenario the hint serves is
            the one the flag exists for (spec 0008). ``--verbose``
            deliberately does not — the hint replays the pull's shape
            and this one diagnostic flag, nothing else.

    Returns:
        The full hint line, lead-in included — or None for a repo id
        that fails validation.
    """
    command = _compose_pull_command(
        repo_id,
        archive_path,
        include=include,
        select_all=select_all,
        roles=roles,
        base_model=base_model,
        refresh_docs=refresh_docs,
        hf_logging=hf_logging,
    )
    if command is None:
        return None
    return f"{RESUME_HINT_LEAD_IN}: {command}"


def compose_doc_refresh_hint(
    repo_id: str,
    archive_path: Path,
    *,
    include: Sequence[str] = (),
    select_all: bool = False,
    roles: Sequence[str] = (),
    base_model: str | None = None,
    hf_logging: bool = False,
) -> str | None:
    """Compose the ``pull ... --refresh-docs`` command that clears a doc stop.

    Spec 0020. There is deliberately no ``refresh_docs`` parameter: this
    composer exists to pin the flag on, and a caller able to pass False
    could compose a command that does not resolve the stop it is printed
    under. The flag can never appear twice either — a doc stop implies
    ``--refresh-docs`` was absent, since the same ``is_doc_file``
    predicate that raises the stop is the one that would have suppressed
    it.

    Args:
        repo_id: Exact hub repo id the refused pull targeted.
        archive_path: Archive root as the CLI received it.
        include: fnmatch patterns the pull was shaped with.
        select_all: Whole-repo snapshot mode (--whole-repo).
        roles: Roles the refused pull would have assigned.
        base_model: Curator-asserted lineage to replay.
        hf_logging: Whether --hf-logging was in effect.

    Returns:
        The full recovery line, lead-in included — or None for a repo id
        that fails validation.
    """
    command = _compose_pull_command(
        repo_id,
        archive_path,
        include=include,
        select_all=select_all,
        roles=roles,
        base_model=base_model,
        refresh_docs=True,
        hf_logging=hf_logging,
    )
    if command is None:
        return None
    return f"{DOC_REFRESH_LEAD_IN}: {command}"
