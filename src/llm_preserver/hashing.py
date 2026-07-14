"""Streaming file hashing, shared by pull planning and verify.

Extracted from ``pull_plan`` (spec 0009): verify re-hashes archived
payloads and must not depend on pull planning. Callers that need the
hash seam observable (tests count and fault-inject hash calls) resolve
``sha256_of`` as a module attribute at call time —
``hashing.sha256_of(path)`` — never an early ``from``-import binding.
"""

import hashlib
from pathlib import Path

_HASH_CHUNK_BYTES = 1 << 20


def sha256_of(path: Path) -> str:
    """Stream-hash a file without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()
