"""Refuse to merge a second commit into an archived artifact (spec 0024).

A ``--revision`` pull names one tree on purpose. If the artifact it
would land in is already recorded at a different commit, the two trees
can merge silently whenever their file names do not collide — different
shard counts, or a same-size hashless ``config.json`` — and the
artifact-level ``revision`` would then name a commit that does not hold
every file it lists. Review-round finding, adjudicated 2026-10-20:
refuse during planning, before any byte moves, and name the archived
commit so the human can extend that tree or remove it to switch.

Default-branch pulls are deliberately not guarded: ``main`` drifting
between pulls (a README edit is enough) is the normal add-a-quant-later
workflow, and refusing it would change pre-0024 behavior.
"""

from llm_preserver.hub import PullIntegrityError
from llm_preserver.records import ArtifactFormat, ModelRecord


def require_archived_commit(
    record: ModelRecord | None, subdir: ArtifactFormat, commit: str, repo_id: str
) -> None:
    """Stop a ``--revision`` pull that would mix two commits in one artifact.

    Args:
        record: The model's existing record, or None for a new model.
        subdir: The format subdirectory this pull lands in.
        commit: The full commit the requested revision resolved to.
        repo_id: The repo being pulled (for the message).

    Raises:
        PullIntegrityError: The artifact for ``subdir`` is recorded at a
            different commit.
    """
    if record is None:
        return
    for artifact in record.artifacts:
        if artifact.format != subdir or artifact.revision is None:
            continue
        if artifact.revision != commit:
            raise PullIntegrityError(
                f"{repo_id} {subdir} is archived at commit {artifact.revision}, but --revision "
                f"resolved to commit {commit}; one artifact holds one tree. To add files from "
                f"the archived tree, pass --revision {artifact.revision}; to switch trees, "
                "remove the archived artifact first"
            )
