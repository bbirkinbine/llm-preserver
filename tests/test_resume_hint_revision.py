"""The composers' ``revision=`` parameter (spec 0024, criteria 16-17).

A composed command must pin the resolved commit, never a moving branch:
a resumed pull that re-resolved ``4.05bpw`` could land on a different
tree. The composer accepts only a 40-hex value and composes nothing
otherwise — the same rule spec 0007 applies to a malformed repo id.

Expected red (test-first): no composer takes ``revision=`` yet, so every
call passing it is a TypeError.
"""

import shlex

import pytest

from llm_preserver.cli.resume_hint import (
    _compose_pull_command,
    compose_doc_refresh_hint,
    compose_resume_hint,
)

REPO_ID = "turboderp/tiny-exl3"
SHA = "2a30229e67012798ba9f0cd832bb78abf4c363d5"
COMPOSERS = [_compose_pull_command, compose_resume_hint, compose_doc_refresh_hint]


@pytest.mark.parametrize("compose", COMPOSERS)
def test_a_full_sha_rides_as_one_revision_flag(compose, tmp_path) -> None:
    line = compose(REPO_ID, tmp_path, include=["*.safetensors"], revision=SHA)

    assert line is not None
    assert f"--revision {SHA}" in line
    assert line.count("--revision") == 1
    # Still one shell-parsable command: the flag's value is its own token.
    tokens = shlex.split(line.split(": ", 1)[-1])
    assert tokens[tokens.index("--revision") + 1] == SHA


@pytest.mark.parametrize("compose", COMPOSERS)
@pytest.mark.parametrize("revision", ["4.05bpw", "main", "feature/q4", SHA[:7], "--yes"])
def test_a_revision_that_is_not_a_full_sha_composes_nothing(compose, tmp_path, revision) -> None:
    # Control first: the same call without the revision composes a line,
    # so a None below is the revision's doing and nothing else.
    assert compose(REPO_ID, tmp_path, include=["*.safetensors"]) is not None

    assert compose(REPO_ID, tmp_path, include=["*.safetensors"], revision=revision) is None


@pytest.mark.parametrize("compose", COMPOSERS)
def test_revision_none_leaves_the_command_unchanged(compose, tmp_path) -> None:
    without = compose(REPO_ID, tmp_path, include=["*.safetensors"], select_all=False)

    assert compose(REPO_ID, tmp_path, include=["*.safetensors"], revision=None) == without
    assert "--revision" not in (without or "")
