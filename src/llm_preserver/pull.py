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
from llm_preserver.layout import split_repo_id
from llm_preserver.pull_home import ConfirmCallback
from llm_preserver.pull_preflight import human_size, require_disk_budget
from llm_preserver.pull_prepare import STAGING_DIRNAME, PullPreparation, prepare_pull
from llm_preserver.pull_record import update_record, write_manifest
from llm_preserver.pull_transfer import download_and_archive
from llm_preserver.records import FileEntry, Role, save_record
from llm_preserver.render import clean_text

logger = logging.getLogger(__name__)

__all__ = ["STAGING_DIRNAME", "pull_model"]


def validated_base_model(base_model: str | None) -> str | None:
    """Refuse a ``--base-model`` that is not a usable repo id.

    Exactly two components, via the one validator the whole tool shares:
    the value is recorded, rendered into ``MODEL-RECORD.md``, and read
    back as a lineage pointer, so it can never be free text.

    Raises:
        PullUserError: If the claim is not an ``<owner>/<repo>`` id.
    """
    if base_model is None:
        return None
    try:
        split_repo_id(base_model)
    except ValueError as exc:
        raise PullUserError(
            f"--base-model must look like <owner>/<repo>, got {base_model!r}"
        ) from exc
    return base_model


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


def _apply_metadata_only(
    prep: PullPreparation, roles: list[Role], asserted_base: str | None
) -> bool:
    """Write curator metadata into an existing record, moving no bytes.

    Returns:
        True when something was recorded — the caller then reports a
        metadata write rather than "nothing to pull". False when the
        run really is a no-op, leaving the 0014 path untouched.
    """
    record = prep.record
    if record is None:
        return False
    new_roles = [role for role in roles if role not in record.roles]
    # A human asserting the value the card already gave is still a
    # change: the claim is the same, the authority behind it is not,
    # and `base_model_source` exists precisely to tell them apart.
    asserts_base = asserted_base is not None and (
        asserted_base != record.base_model or record.base_model_source != "asserted"
    )
    changed = bool(new_roles) or asserts_base
    if not changed:
        return False
    record.roles.extend(new_roles)
    if asserts_base and asserted_base is not None:
        record.base_model = asserted_base
        record.base_model_source = "asserted"
    write_manifest(prep.model_dir, record)
    save_record(record, prep.model_dir)
    return True


def pull_model(
    archive_root: Path,
    repo_id: str,
    client: HubClientProtocol,
    *,
    include: Sequence[str],
    roles: Sequence[str] = (),
    base_model: str | None = None,
    repo_info: RepoInfo | None = None,
    refresh_docs: bool = False,
    select_all: bool = False,
    confirm: ConfirmCallback,
    on_transfer_start: Callable[[str], None] | None = None,
    on_no_op: Callable[[], None] | None = None,
    on_metadata_only: Callable[[], None] | None = None,
) -> Path:
    """Pull selected files from a hub repo into the archive.

    Args:
        archive_root: An initialized archive root.
        repo_id: Exact hub repo id (``namespace/repo``) — never fuzzy.
        client: The hub seam (real ``HubClient`` or a test double).
        include: fnmatch patterns selecting files; docs always ride.
            Ignored under ``select_all`` (the CLI rejects the combination).
        base_model: Curator-asserted lineage recorded verbatim; never
            consulted for the destination (ADR 0003). Validated as a
            repo id before it reaches the record.
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
        confirm: Yes/no prompt callback for the size/weight
            confirmations.
        on_transfer_start: Called once with the model directory
            (``<owner>/<repo>``, ADR 0003) after every confirmation
            succeeds and before the first download begins — the moment
            the resume hint is both accurate (the selection is settled)
            and useful (spec 0007). Not called for adopt-only pulls:
            there is no transfer to interrupt.
        on_metadata_only: Called when the pull moved no bytes but did
            record curator metadata (``--role`` / ``--base-model``).
            A separate seam from ``on_no_op`` because the two outcomes
            need different final lines: one wrote the record, the other
            did nothing at all, and a run must not read as more than it
            did (the 0014 principle).
        on_no_op: Called when the pull finds nothing to download and
            nothing to adopt (spec 0014) — the caller's chance to
            report "already archived" instead of pull success, since a
            run that moved no bytes and wrote no record must not read
            as one that did.

    Returns:
        The model directory the pull landed in.

    Raises:
        PullError: One of the four fault-domain errors — user input,
            local environment, hub-side, or integrity.
        ArchiveError: If ``archive_root`` is not a usable archive.
    """
    require_archive(archive_root)
    role_list = validated_roles(roles)
    asserted_base = validated_base_model(base_model)
    prep = prepare_pull(
        archive_root,
        repo_id,
        client,
        include=include,
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
        # Nothing to *download* — but ``--role`` and ``--base-model``
        # are record edits, not downloads, and already-archived is
        # exactly when a curator wants to correct a lineage claim. The
        # 0014 no-op contract covers bytes; silently discarding an
        # explicit instruction is the same class of fault 0014 itself
        # was written to close (live use, 2026-08-11).
        if _apply_metadata_only(prep, role_list, asserted_base):
            logger.info("recorded metadata for %s (no files to pull)", repo_id)
            if on_metadata_only is not None:
                on_metadata_only()
            return prep.model_dir
        logger.info(
            "nothing to pull: every selected file is already archived in %s", prep.model_dir
        )
        if on_no_op is not None:
            on_no_op()
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
            asserted_base=asserted_base,
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
