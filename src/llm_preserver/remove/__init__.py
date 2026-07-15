"""Managed removal (spec 0010): the archive's one deletion path.

CLI-free: ``plan_removal`` describes what a removal will delete and
``execute_removal`` deletes it. Two granularities — the whole model
(record, payload, staging leftovers) or a pattern-scoped subset of a
model's payload files.

Split into ``models`` (data types, exceptions, path-safety primitives),
``plan`` (deciding what to delete), and ``execute`` (deleting it) at the
300-line cap. This ``__init__`` re-exports the public API so
``from llm_preserver.remove import ...`` keeps working.
"""

from llm_preserver.remove.execute import execute_removal
from llm_preserver.remove.models import (
    ModelNotFound,
    PlannedFile,
    RemoveError,
    RemovePlan,
    RemoveUserError,
)
from llm_preserver.remove.plan import plan_removal

__all__ = [
    "ModelNotFound",
    "PlannedFile",
    "RemoveError",
    "RemovePlan",
    "RemoveUserError",
    "execute_removal",
    "plan_removal",
]
