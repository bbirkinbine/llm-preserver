"""Streaming file hashing, shared by pull planning and verify.

Extracted from ``pull_plan`` (spec 0009): verify re-hashes archived
payloads and must not depend on pull planning. Callers that need the
hash seam observable (tests count and fault-inject hash calls) resolve
``sha256_of`` as a module attribute at call time —
``hashing.sha256_of(path)`` — never an early ``from``-import binding;
fakes patched over this seam must accept the optional ``progress``
keyword.
"""

import hashlib
from collections.abc import Callable
from pathlib import Path

_HASH_CHUNK_BYTES = 1 << 20


def sha256_of(path: Path, progress: Callable[[int], None] | None = None) -> str:
    """Stream-hash a file without loading it into memory.

    Args:
        path: The file to hash.
        progress: Optional callback invoked with each chunk's byte
            count as it is read — the live-progress feed for
            multi-gigabyte hashes (spec 0009).

    Returns:
        The lowercase SHA256 hex digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
            if progress is not None:
                progress(len(chunk))
    return digest.hexdigest()
