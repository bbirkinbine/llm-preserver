"""Pull grouping: which canonical model directory a pull lands in.

Split out of ``pull_plan.py`` (300-line rule). Grouping is ADR 0001's
"judgment call at download time", and it is *format-directed*
(spec 0004 adjudications, 2026-07-11): GGUF/MLX trees are conversions —
same weights, different container — and group under ``base_model``;
``hf-snapshot`` trees with a ``base_model`` are derived models —
different weights — and default to their own repo id, with
``base_model`` mentioned as lineage, never used as the home. A repo
with no ``base_model`` defaults to the repo id regardless of format.
Every default is confirm-gated; ``--model`` overrides without a prompt.
"""

from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from llm_preserver.hub import PullUserError, RepoInfo
from llm_preserver.records import (
    ID_COMPONENT_RE,
    RECORD_FILENAME,
    ArtifactFormat,
    ModelRecord,
    load_record,
)

ConfirmCallback = Callable[[str], bool]
"""Callback that shows the user a prompt and returns their yes/no."""

_MODEL_FLAG_HINT = "--model <creator>/<model>"


def _confirmed_default_home(
    info: RepoInfo, repo_id: str, confirm: ConfirmCallback, tree_format: ArtifactFormat
) -> str:
    """Pick and confirm the default canonical home for a pull.

    Raises:
        PullUserError: If the user declines the offered default.
    """
    if info.base_model is None:
        # Nothing to group under, any format: the repo is its own home.
        prompt = f"{repo_id} declares no base_model; archive it as canonical model {repo_id}?"
        home = repo_id
    elif tree_format == "hf-snapshot":
        # A safetensors tree with a base_model is a *derived model* —
        # different weights — so the base is lineage, not the home.
        prompt = (
            f"{repo_id} declares base_model {info.base_model} (lineage); "
            f"archive it as its own canonical model {repo_id}?"
        )
        home = repo_id
    else:
        # GGUF/MLX trees are conversions of the base model's weights.
        prompt = f"group {repo_id} under canonical model {info.base_model}?"
        home = info.base_model
    if not confirm(prompt):
        raise PullUserError(
            f"grouping under {home} declined: re-run with {_MODEL_FLAG_HINT} "
            "to choose the canonical model directory"
        )
    return home


def resolve_model_id(
    model: str | None,
    info: RepoInfo,
    repo_id: str,
    confirm: ConfirmCallback,
    tree_format: ArtifactFormat,
) -> tuple[str, str]:
    """Resolve the canonical ``<creator>/<model>`` directory for a pull.

    A ``--model`` override is used verbatim with no prompt. Otherwise
    the default home is format-directed (see the module docstring) and
    confirmed with the user. Metadata that is present but malformed
    stays a hard stop, not a guess (spec 0003).

    Args:
        model: The ``--model`` override, or None to derive the home.
        info: The repo metadata (supplies ``base_model``).
        repo_id: The repo being pulled.
        confirm: Yes/no prompt callback.
        tree_format: Format inferred over the repo's *whole tree* —
            grouping direction is a property of the repo, not of which
            files were selected.

    Raises:
        PullUserError: On a declined confirmation or a malformed
            model id.
    """
    if model is None:
        model = _confirmed_default_home(info, repo_id, confirm, tree_format)
    creator, sep, name = model.partition("/")
    if not sep or not ID_COMPONENT_RE.fullmatch(creator) or not ID_COMPONENT_RE.fullmatch(name):
        raise PullUserError(f"model id must look like <creator>/<model>, got {model!r}")
    return creator, name


def require_single_snapshot_source(
    record: ModelRecord | None, subdir: ArtifactFormat, repo_id: str
) -> None:
    """Refuse a snapshot into a format subdirectory another repo owns.

    One source repo per format subdirectory per model (spec 0004
    adjudications): tree-verbatim snapshots would otherwise interleave
    two repos' trees in one directory, and ``--refresh-docs`` could no
    longer prove which artifact a path belongs to. Selective pulls are
    unaffected (their docs relocate; weight-name collisions already
    hard-stop on hash). Decided by recorded artifact ``source_repo``,
    not by file collision.

    Raises:
        PullUserError: If a same-format artifact from a different
            source repo is already recorded, naming both repos and the
            ways out.
    """
    if record is None:
        return
    source_repo = f"https://huggingface.co/{repo_id}"
    other = next(
        (
            artifact
            for artifact in record.artifacts
            if artifact.format == subdir
            and artifact.source_repo is not None
            and artifact.source_repo != source_repo
        ),
        None,
    )
    if other is not None:
        raise PullUserError(
            f"the {subdir}/ subdirectory of {record.hub_id} already holds files from "
            f"{other.source_repo}; a full snapshot of {repo_id} would mix two source repos "
            f"in one tree — archive it under a different home with {_MODEL_FLAG_HINT}, "
            "or pull selected files instead"
        )


def load_existing_record(model_dir: Path) -> ModelRecord | None:
    """Load the model's record if one exists; unreadable is a hard stop.

    Raises:
        PullUserError: If a record file exists but cannot be read —
            pulling on top of an unreadable record risks clobbering it.
    """
    if not (model_dir / RECORD_FILENAME).is_file():
        return None
    try:
        return load_record(model_dir)
    except (ValidationError, ValueError, OSError) as exc:
        raise PullUserError(
            f"existing record in {model_dir} cannot be read ({exc}); "
            "fix or move it before pulling into this model"
        ) from exc
