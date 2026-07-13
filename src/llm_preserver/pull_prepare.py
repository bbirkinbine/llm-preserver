"""Everything a pull decides before any weight bytes move (spec 0005).

``prepare_pull`` runs the shared front half of every pull — resolve
the tree, apply the selection and grouping rules, plan the downloads,
evaluate advisories, total the sizes, read free disk — and returns it
as one value. ``pull_model`` executes a preparation after the size
confirmation; ``pull --plan`` renders one and exits. One code path is
what makes the printed plan match what a real pull does.
"""

import json
import logging
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from llm_preserver.archive import require_archive
from llm_preserver.hub import HubClientProtocol, PullError, PullUserError, RepoFile, RepoInfo
from llm_preserver.pull_advisory import Advisory, advisories_for, archived_hub_repos
from llm_preserver.pull_grouping import (
    ConfirmCallback,
    load_existing_record,
    require_single_snapshot_source,
    resolve_model_id,
)
from llm_preserver.pull_plan import PullPlan, plan_downloads
from llm_preserver.pull_preflight import already_staged_bytes, total_selected_size
from llm_preserver.records import ArtifactFormat, ModelRecord
from llm_preserver.selection import (
    infer_format_subdir,
    require_case_distinct_targets,
    require_nondoc_selection,
    select_files,
    selects_all_weights,
)

logger = logging.getLogger(__name__)

STAGING_DIRNAME = ".staging"


@dataclass(frozen=True)
class PullPreparation:
    """A pull's full decision state, computed before any bytes move."""

    repo_id: str
    info: RepoInfo
    creator: str
    name: str
    model_dir: Path
    subdir: ArtifactFormat
    selected: list[RepoFile]
    plan: PullPlan
    needed_bytes: int
    disk_free: int
    advisories: list[Advisory]
    staging_dir: Path
    select_all: bool
    # Defaulted fields sit outside the report tests' constructor
    # contract: record is carried for pull_model's execute half;
    # adapter_config_fetched marks the one adjudicated exception to
    # "--plan downloads nothing" so the report can say so.
    record: ModelRecord | None = None
    adapter_config_fetched: bool = False


# An adapter config is kilobytes (peft serializes a flat dict); a hub
# file claiming to be one at megabyte scale is not worth fetching for
# an advisory.
_ADAPTER_CONFIG_MAX_BYTES = 1024 * 1024


def _fetch_adapter_base(
    client: HubClientProtocol, repo_id: str, info: RepoInfo
) -> tuple[str | None, bool]:
    """Read ``base_model_name_or_path`` from the repo's adapter config.

    Returns:
        ``(base_model_pointer, fetched)`` — the pointer is None for
        any unusable config; ``fetched`` is True whenever a download
        was attempted, so the plan report can disclose it.

    Provenance: peft ``src/peft/config.py`` — ``save_pretrained``
    writes ``adapter_config.json`` at the repo root with the
    ``base_model_name_or_path`` field
    (https://github.com/huggingface/peft, Apache-2.0, retrieved
    2026-07-12).

    Adjudicated 2026-07-12: accuracy beats purity — when the tree
    ships a root-level ``adapter_config.json``, both real pulls and
    ``--plan`` fetch that small file (into a throwaway temp dir,
    never the archive or its staging) so the adapter-base advisory
    can name the exact follow-up pull, and say so out loud. Hub data
    is untrusted, and an advisory input must never abort a pull:
    oversized, malformed, unfetchable, or non-object configs all
    yield None. Root-only matching also keeps a nested decoy from
    shadowing the real config.
    """
    config = next((f for f in info.files if f.path == "adapter_config.json"), None)
    if config is None:
        return None, False
    if config.size is not None and config.size > _ADAPTER_CONFIG_MAX_BYTES:
        logger.debug("adapter_config.json declares %d bytes: too large, skipping", config.size)
        return None, False
    logger.info("fetching %s to read its base-model pointer (advisory only)", config.path)
    try:
        with tempfile.TemporaryDirectory(prefix="llm-preserver-advisory-") as scratch:
            local = client.download(
                repo_id=repo_id,
                filename=config.path,
                revision=info.commit,
                dest_dir=Path(scratch),
            )
            parsed = json.loads(local.read_text(encoding="utf-8"))
    except (OSError, ValueError, PullError) as exc:
        logger.debug("adapter_config.json unusable for the advisory: %s", exc)
        return None, True
    if not isinstance(parsed, dict):
        return None, True
    base = parsed.get("base_model_name_or_path")
    return (base if isinstance(base, str) and base else None), True


def prepare_pull(
    archive_root: Path,
    repo_id: str,
    client: HubClientProtocol,
    *,
    include: Sequence[str],
    model: str | None = None,
    repo_info: RepoInfo | None = None,
    refresh_docs: bool = False,
    select_all: bool = False,
    confirm: ConfirmCallback,
) -> PullPreparation:
    """Resolve, select, group, plan, and advise — download nothing.

    Asks ``confirm`` the plan-affecting questions (grouping,
    every-weight); the size confirmation belongs to the caller. Does
    not raise on insufficient disk — the caller compares
    ``needed_bytes`` against ``disk_free`` and picks its own refusal.

    Args:
        archive_root: An initialized archive root.
        repo_id: Exact hub repo id (``namespace/repo``) — never fuzzy.
        client: The hub seam (real ``HubClient`` or a test double).
        include: fnmatch patterns selecting files; docs always ride.
            Ignored under ``select_all``.
        model: ``<creator>/<model>`` override for the canonical model
            directory; None infers it from ``base_model`` metadata.
        repo_info: Pre-fetched repo metadata — spec 0003 mandates one
            metadata call per pull; None fetches it here.
        refresh_docs: Plan replacements for changed upstream doc files.
        select_all: Full snapshot: the selection is the whole tree.
        confirm: Yes/no callback for the plan-affecting questions.

    Returns:
        The pull's complete decision state.

    Raises:
        PullError: User-input or hub-side faults found while planning.
        ArchiveError: If ``archive_root`` is not a usable archive.
    """
    require_archive(archive_root)
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
    adapter_base, adapter_config_fetched = _fetch_adapter_base(client, repo_id, info)
    advisories = advisories_for(
        info.files,
        selected,
        record,
        repo_id=repo_id,
        base_model=info.base_model,
        adapter_base=adapter_base,
        archived_repos=archived_hub_repos(archive_root),
        model_override=model,
    )
    # Only the files this run must fetch count, and bytes already in
    # staging (interrupted-pull leftovers the client reuses) are not
    # charged twice.
    needed, _ = total_selected_size([planned.repo_file for planned in plan.to_download])
    needed = max(needed - already_staged_bytes(staging_dir, plan.to_download), 0)
    return PullPreparation(
        repo_id=repo_id,
        info=info,
        creator=creator,
        name=name,
        model_dir=model_dir,
        subdir=subdir,
        selected=selected,
        plan=plan,
        needed_bytes=needed,
        disk_free=shutil.disk_usage(archive_root).free,
        advisories=advisories,
        staging_dir=staging_dir,
        select_all=select_all,
        record=record,
        adapter_config_fetched=adapter_config_fetched,
    )
