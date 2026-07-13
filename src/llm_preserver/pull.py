"""Selective-pull orchestration: stage, hash, verify, move, record.

The pull invariants (spec 0003): files download into a staging
directory under the archive root, are hashed there, verified against
the hub-declared hash when one exists, and only then move into the
canonical model directory. The model record is written last, after
every selected file is fully on disk — a failed or interrupted pull
never records a partial artifact. Archived payload is immutable: pull
only ever adds, and any name-matches-but-content-differs case is a
hard stop, never a silent overwrite (see ``pull_plan``).
"""

import logging
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast, get_args

from llm_preserver.archive import require_archive
from llm_preserver.hub import (
    HubClientProtocol,
    PullEnvError,
    PullError,
    PullUserError,
    RepoInfo,
)
from llm_preserver.pull_grouping import ConfirmCallback
from llm_preserver.pull_preflight import human_size, require_disk_budget
from llm_preserver.pull_prepare import STAGING_DIRNAME, prepare_pull
from llm_preserver.pull_record import update_record, write_manifest
from llm_preserver.pull_transfer import download_and_archive
from llm_preserver.records import FileEntry, Role, save_record
from llm_preserver.render import clean_text

logger = logging.getLogger(__name__)

__all__ = ["STAGING_DIRNAME", "pull_model"]


def validated_roles(roles: Sequence[str]) -> list[Role]:
    """Validate caller-supplied role names against the record vocabulary."""
    valid = get_args(Role)
    unknown = [role for role in roles if role not in valid]
    if unknown:
        raise PullUserError(f"unknown role(s) {unknown!r}: valid roles are {', '.join(valid)}")
    return cast(list[Role], list(roles))


def _size_confirmation(to_download: int, selected: int, needed_bytes: int, repo_id: str) -> str:
    """Compose the size confirmation: what will actually download.

    Asked on every pull mode (spec 0004 for whole-repo; the spec 0005
    rider extends it to selective pulls). Shows the remaining work —
    download count out of the selection total, net bytes still needed,
    and the already-covered count when any file is skipped. Never
    filenames: listing 500 shards is noise. The ``pull `` prefix is
    the seam the CLI's ``--yes`` classification keys on.
    """
    plural = "file" if selected == 1 else "files"
    covered = selected - to_download
    already = f"; {covered} already archived" if covered else ""
    return (
        f"pull {to_download} of {selected} {plural} "
        f"({human_size(needed_bytes)} to download{already}) from {repo_id}?"
    )


def pull_model(
    archive_root: Path,
    repo_id: str,
    client: HubClientProtocol,
    *,
    include: Sequence[str],
    model: str | None = None,
    roles: Sequence[str] = (),
    repo_info: RepoInfo | None = None,
    refresh_docs: bool = False,
    select_all: bool = False,
    confirm: ConfirmCallback,
    on_transfer_start: Callable[[str], None] | None = None,
) -> Path:
    """Pull selected files from a hub repo into the archive.

    Args:
        archive_root: An initialized archive root.
        repo_id: Exact hub repo id (``namespace/repo``) — never fuzzy.
        client: The hub seam (real ``HubClient`` or a test double).
        include: fnmatch patterns selecting files; docs always ride.
            Ignored under ``select_all`` (the CLI rejects the combination).
        model: ``<creator>/<model>`` override for the canonical model
            directory; None infers it from ``base_model`` metadata.
        roles: Roles to assign at pull time (curator judgment; may be
            empty — the tool never fabricates them).
        repo_info: Pre-fetched repo metadata (e.g. from the interactive
            listing) — spec 0003 mandates one metadata call per pull;
            None fetches it here.
        refresh_docs: Replace changed upstream *doc* files (unlock,
            replace, re-record, re-lock). Weight paths never honor
            this flag — a changed weight remains a hard stop.
        select_all: Full snapshot (spec 0004; CLI flag
            ``--whole-repo``): the selection is the repo's whole
            tree, kept at its in-tree paths. Every mode asks the
            file-count + total-size confirmation and runs the
            disk-space preflight before any bytes download (spec
            0005 rider).
        confirm: Yes/no prompt callback for grouping and size/weight
            confirmations.
        on_transfer_start: Called once with the resolved canonical
            model directory (``<creator>/<model>``) after every
            confirmation succeeds and before the first download begins
            — the moment the resume hint is both accurate (grouping
            settled) and useful (spec 0007). Not called for adopt-only
            pulls: there is no transfer to interrupt.

    Returns:
        The model directory the pull landed in.

    Raises:
        PullError: One of the four fault-domain errors — user input,
            local environment, hub-side, or integrity.
        ArchiveError: If ``archive_root`` is not a usable archive.
    """
    require_archive(archive_root)
    role_list = validated_roles(roles)
    prep = prepare_pull(
        archive_root,
        repo_id,
        client,
        include=include,
        model=model,
        repo_info=repo_info,
        refresh_docs=refresh_docs,
        select_all=select_all,
        confirm=confirm,
    )
    for advisory in prep.advisories:
        # Advisory text embeds hub-supplied filenames/metadata; strip
        # terminal control characters before it reaches a terminal.
        # Warnings (likely human error) log at WARNING so they stand
        # apart from the INFO advisory wall.
        level = logging.WARNING if advisory.severity == "warning" else logging.INFO
        logger.log(
            level, "%s: %s", advisory.severity, clean_text(advisory.message, single_line=True)
        )
    # Plan → preflight → confirm on every mode (spec 0004 shape,
    # extended to selective pulls by the spec 0005 rider): refuse an
    # over-budget pull before asking anyone to confirm it. One disk
    # read (prepare's) backs both the figure shown and the decision.
    require_disk_budget(archive_root, prep.needed_bytes, prep.disk_free)
    if not prep.plan.to_download and not prep.plan.adopted:
        logger.info(
            "nothing to pull: every selected file is already archived in %s", prep.model_dir
        )
        return prep.model_dir
    # Adopt-only pulls (files already on disk, record catching up) move
    # zero bytes; a "pull 0 files (0 B)?" prompt would block scripted
    # re-pulls for nothing (adjudicated 2026-07-12).
    if prep.plan.to_download and not confirm(
        _size_confirmation(
            len(prep.plan.to_download), len(prep.selected), prep.needed_bytes, repo_id
        )
    ):
        raise PullUserError("pull declined: nothing downloaded")
    if prep.plan.to_download and on_transfer_start is not None:
        on_transfer_start(f"{prep.creator}/{prep.name}")
    try:
        new_entries: list[FileEntry] = list(prep.plan.adopted)
        if prep.plan.to_download:
            new_entries.extend(
                download_and_archive(
                    client,
                    repo_id,
                    prep.info,
                    prep.plan.to_download,
                    prep.staging_dir,
                    prep.model_dir,
                )
            )
        record = update_record(
            prep.record,
            prep.info,
            repo_id,
            prep.creator,
            prep.name,
            role_list,
            prep.subdir,
            new_entries,
        )
        write_manifest(prep.model_dir, record)
        save_record(record, prep.model_dir)
        if prep.plan.to_download:
            # Staging now holds only the client's .cache/huggingface
            # bookkeeping, which must never reach the archive; drop it.
            shutil.rmtree(prep.staging_dir)
    except PullError:
        raise
    except OSError as exc:
        raise PullEnvError(
            f"local filesystem failure during pull: {exc}; "
            "check disk space and permissions, then retry"
        ) from exc
    logger.info("pulled %d file(s) from %s into %s", len(new_entries), repo_id, prep.model_dir)
    return prep.model_dir
