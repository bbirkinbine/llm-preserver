"""Archive fixity audit (spec 0009): complete versus valid, BagIt-style.

The record enumerates *expected* files; this module checks disk
against it, per file in cheap-first order — existence, then size, then
hash — failing fast so a missing or truncated file never pays for a
hash. Payloads and records are never modified; the one write is the
regenerable ``manifest-sha256.txt`` sidecar, refreshed after a full
(hashing) audit of any model with a readable record.

Payload hashes go through the ``llm_preserver.hashing`` seam as a
late-bound module attribute (``hashing.sha256_of``) so tests can count
and fault-inject every hash call. The record file's own manifest
digest deliberately does not: it must reflect the on-disk bytes, is
size-capped at load, and is not part of the payload-hash contract.
"""

from llm_preserver.verify.core import verify_archive
from llm_preserver.verify.models import (
    DRIFT_STATES,
    LAYOUT_STATES,
    FileProblem,
    ModelVerifyResult,
    ProgressEvents,
    VerifyReport,
)

__all__ = [
    "DRIFT_STATES",
    "LAYOUT_STATES",
    "FileProblem",
    "ModelVerifyResult",
    "ProgressEvents",
    "VerifyReport",
    "verify_archive",
]
