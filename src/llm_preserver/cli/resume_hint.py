"""Compose the copy-paste resume command for interactively shaped pulls.

Spec 0007: when a pull's shape was assembled interactively — the
discover handoff or pull's interactive file listing — the exact direct
``pull`` command exists nowhere the user can retrieve it (shell history
holds only the ``discover`` invocation). This module composes that
command as one shell-safe line for ``run_pull`` to print before the
first byte transfers and again when Ctrl-C interrupts the transfer.
Pull is idempotent over already-archived files (the skip matrix in
``pull_plan``), so the printed command is a true continue, not a
re-download.
"""

import shlex
from collections.abc import Sequence
from pathlib import Path

from llm_preserver.hub_discovery import looks_like_repo_id
from llm_preserver.render import clean_text

RESUME_HINT_LEAD_IN = "to continue this pull later"


def compose_resume_hint(
    repo_id: str,
    archive_path: Path,
    *,
    include: Sequence[str] = (),
    select_all: bool = False,
    model: str | None = None,
    roles: Sequence[str] = (),
    refresh_docs: bool = False,
    hf_logging: bool = False,
) -> str | None:
    """Compose the one-line direct ``pull`` command that resumes this pull.

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
        model: The human-confirmed canonical model directory, or None
            when no grouping decision was confirmed (plan mode) — a
            hint must never bake in a directory nobody approved.
        roles: Roles assigned at pull time.
        refresh_docs: Whether --refresh-docs was in effect.
        hf_logging: Whether --hf-logging was in effect; it rides along
            because the stalled-transfer scenario the hint serves is
            the one the flag exists for (spec 0008). ``--verbose``
            deliberately does not — the hint replays the pull's shape
            and this one diagnostic flag, nothing else.

    Returns:
        The full hint line, lead-in included — or None for a repo id
        that fails validation (no hint beats a booby-trapped one).
    """
    if not looks_like_repo_id(repo_id) or "/" not in repo_id:
        return None
    parts = ["llm-preserver", "pull", repo_id, str(archive_path.resolve())]
    if select_all:
        parts.append("--whole-repo")
    for pattern in include:
        parts.extend(["--include", pattern])
    if model is not None:
        parts.extend(["--model", model])
    for role in roles:
        parts.extend(["--role", role])
    if refresh_docs:
        parts.append("--refresh-docs")
    if hf_logging:
        parts.append("--hf-logging")
    command = " ".join(shlex.quote(clean_text(part, single_line=True)) for part in parts)
    return f"{RESUME_HINT_LEAD_IN}: {command}"
