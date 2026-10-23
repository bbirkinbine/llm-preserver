"""Validation of the curator-supplied pull inputs.

Shared by ``pull_model`` and the ``pull --plan`` branch, so a value the
plan accepts is exactly a value the pull accepts. Split out of ``pull``
to keep that module under the size cap when spec 0025 wrapped
``pull_model`` in its repo claim.
"""

from collections.abc import Sequence
from typing import cast, get_args

from llm_preserver.hub import PullUserError
from llm_preserver.layout import split_repo_id
from llm_preserver.records import Role


def validated_base_model(base_model: str | None) -> str | None:
    """Refuse a ``--base-model`` that is not a usable repo id.

    Exactly two components, via the one validator the whole tool shares:
    the value is recorded, rendered into ``MODEL-RECORD.md``, and read
    back as a lineage pointer, so it can never be free text.

    Raises:
        PullUserError: If the claim is not an ``<owner>/<repo>`` id.
    """
    if base_model is None:
        return None
    try:
        split_repo_id(base_model)
    except ValueError as exc:
        raise PullUserError(
            f"--base-model must look like <owner>/<repo>, got {base_model!r}"
        ) from exc
    return base_model


def validated_roles(roles: Sequence[str]) -> list[Role]:
    """Validate caller-supplied role names against the record vocabulary."""
    valid = get_args(Role)
    unknown = [role for role in roles if role not in valid]
    if unknown:
        raise PullUserError(f"unknown role(s) {unknown!r}: valid roles are {', '.join(valid)}")
    return cast(list[Role], list(roles))
