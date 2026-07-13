"""Companion-artifact advisories (spec 0005).

A curated rules table — data, not inference — maps repo-tree filename
patterns to artifact kinds. When a pull's selection excludes a
companion the tree ships, or a machine-readable cross-repo dependency
is absent from the archive, the pull prints an advisory naming the
exact remedy. Advisory only: nothing here ever changes a selection.
Every row is archive-aware — an advisory means "you are missing
this", never "this exists".

External-reference provenance (spec 0005 ``## External references``,
all retrieved 2026-07-12):

- ``mmproj-*``: llama.cpp ``convert_hf_to_gguf.py`` ``--mmproj`` help
  ("An 'mmproj-' prefix will be added to the output file name") and
  ``common/download.cpp``. https://github.com/ggml-org/llama.cpp (MIT).
- ``mtp-*``: llama.cpp ``common/download.cpp``
  (``find_best_sibling(files, model, "mtp-")``; ``--mtp`` flag help).
  Optional sidecar convention since PR #22673 (2026-05). MIT.
- ``*imatrix*``: llama.cpp ``tools/imatrix/README.md``;
  ``download.cpp`` treats imatrix-named GGUFs as non-model
  companions. MIT.
- Shard naming: HF transformers big-model sharding
  (``model-00001-of-00006.safetensors``,
  https://huggingface.co/docs/transformers/main/en/big_models,
  Apache-2.0) and llama.cpp ``src/llama.cpp``
  ``SPLIT_PATH_FORMAT = "%s-%05d-of-%05d.gguf"`` (MIT).
"""

import fnmatch
import logging
import re
import shlex
from collections.abc import Sequence, Set
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlparse

from pydantic import ValidationError

from llm_preserver.hub import RepoFile
from llm_preserver.records import RECORD_FILENAME, ModelRecord, load_record

logger = logging.getLogger(__name__)

# (basename fnmatch pattern, artifact kind) — one row per curated
# companion convention; adding a kind is a one-row change. Substring
# matches on purpose: llama.cpp's own downloader classifies by
# substring (download.cpp excludes filenames *containing* mmproj /
# imatrix / mtp-), and real repos ship mid-name forms like
# <model>-mmproj-f16.gguf (adjudicated 2026-07-12).
COMPANION_RULES: tuple[tuple[str, str], ...] = (
    ("*mmproj*", "vision projector"),
    ("*mtp-*", "speculative-decoding head"),
    ("*imatrix*", "quantization calibration data"),
)

# Exact hub repo id — the only shape ever embedded in a runnable
# "run: llm-preserver pull ..." remedy. Anything else (hub metadata is
# attacker-controlled) gets a non-command advisory instead, so a
# copy-pasted remedy can never smuggle shell syntax.
_REPO_ID_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")

# model-00001-of-00003.safetensors / tiny-chat-00001-of-00002.gguf
_SHARD_RE = re.compile(r"^(?P<prefix>.+)-(?P<index>\d{5})-of-(?P<total>\d{5})(?P<ext>\.[^.]+)$")


@dataclass(frozen=True)
class Advisory:
    """One advisory finding: what needs attention and the remedy.

    ``severity`` separates "you might also want this" (``advisory`` —
    missing companions) from "check this before proceeding"
    (``warning`` — likely human error, e.g. a grouping mismatch).
    Warnings sort first and render with a distinct prefix.
    """

    kind: str
    message: str
    severity: Literal["advisory", "warning"] = "advisory"


def _record_basenames(record: ModelRecord | None) -> set[str]:
    """Basenames of every file the target model's record already holds.

    Recorded paths are archive target paths (``gguf/x`` or verbatim
    snapshot paths), not hub paths — basename comparison is the seam
    that stays true across both layouts.
    """
    if record is None:
        return set()
    return {
        PurePosixPath(entry.path).name for artifact in record.artifacts for entry in artifact.files
    }


def _companion_advisories(
    tree: Sequence[RepoFile], selected_paths: Set[str], archived_names: Set[str]
) -> list[Advisory]:
    """Same-repo rows, in tree order: shipped, excluded, not archived."""
    advisories = []
    for repo_file in tree:
        name = PurePosixPath(repo_file.path).name
        if repo_file.path in selected_paths or name in archived_names:
            continue
        for pattern, kind in COMPANION_RULES:
            if fnmatch.fnmatchcase(name, pattern):
                advisories.append(
                    Advisory(
                        kind=kind,
                        message=(
                            f"tree ships {repo_file.path} ({kind}); the selection "
                            f"excludes it — add --include {shlex.quote(f'*{name}')}"
                        ),
                    )
                )
                break
    return advisories


def _shard_set_advisories(
    tree: Sequence[RepoFile], selected_paths: Set[str], archived_names: Set[str]
) -> list[Advisory]:
    """Incomplete shard sets: some of a ``-NNNNN-of-NNNNN`` set covered.

    Zero shards selected is a deliberate exclusion, not an incomplete
    set; shards already in the record count as covered.
    """
    sets: dict[tuple[str, str, str], list[RepoFile]] = {}
    for repo_file in tree:
        path = PurePosixPath(repo_file.path)
        match = _SHARD_RE.match(path.name)
        if match:
            key = (str(path.parent), match["prefix"], match["ext"])
            sets.setdefault(key, []).append(repo_file)
    advisories = []
    for (_, prefix, ext), shards in sets.items():
        covered = [
            shard
            for shard in shards
            if shard.path in selected_paths or PurePosixPath(shard.path).name in archived_names
        ]
        missing = len(shards) - len(covered)
        if covered and missing:
            advisories.append(
                Advisory(
                    kind="sharded weight set",
                    message=(
                        f"sharded weight set '{prefix}-*{ext}' is incomplete: {missing} of "
                        # Leading * so the remedy matches nested paths too:
                        # select_files fnmatches full repo paths, not basenames.
                        f"{len(shards)} shards excluded — "
                        f"add --include {shlex.quote(f'*{prefix}-*{ext}')}"
                    ),
                )
            )
    return advisories


def _grouping_mismatch_warning(
    repo_id: str, base_model: str | None, model_override: str | None
) -> list[Advisory]:
    """The human-error row: an explicit ``--model`` metadata disagrees with.

    The explicit ``--model`` stays verbatim (spec 0003) — but when the
    repo's own metadata contradicts it, the odds are a copy-paste slip
    filing one model under another's directory (live footgun,
    2026-07-12). Silent when ``--model`` equals the declared base (the
    correct quant grouping) or the repo id (the sanctioned
    derived-model/self grouping).
    """
    if not (
        model_override and base_model and model_override != base_model and model_override != repo_id
    ):
        return []
    return [
        Advisory(
            kind="grouping mismatch",
            message=(
                f"this repo declares base model {base_model}, but --model files it "
                f"under {model_override} — verify the target model directory "
                "(the explicit --model is honored)"
            ),
            severity="warning",
        )
    ]


def _cross_repo_advisories(
    repo_id: str,
    base_model: str | None,
    adapter_base: str | None,
    archived_repos: Set[str],
) -> list[Advisory]:
    """Cross-repo rows: dependencies the archive does not hold yet.

    A runnable ``run: llm-preserver pull ...`` remedy is emitted only
    when the dependency is a well-formed hub repo id; hub metadata is
    attacker-controlled, so anything else gets a non-command advisory.
    """
    advisories = []
    if adapter_base and adapter_base not in archived_repos:
        if _REPO_ID_RE.match(adapter_base):
            message = (
                f"this adapter's base model {adapter_base} is not in the archive — "
                f"run: llm-preserver pull {adapter_base}"
            )
        else:
            message = (
                f"this adapter declares base model {adapter_base!r}, which is not a "
                "valid hub repo id — inspect adapter_config.json before pulling anything"
            )
        advisories.append(Advisory(kind="adapter base model", message=message))
    if base_model and base_model != repo_id and base_model not in archived_repos:
        if _REPO_ID_RE.match(base_model):
            message = (
                f"this repo derives from {base_model}; the full-precision master "
                f"(needed for later fine-tuning) is not in the archive — "
                f"run: llm-preserver pull {base_model} --whole-repo"
            )
        else:
            message = (
                f"this repo declares base model {base_model!r}, which is not a valid "
                "hub repo id — verify the model card before pulling anything"
            )
        advisories.append(Advisory(kind="full-precision master", message=message))
    return advisories


def advisories_for(
    tree: Sequence[RepoFile],
    selected: Sequence[RepoFile],
    record: ModelRecord | None,
    *,
    repo_id: str,
    base_model: str | None,
    adapter_base: str | None,
    archived_repos: Set[str],
    model_override: str | None = None,
) -> list[Advisory]:
    """Evaluate every advisory row against a pull's selection.

    Pure and deterministic: same inputs, same advisories, in a fixed
    order — warnings (grouping mismatch) first, then same-repo
    companions in tree order, then incomplete shard sets, then
    cross-repo dependency rows.

    Args:
        tree: The repo's whole file tree, from the one metadata call.
        selected: The selection (doc files ride along inside it).
        record: The target model's existing record, or None.
        repo_id: The repo being pulled.
        base_model: The repo's hub ``base_model`` metadata, or None.
        adapter_base: ``base_model_name_or_path`` parsed from the
            repo's ``adapter_config.json``, or None when absent.
        archived_repos: Hub repo ids already archived anywhere (from
            ``archived_hub_repos``).
        model_override: The explicit ``--model`` value, or None when
            the grouping was inferred/confirmed from metadata.

    Returns:
        The advisories that apply; empty when nothing is missing.
    """
    selected_paths = {repo_file.path for repo_file in selected}
    archived_names = _record_basenames(record)
    return [
        *_grouping_mismatch_warning(repo_id, base_model, model_override),
        *_companion_advisories(tree, selected_paths, archived_names),
        *_shard_set_advisories(tree, selected_paths, archived_names),
        *_cross_repo_advisories(repo_id, base_model, adapter_base, archived_repos),
    ]


def archived_hub_repos(archive_root: Path) -> set[str]:
    """Hub repo ids whose files are archived somewhere under this root.

    Derived from each record's artifact ``source_repo`` values — never
    from ``hub_id``, which names the canonical model a quant pull was
    grouped under, not a repo whose files were archived. An unreadable
    record is skipped: it is not evidence of an archived repo, and the
    advisory scan must never abort a pull.
    """
    repos: set[str] = set()
    models_dir = archive_root / "models"
    if not models_dir.is_dir():
        return repos
    for record_path in sorted(models_dir.glob(f"*/*/{RECORD_FILENAME}")):
        try:
            record = load_record(record_path.parent)
        except (ValidationError, ValueError, OSError):
            logger.debug("skipping unreadable record %s during advisory scan", record_path)
            continue
        for artifact in record.artifacts:
            if artifact.source_repo:
                repo_id = urlparse(artifact.source_repo).path.strip("/")
                if repo_id:
                    repos.add(repo_id)
    return repos
