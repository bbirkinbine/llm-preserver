"""Everything a pull decides before any weight bytes move (spec 0005).

``prepare_pull`` runs the shared front half of every pull — resolve
the tree, apply the selection and grouping rules, plan the downloads,
evaluate advisories, total the sizes, read free disk — and returns it
as one value. ``pull_model`` executes a preparation after the size
confirmation; ``pull --plan`` renders one and exits. One code path is
what makes the printed plan match what a real pull does.
"""

import logging
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from llm_preserver.archive import require_archive
from llm_preserver.hub import HubClientProtocol, PullUserError, RepoFile, RepoInfo
from llm_preserver.pull_advisory import Advisory, advisories_for, archived_hub_repos
from llm_preserver.pull_grouping import (
    ConfirmCallback,
    confirm_default_home,
    load_existing_record,
    parse_model_id,
    propose_default_home,
    require_single_snapshot_source,
)
from llm_preserver.pull_metadata import fetch_adapter_base, resolved_base_model
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
    every-weight); the size confirmation belongs to the caller. When
    the plan finds nothing to download and nothing to adopt AND the
    home is not hub-derived, no question is asked at all (spec 0014) —
    a no-op pull at a user-chosen home needs no answers. A hub-derived
    home (the declared ``base_model``) always confirms first. Does not
    raise on insufficient disk — the caller compares ``needed_bytes``
    against ``disk_free`` and picks its own refusal.

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
    # Rename-resolve the declared base once, so grouping proposals,
    # the mismatch warning, and the master advisory all speak the
    # hub's current name (adjudicated 2026-07-13).
    info = replace(info, base_model=resolved_base_model(client, info.base_model))
    # Grouping direction is a property of the repo's whole tree, not of
    # which files were selected (spec 0004 adjudications).
    tree_format = infer_format_subdir([f.path for f in info.files], repo_id)
    # Resolve the home (spec 0014). A *hub-derived* home — the declared
    # base_model, offered when a GGUF/MLX tree groups under its base —
    # confirms up front, before it can name any directory: hub metadata
    # never names an archive directory without a human yes (spec 0006
    # adjudication, upheld at the 0014 review round — a hostile
    # base_model plus a name+size-matched hashless file could otherwise
    # steer a silent "already archived" verdict at another model's
    # directory). When the home is the repo id the user typed (--model,
    # no base_model, or hf-snapshot lineage), it is resolved
    # tentatively and the grouping question waits until the plan proves
    # there is work to do.
    grouping_prompt: str | None = None
    if model is None:
        home, prompt = propose_default_home(info, repo_id, tree_format)
        if home != repo_id:
            confirm_default_home(home, prompt, confirm)
        else:
            grouping_prompt = prompt
    else:
        home = model
    creator, name = parse_model_id(home)
    if select_all:
        selected = list(info.files)
    else:
        selected = select_files(info.files, include)
        require_nondoc_selection(selected, info.files, repo_id, include)
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
    if plan.to_download or plan.adopted:
        # Work to do: ask today's questions in today's order (grouping,
        # then every-weight; the size confirmation stays the caller's).
        # A plan with nothing to download and nothing to adopt skips
        # the deferred questions (spec 0014): y reaches the no-op and N
        # aborts — both no-ops — so no answer can change the outcome.
        if grouping_prompt is not None:
            confirm_default_home(home, grouping_prompt, confirm)
        if (
            not select_all
            and selects_all_weights(info.files, selected)
            and not confirm(f"selection covers every weight file in {repo_id}; pull them all?")
        ):
            raise PullUserError("every-weight pull declined: narrow --include and re-run")
    staging_dir = archive_root / STAGING_DIRNAME / creator / name
    adapter_base, adapter_config_fetched = fetch_adapter_base(client, repo_id, info)
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
