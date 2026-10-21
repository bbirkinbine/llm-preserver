"""The shared revision helpers in ``llm_preserver.revision`` (spec 0024).

Three helpers, one rule each: ``is_full_sha`` (the 40-hex test the
client, the composers, and the display share), ``revision_line`` (the
one resolution line, plan and real pull alike), and ``fetch_repo_info``
(the one metadata call, positional when no revision was requested so
seam doubles taking ``repo_info(self, repo_id)`` keep working).

Expected red (test-first): the module does not exist yet, so each test
fails on its import. Imports are inside test bodies so that is a
per-test failure rather than a collection error for the file.
"""

import pytest

SHA = "2a30229e67012798ba9f0cd832bb78abf4c363d5"


@pytest.mark.parametrize("value", [SHA, "a" * 40, SHA.upper()])
def test_forty_hex_characters_are_a_full_sha(value: str) -> None:
    from llm_preserver.revision import is_full_sha

    assert is_full_sha(value) is True


@pytest.mark.parametrize(
    "value",
    ["", "main", "4.05bpw", SHA[:7], SHA[:-1], SHA + "0", "g" * 40, f" {SHA}", f"{SHA}\n"],
)
def test_anything_else_is_not_a_full_sha(value: str) -> None:
    from llm_preserver.revision import is_full_sha

    assert is_full_sha(value) is False


def test_a_ref_that_differs_from_its_commit_names_both() -> None:
    from llm_preserver.revision import revision_line

    assert revision_line("4.05bpw", SHA) == f"revision: 4.05bpw resolves to commit {SHA}"


def test_a_request_that_is_the_commit_names_the_commit_alone() -> None:
    from llm_preserver.revision import revision_line

    assert revision_line(SHA, SHA) == f"revision: commit {SHA}"


def test_an_uppercase_request_for_the_commit_names_the_commit_alone() -> None:
    # Plan decision 4: compared case-insensitively; the hub's lowercase
    # form is what prints.
    from llm_preserver.revision import revision_line

    assert revision_line(SHA.upper(), SHA) == f"revision: commit {SHA}"


def test_the_requested_ref_is_scrubbed_of_control_characters() -> None:
    # Criterion 15: user-typed, but terminal hygiene is uniform.
    from llm_preserver.revision import revision_line

    line = revision_line("evil\x1b[2J\nforged line", SHA)

    assert "\x1b" not in line
    assert "\n" not in line
    assert SHA in line


class PositionalOnlyClient:
    """A seam double predating spec 0024: no revision parameter at all."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def repo_info(self, repo_id: str) -> str:
        self.calls.append(repo_id)
        return "info"


def test_fetch_without_a_revision_makes_the_positional_call() -> None:
    # Criterion 2: a double that cannot accept revision= still works.
    from llm_preserver.revision import fetch_repo_info

    client = PositionalOnlyClient()

    assert fetch_repo_info(client, "acme/tiny", None) == "info"
    assert client.calls == ["acme/tiny"]


def test_fetch_with_a_revision_passes_it_to_the_seam(fake_hub_factory) -> None:
    from llm_preserver.revision import fetch_repo_info

    client = fake_hub_factory()

    info = fetch_repo_info(client, "bartowski/tiny-chat-GGUF", "4.05bpw")

    assert client.repo_info_revisions == ["4.05bpw"]
    assert info.commit == client.commit
