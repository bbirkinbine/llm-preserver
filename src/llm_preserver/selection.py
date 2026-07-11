"""File selection for pull: include patterns, doc files, target paths.

Selection is the point of the selective-pull shape (spec 0003): the
user picks the artifacts that fit their hardware, and the tool always
rides the repo's documentation (README / model card, LICENSE) along
with them. This module also derives where a selected file lands:
weights at ``<format>/<file>``, docs under
``<format>/docs/<namespace>--<repo>/`` so source repos never collide
(spec 0003, review adjudications 2026-07-10).
"""

import fnmatch
from collections.abc import Sequence
from pathlib import PurePosixPath

from pydantic import ValidationError

from llm_preserver.hub import PullUserError, RepoFile
from llm_preserver.records import ID_COMPONENT_RE, ArtifactFormat, FileEntry

_DOC_BASENAMES = frozenset({"readme", "license", "licence", "notice", "copying", "use_policy"})
"""Doc-file stems (case-insensitive): README / model card and license
material, always pulled alongside the selected artifacts. ``use_policy``
covers Meta-Llama repos, which ship license terms in USE_POLICY.md."""

_WEIGHT_SUFFIXES = frozenset({".gguf", ".safetensors", ".bin", ".pt", ".pth"})
"""File extensions that carry model weights — the files whose
select-them-all case needs explicit confirmation."""


def is_doc_file(path: str) -> bool:
    """Return True for README / model card / LICENSE files.

    Args:
        path: Repo-relative file path.

    Returns:
        True when the file is repo documentation that every pull
        fetches regardless of the include patterns.
    """
    stem = PurePosixPath(path).name.split(".", 1)[0].lower()
    return stem in _DOC_BASENAMES


def _is_weight_file(path: str) -> bool:
    """Return True when the file extension indicates model weights."""
    return PurePosixPath(path).suffix.lower() in _WEIGHT_SUFFIXES


def _doc_subdir_for(repo_id: str) -> str:
    """Per-source-repo doc directory name: ``<namespace>--<repo>``."""
    namespace, sep, repo_name = repo_id.partition("/")
    if (
        not sep
        or not ID_COMPONENT_RE.fullmatch(namespace)
        or not ID_COMPONENT_RE.fullmatch(repo_name)
    ):
        raise PullUserError(f"repo id must look like <namespace>/<repo>, got {repo_id!r}")
    return f"{namespace}--{repo_name}"


def checked_target_path(subdir: str, repo_id: str, hub_path: str) -> str:
    """Build the model-dir-relative target path, rejecting unsafe names.

    Docs land under ``<format>/docs/<namespace>--<repo>/`` — collision
    free across source repos; everything else lands at
    ``<format>/<file>``. The hub path is validated *alone* (an absolute
    ``/etc/evil`` must fail on its own — concatenation would mask the
    leading slash) and then as the full target, both through the record
    schema's path validator so the write path and the record enforce
    the same rules.

    Raises:
        PullUserError: If the hub filename cannot be archived safely.
    """
    if is_doc_file(hub_path):
        target_rel = f"{subdir}/docs/{_doc_subdir_for(repo_id)}/{hub_path}"
    else:
        target_rel = f"{subdir}/{hub_path}"
    try:
        FileEntry(path=hub_path, source="original")
        FileEntry(path=target_rel, source="original")
    except ValidationError as exc:
        raise PullUserError(f"repo supplies an unsafe filename {hub_path!r}: {exc}") from exc
    return target_rel


def select_files(files: Sequence[RepoFile], include: Sequence[str]) -> list[RepoFile]:
    """Select repo files by repeatable fnmatch ``--include`` patterns.

    Doc files (README / LICENSE) are always included, matching pattern
    or not — the archive must preserve them on every pull (spec 0003).

    Args:
        files: The repo's files, from the metadata call.
        include: fnmatch patterns; a file matching any one is selected.

    Returns:
        The selected files, in repo order.
    """
    return [
        repo_file
        for repo_file in files
        if is_doc_file(repo_file.path)
        or any(fnmatch.fnmatch(repo_file.path, pattern) for pattern in include)
    ]


def selects_all_weights(files: Sequence[RepoFile], selected: Sequence[RepoFile]) -> bool:
    """Return True when a selection covers every weight in a multi-quant repo.

    Pulling every weight file of a repo that carries more than one is
    the mirror-the-repo case that requires explicit confirmation
    (spec 0003) — selection is the point.

    Args:
        files: All files in the repo.
        selected: The files the selection picked.

    Returns:
        True when the repo has more than one weight file and the
        selection includes all of them.
    """
    weight_paths = {f.path for f in files if _is_weight_file(f.path)}
    selected_paths = {f.path for f in selected}
    return len(weight_paths) > 1 and weight_paths <= selected_paths


def infer_format_subdir(paths: Sequence[str], source_repo_id: str) -> ArtifactFormat:
    """Infer the ADR 0001 format subdirectory for a selection.

    Rule order: any ``.gguf`` file wins (a GGUF quant repo is GGUF no
    matter who publishes it), then an ``mlx-community/*`` source repo,
    else the Hugging Face snapshot layout.

    Args:
        paths: The selected files' repo-relative paths.
        source_repo_id: The repo the files come from.

    Returns:
        The format subdirectory name under the model directory.
    """
    if any(PurePosixPath(path).suffix.lower() == ".gguf" for path in paths):
        return "gguf"
    if source_repo_id.startswith("mlx-community/"):
        return "mlx"
    return "hf-snapshot"
