"""Hub revision helpers shared by the client, composers, and display (spec 0024).

A ``--revision`` value is a branch, tag, or commit as the human typed
it: a selector for one invocation, never provenance. The pull resolves
it to the full commit the hub returns, and only that commit reaches
downloads, records, and composed commands. One home for the 40-hex rule
keeps the client, the composers, and the plan report from drifting.
"""

import re
from typing import TYPE_CHECKING

from llm_preserver.render import clean_text

if TYPE_CHECKING:
    from llm_preserver.hub import HubClientProtocol, RepoInfo

# Case-insensitive on input (plan decision 4); the hub answers lowercase.
_FULL_SHA_RE = re.compile(r"[0-9a-fA-F]{40}")


def is_full_sha(value: str) -> bool:
    """Whether ``value`` is a full 40-hex commit hash (either case)."""
    return _FULL_SHA_RE.fullmatch(value) is not None


def revision_line(requested: str, commit: str) -> str:
    """The one line naming a requested revision and the commit it resolved to.

    Args:
        requested: The ``--revision`` value as typed (scrubbed here).
        commit: The full commit the hub resolved it to.

    Returns:
        ``revision: <ref> resolves to commit <sha>``, or
        ``revision: commit <sha>`` when the request was that commit.
    """
    if requested.lower() == commit.lower():
        return f"revision: commit {commit}"
    return f"revision: {clean_text(requested, single_line=True)} resolves to commit {commit}"


def fetch_repo_info(client: "HubClientProtocol", repo_id: str, revision: str | None) -> "RepoInfo":
    """Make the pull's one metadata call, at ``revision`` when one was asked.

    The no-revision call stays positional-only — exactly the call every
    pull made before spec 0024 — so seam doubles predating the
    parameter keep working.

    Args:
        client: The hub seam.
        repo_id: Exact hub repo id.
        revision: The requested branch, tag, or commit; None for the
            default branch.

    Returns:
        The repo metadata at the resolved commit.
    """
    if revision is None:
        return client.repo_info(repo_id)
    return client.repo_info(repo_id, revision=revision)
