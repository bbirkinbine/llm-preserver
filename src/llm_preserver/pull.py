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
    PullIntegrityError,
    PullUserError,
    RepoFile,
    RepoInfo,
)
from llm_preserver.pull_plan import (
    ConfirmCallback,
    PlannedDownload,
    load_existing_record,
    plan_downloads,
    resolve_model_id,
    sha256_of,
)
from llm_preserver.pull_record import update_record, write_manifest
from llm_preserver.records import FileEntry, Role, save_record
from llm_preserver.selection import (
    infer_format_subdir,
    is_doc_file,
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


def _require_nondoc_selection(
    selected: Sequence[RepoFile], info: RepoInfo, repo_id: str, include: Sequence[str]
) -> None:
    """Reject selections whose only content is the always-riding docs.

    Docs ride along with every pull, so a zero-match ``--include`` (a
    typo, a case-sensitive fnmatch miss, blank interactive input) still
    yields a non-empty selection — archiving only a README and stamping
    a wrong-format artifact. That is a user-input fault, not a pull.
    """
    if any(not is_doc_file(repo_file.path) for repo_file in selected):
        return
    available = ", ".join(
        repo_file.path for repo_file in info.files if not is_doc_file(repo_file.path)
    )
    raise PullUserError(
        f"no files in {repo_id} match include patterns {list(include)!r} "
        "(docs always ride along but cannot be the whole pull); adjust --include — "
        f"available files: {available or 'none'}"
    )


def _require_case_distinct_targets(selected: Sequence[RepoFile]) -> None:
    """Reject selections that collide on case-insensitive filesystems.

    Two paths differing only by case (``README.md`` / ``readme.md``)
    map to one file on APFS/NTFS: the second move would consume the
    first's inode and leave a half-moved, unrecorded file.
    """
    seen: dict[str, str] = {}
    for repo_file in selected:
        folded = repo_file.path.lower()
        if folded in seen and seen[folded] != repo_file.path:
            raise PullUserError(
                f"selection contains paths that collide on case-insensitive filesystems: "
                f"{seen[folded]!r} and {repo_file.path!r}; narrow --include to one of them"
            )
        seen[folded] = repo_file.path


def _discard_corrupt_staged_file(staging_dir: Path, local: Path, hub_path: str) -> None:
    """Drop a hash-mismatched download so a retry cannot reuse it.

    The client's ``.cache/huggingface`` bookkeeping marks the staged
    file complete; left in place, every retry would reuse the corrupt
    bytes and re-fail forever.
    """
    local.unlink(missing_ok=True)
    metadata = staging_dir / ".cache" / "huggingface" / "download" / f"{hub_path}.metadata"
    metadata.unlink(missing_ok=True)


def _move_into_archive(
    staged: Sequence[tuple[PlannedDownload, Path, str]], model_dir: Path, commit: str
) -> list[FileEntry]:
    """Move fully hashed staged files into place; lock and describe them.

    Consumes the exact target paths planning validated, and re-checks
    containment right before the write — the archive must be impossible
    to escape even if an earlier check regresses. A ``--refresh-docs``
    replacement (``replace_existing``) unlocks the superseded doc file
    first; weight paths never plan a replacement.
    """
    entries: list[FileEntry] = []
    model_root = model_dir.resolve()
    for planned, local, digest in staged:
        target = model_dir / planned.target_rel
        if not target.resolve().is_relative_to(model_root):
            raise PullUserError(
                f"repo supplies an unsafe filename {planned.repo_file.path!r}: "
                f"target escapes the model directory {model_dir}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        if planned.replace_existing and target.exists():
            # --refresh-docs: unlock the superseded doc before replacing.
            target.chmod(target.stat().st_mode | 0o200)
        size = local.stat().st_size
        local.replace(target)
        # ADR 0001 payload locking: clear write permission after the move.
        target.chmod(target.stat().st_mode & ~0o222)
        entries.append(
            FileEntry(
                path=planned.target_rel,
                sha256=digest,
                size=size,
                source="original",
                # digest == hub hash was enforced before the move.
                provenance="verified" if planned.repo_file.sha256 is not None else "hashed-locally",
                revision=commit,
            )
        )
    return entries


def _download_and_archive(
    client: HubClientProtocol,
    repo_id: str,
    info: RepoInfo,
    to_download: Sequence[PlannedDownload],
    staging_dir: Path,
    model_dir: Path,
) -> list[FileEntry]:
    """Stage, hash, verify, and move every planned download."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[PlannedDownload, Path, str]] = []
    for planned in to_download:
        hub_path = planned.repo_file.path
        logger.debug("downloading %s at %s into %s", hub_path, info.commit, staging_dir)
        local = client.download(
            repo_id=repo_id, filename=hub_path, revision=info.commit, dest_dir=staging_dir
        )
        digest = sha256_of(local)
        declared = planned.repo_file.sha256
        if declared is not None and digest != declared.lower():
            _discard_corrupt_staged_file(staging_dir, local, hub_path)
            raise PullIntegrityError(
                f"sha256 mismatch for {hub_path} after download: hub declared {declared}, "
                f"local hash is {digest}; the file never entered the archive — retry the pull"
            )
        staged.append((planned, local, digest))
    return _move_into_archive(staged, model_dir, info.commit)


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
    confirm: ConfirmCallback,
) -> Path:
    """Pull selected files from a hub repo into the archive.

    Args:
        archive_root: An initialized archive root.
        repo_id: Exact hub repo id (``namespace/repo``) — never fuzzy.
        client: The hub seam (real ``HubClient`` or a test double).
        include: fnmatch patterns selecting files; docs always ride.
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
        confirm: Yes/no prompt callback for grouping and every-weight
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
    creator, name = resolve_model_id(model, info, repo_id, confirm)
    selected = select_files(info.files, include)
    _require_nondoc_selection(selected, info, repo_id, include)
    _require_case_distinct_targets(selected)
    if selects_all_weights(info.files, selected) and not confirm(
        f"selection covers every weight file in {repo_id}; pull them all?"
    ):
        raise PullUserError("every-weight pull declined: narrow --include and re-run")
    subdir = infer_format_subdir([f.path for f in selected], repo_id)
    model_dir = archive_root / "models" / creator / name
    record = load_existing_record(model_dir)
    plan = plan_downloads(
        selected,
        subdir,
        model_dir,
        record,
        repo_id=repo_id,
        commit=info.commit,
        refresh_docs=refresh_docs,
    )
    if not plan.to_download and not plan.adopted:
        logger.info("nothing to pull: every selected file is already archived in %s", model_dir)
        return model_dir
    staging_dir = archive_root / STAGING_DIRNAME / creator / name
    try:
        new_entries: list[FileEntry] = list(plan.adopted)
        if plan.to_download:
            new_entries.extend(
                _download_and_archive(
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
