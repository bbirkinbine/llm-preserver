"""Byte-identity matching between a local Ollama model and hub files (spec 0013).

Pure logic, no I/O. A hub GGUF *matches* iff its hub-declared SHA256
equals a local model-layer digest — the only verdict the tool ever
states. Verdicts keep the hub's file order (facts, never a ranking),
and the pasteable archive command follows the 0007 rules: repo id
validated at composition, filename scrubbed then shell-quoted.
"""

import glob
import shlex
from dataclasses import dataclass

from llm_preserver.hub import RepoFile
from llm_preserver.hub_discovery import looks_like_repo_id
from llm_preserver.render import clean_text


@dataclass(frozen=True)
class GgufVerdict:
    """The stated fact about one candidate GGUF file.

    Attributes:
        path: Repo-relative filename, verbatim from the hub.
        size: Hub-reported size in bytes, or None when unreported.
        matched: True iff the hub-declared SHA256 equals a local
            model-layer digest; a file the hub publishes no hash for
            never matches.
    """

    path: str
    size: int | None
    matched: bool


def match_gguf_files(local_digests: list[str], files: list[RepoFile]) -> list[GgufVerdict]:
    """State the byte-identity verdict for every GGUF in ``files``.

    Args:
        local_digests: Model-layer SHA256 digests from the local store.
        files: A candidate repo's file list, hub order.

    Returns:
        One verdict per ``.gguf`` file (case-insensitive suffix), in
        the given order.
    """
    wanted = {digest.lower() for digest in local_digests}
    return [
        GgufVerdict(
            path=file.path,
            size=file.size,
            matched=file.sha256 is not None and file.sha256.lower() in wanted,
        )
        for file in files
        if file.path.lower().endswith(".gguf")
    ]


def compose_pull_command(repo_id: str, filename: str) -> str | None:
    """The pasteable archive command for a byte-identical match.

    0007 rules: only an ``<org>/<name>``-shaped repo id may become an
    argv token (quoting cannot defuse a token like ``--yes``), and a
    filename that scrubbing would alter gets no command at all — a
    scrubbed pattern would not match the real file, so printing it
    would hand out a command that archives nothing. ``--include`` is
    an fnmatch pattern, so glob metacharacters in the filename are
    escaped to keep the printed command matching exactly that file
    (adversarial review, 2026-07-31).

    Returns:
        The command line, or None when the repo id is not shaped like
        a two-component hub repo id or the filename is unsafe to echo
        — the match verdict is still reported, just without the
        paste line.
    """
    if "/" not in repo_id or not looks_like_repo_id(repo_id):
        return None
    if clean_text(filename, single_line=True) != filename:
        return None
    quoted_pattern = shlex.quote(glob.escape(filename))
    return f"llm-preserver pull {repo_id} --include {quoted_pattern}"
