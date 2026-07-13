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
from collections.abc import Sequence
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
from llm_preserver.pull_grouping import (
    ConfirmCallback,
    load_existing_record,
    require_single_snapshot_source,
    resolve_model_id,
)
from llm_preserver.pull_plan import plan_downloads
from llm_preserver.pull_preflight import (
    already_staged_bytes,
    human_size,
    require_disk_space,
    total_selected_size,
)
from llm_preserver.pull_record import update_record, write_manifest
from llm_preserver.pull_transfer import download_and_archive
from llm_preserver.records import FileEntry, Role, save_record
from llm_preserver.selection import (
    infer_format_subdir,
    require_case_distinct_targets,
    require_nondoc_selection,
    select_files,
    selects_all_weights,
)

logger = logging.getLogger(__name__)

STAGING_DIRNAME = ".staging"


def _validated_roles(roles: Sequence[str]) -> list[Role]:
    """Validate caller-supplied role names against the record vocabulary."""
    valid = get_args(Role)
    unknown = [role for role in roles if role not in valid]
    if unknown:
        raise PullUserError(f"unknown role(s) {unknown!r}: valid roles are {', '.join(valid)}")
    return cast(list[Role], list(roles))


def _snapshot_confirmation(to_download: int, selected: int, needed_bytes: int, repo_id: str) -> str:
    """Compose the --all confirmation: what will actually download.

    Shows the remaining work (spec 0004 adjudications) — download count
    out of the tree total, net bytes still needed, and the
    already-covered count when any file is skipped. Never filenames:
    listing 500 shards is noise.
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
        select_all: Full snapshot (spec 0004): the selection is the
            repo's whole tree, kept at its in-tree paths; one
            file-count + total-size confirmation replaces the per-file
            prompts, and a disk-space preflight refuses before any
            bytes download.
        confirm: Yes/no prompt callback for grouping and size/weight
            confirmations.

    Returns:
        The model directory the pull landed in.

    Raises:
        PullError: One of the four fault-domain errors — user input,
            local environment, hub-side, or integrity.
        ArchiveError: If ``archive_root`` is not a usable archive.
    """
    require_archive(archive_root)
    role_list = _validated_roles(roles)
    info = repo_info if repo_info is not None else client.repo_info(repo_id)
    if not info.files:
        raise PullUserError(f"{repo_id} has no files at revision {info.commit}: nothing to archive")
    # Grouping direction is a property of the repo's whole tree, not of
    # which files were selected (spec 0004 adjudications).
    tree_format = infer_format_subdir([f.path for f in info.files], repo_id)
    creator, name = resolve_model_id(model, info, repo_id, confirm, tree_format)
    if select_all:
        selected = list(info.files)
    else:
        selected = select_files(info.files, include)
        require_nondoc_selection(selected, info.files, repo_id, include)
        if selects_all_weights(info.files, selected) and not confirm(
            f"selection covers every weight file in {repo_id}; pull them all?"
        ):
            raise PullUserError("every-weight pull declined: narrow --include and re-run")
    require_case_distinct_targets(selected)
    subdir = infer_format_subdir([f.path for f in selected], repo_id)
    model_dir = archive_root / "models" / creator / name
    record = load_existing_record(model_dir)
    if select_all:
        # One source repo per format subdirectory (spec 0004).
        require_single_snapshot_source(record, subdir, repo_id)
    plan = plan_downloads(
        selected,
        subdir,
        model_dir,
        record,
        repo_id=repo_id,
        commit=info.commit,
        refresh_docs=refresh_docs,
        relocate_docs=not select_all,  # snapshots keep the tree verbatim
    )
    staging_dir = archive_root / STAGING_DIRNAME / creator / name
    needed = 0
    if select_all:
        # Plan → preflight → confirm (spec 0004 adjudications): refuse
        # an over-budget pull before asking anyone to confirm it. Only
        # the files this run must fetch count, and bytes already in
        # staging (interrupted-pull leftovers the client reuses) are
        # not charged twice.
        needed, _ = total_selected_size([planned.repo_file for planned in plan.to_download])
        needed = max(needed - already_staged_bytes(staging_dir, plan.to_download), 0)
        require_disk_space(archive_root, needed)
    if not plan.to_download and not plan.adopted:
        logger.info("nothing to pull: every selected file is already archived in %s", model_dir)
        return model_dir
    if select_all and not confirm(
        _snapshot_confirmation(len(plan.to_download), len(selected), needed, repo_id)
    ):
        raise PullUserError("full-snapshot pull declined: nothing downloaded")
    try:
        new_entries: list[FileEntry] = list(plan.adopted)
        if plan.to_download:
            new_entries.extend(
                download_and_archive(
                    client, repo_id, info, plan.to_download, staging_dir, model_dir
                )
            )
        record = update_record(record, info, repo_id, creator, name, role_list, subdir, new_entries)
        write_manifest(model_dir, record)
        save_record(record, model_dir)
        if plan.to_download:
            # Staging now holds only the client's .cache/huggingface
            # bookkeeping, which must never reach the archive; drop it.
            shutil.rmtree(staging_dir)
    except PullError:
        raise
    except OSError as exc:
        raise PullEnvError(
            f"local filesystem failure during pull: {exc}; "
            "check disk space and permissions, then retry"
        ) from exc
    logger.info("pulled %d file(s) from %s into %s", len(new_entries), repo_id, model_dir)
    return model_dir
