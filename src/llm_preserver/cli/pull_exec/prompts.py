"""Interactive prompts the pull flow asks: confirmations, file picking.

Prompt classification keys on the strings ``pull_model`` composes —
the tool owns both sides of that seam.
"""

import fnmatch
from pathlib import PurePosixPath

import typer

from llm_preserver.hub import PullUserError, RepoInfo
from llm_preserver.pull_advisory import COMPANION_RULES
from llm_preserver.pull_preflight import human_size
from llm_preserver.render import clean_text


def confirm_or_stop(prompt: str, assume_yes: bool) -> bool:
    """Confirm interactively; deterministic stop when stdin cannot answer.

    ``--yes`` auto-accepts the *size* confirmation only — grouping is an
    identity decision that needs an explicit ``--model`` value, never a
    blanket yes. When the prompt cannot be answered (non-interactive
    stdin, exhausted piped input), click raises ``Abort``; that becomes
    a ``PullUserError`` (exit 2) naming the bypass, so scripted pulls
    never die with an undocumented exit 1 (spec 0004 adjudications).
    Prompt classification keys on the strings ``pull_model`` composes —
    the tool owns both sides of this seam.
    """
    cleaned = clean_text(prompt, single_line=True)
    is_size_confirm = cleaned.startswith("pull ")
    if assume_yes and is_size_confirm:
        return True
    try:
        return bool(typer.confirm(cleaned))
    # typer vendors click, so catch its own Abort, not the click
    # package's (they are different classes).
    except typer.Abort:
        if is_size_confirm:
            hint = "re-run with --yes to accept the size confirmation"
        elif "every weight" in cleaned:
            hint = "narrow --include, or run interactively"
        else:
            hint = "pass --model <creator>/<model> to choose the canonical model directory"
        raise PullUserError(f"confirmation needed but stdin is not interactive: {hint}") from None


def _kind_note(path: str) -> str:
    """Annotate a recognized companion kind (the advisory rules table).

    The same curated data the advisories use, shown where the human
    is actually reading filenames (live-use ask, 2026-07-13: "what is
    imatrix again?").
    """
    name = PurePosixPath(path).name
    for pattern, kind in COMPANION_RULES:
        if fnmatch.fnmatchcase(name, pattern):
            return f"  — {kind}"
    return ""


def prompt_for_selection(info: RepoInfo, repo_id: str) -> list[str]:
    """List the repo's files with sizes and prompt for include patterns.

    Takes the already-fetched metadata — one metadata call per pull
    (spec 0003), shared with ``pull_model`` via its ``repo_info`` seam.
    """
    # repo_id can arrive from hub metadata via discover — same trust
    # class as the file paths below.
    typer.echo(f"files in {clean_text(repo_id, single_line=True)}:")
    for repo_file in info.files:
        # Human sizes, matching the plan report: the listing is where a
        # quant gets weighed against VRAM (live-use 2026-07-12 — a raw
        # 19851335840 carries no fit signal).
        size = "?" if repo_file.size is None else human_size(repo_file.size)
        line = f"  {size:>10}  {repo_file.path}{_kind_note(repo_file.path)}"
        typer.echo(clean_text(line, single_line=True))
    raw = typer.prompt(
        # The leading * matters: patterns match the full repo path, so
        # bare "Q4_K_M*" matches nothing (live mispull, 2026-07-12).
        "files to pull (comma-separated patterns, e.g. *Q4_K_M* or *.gguf,*mmproj*)",
        default="",
        show_default=False,
    )
    return [pattern.strip() for pattern in raw.split(",") if pattern.strip()]
