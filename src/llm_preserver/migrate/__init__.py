"""Convert a pre-ADR-0003 archive to one directory per source repo.

Two modes, both driven by the same derived plan: in place, or as a copy
at a new root (``--to``). Neither re-downloads or re-hashes a byte —
paths *inside* an artifact do not change, so recorded digests stay true
and the manifests are regenerated from the record.
"""

from llm_preserver.migrate.copy import copy_migration
from llm_preserver.migrate.execute import execute_migration
from llm_preserver.migrate.models import (
    ArtifactMove,
    DirectoryMigration,
    MigrateError,
    MigrateEvents,
    MigratePlan,
    MigrateUserError,
)
from llm_preserver.migrate.plan import plan_migration

__all__ = [
    "ArtifactMove",
    "DirectoryMigration",
    "MigrateError",
    "MigrateEvents",
    "MigratePlan",
    "MigrateUserError",
    "copy_migration",
    "execute_migration",
    "plan_migration",
]
