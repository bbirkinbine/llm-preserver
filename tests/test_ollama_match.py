"""Pure matching logic — spec 0013 phase A.

``match_gguf_files`` states byte-identity facts: a hub GGUF file
matches iff its hub-declared SHA256 equals a local Ollama model-layer
digest (case-insensitive hex compare). No ranking, no re-ordering —
verdicts come back in the hub's file order. ``compose_pull_command``
builds the pasteable archive command under the 0007 rules: repo id
validated at composition, a filename scrubbing would alter gets no
command, glob metacharacters escaped so the fnmatch ``--include``
matches exactly the matched file, and the result shell-quoted so it
stays a single argv token.
"""

import fnmatch
import shlex

import pytest

from llm_preserver.hub import RepoFile
from llm_preserver.ollama_match import GgufVerdict, compose_pull_command, match_gguf_files

LOCAL_DIGEST = "ab" * 32
OTHER_DIGEST = "cd" * 32


def repo_file(path: str, size: int | None = 100, sha256: str | None = None) -> RepoFile:
    return RepoFile(path=path, size=size, sha256=sha256)


# --- match_gguf_files -----------------------------------------------------


def test_considers_only_gguf_files_case_insensitively_in_hub_order():
    files = [
        repo_file("README.md"),
        repo_file("b-model.GGUF", sha256=OTHER_DIGEST),
        repo_file("config.json"),
        repo_file("a-model.gguf", sha256=OTHER_DIGEST),
    ]

    verdicts = match_gguf_files([LOCAL_DIGEST], files)

    assert [v.path for v in verdicts] == ["b-model.GGUF", "a-model.gguf"]


def test_marks_file_matched_when_sha256_equals_a_local_digest():
    verdicts = match_gguf_files([LOCAL_DIGEST], [repo_file("m.gguf", sha256=LOCAL_DIGEST)])

    assert verdicts == [GgufVerdict(path="m.gguf", size=100, matched=True)]


def test_hex_comparison_is_case_insensitive():
    verdicts = match_gguf_files([LOCAL_DIGEST.upper()], [repo_file("m.gguf", sha256=LOCAL_DIGEST)])

    assert verdicts[0].matched is True


def test_file_without_hub_hash_never_matches():
    verdicts = match_gguf_files([LOCAL_DIGEST], [repo_file("m.gguf", sha256=None)])

    assert verdicts == [GgufVerdict(path="m.gguf", size=100, matched=False)]


def test_different_digest_is_unmatched_and_keeps_the_size():
    verdicts = match_gguf_files([LOCAL_DIGEST], [repo_file("m.gguf", size=42, sha256=OTHER_DIGEST)])

    assert verdicts == [GgufVerdict(path="m.gguf", size=42, matched=False)]


def test_verdict_is_immutable():
    verdict = GgufVerdict(path="m.gguf", size=1, matched=False)
    with pytest.raises(Exception):  # noqa: B017 — FrozenInstanceError is a TypeError subclass detail
        verdict.matched = True  # type: ignore[misc]


# --- compose_pull_command ---------------------------------------------------


def test_composed_command_names_repo_and_include_filename():
    cmd = compose_pull_command("gpustack/bge-m3-GGUF", "bge-m3-FP16.gguf")

    assert cmd is not None
    assert shlex.split(cmd) == [
        "llm-preserver",
        "pull",
        "gpustack/bge-m3-GGUF",
        "--include",
        "bge-m3-FP16.gguf",
    ]


def test_filename_with_quote_and_space_stays_one_shell_token():
    cmd = compose_pull_command("gpustack/bge-m3-GGUF", "weird 'name.gguf")

    assert cmd is not None
    assert shlex.split(cmd) == [
        "llm-preserver",
        "pull",
        "gpustack/bge-m3-GGUF",
        "--include",
        "weird 'name.gguf",
    ]


def test_control_chars_in_filename_suppress_the_command():
    # A scrubbed pattern would not match the real hub file, so a
    # command is worse than no command (adversarial review 2026-07-31);
    # the verdict line still reports the match.
    hostile = "evil\x1b[31m.gguf"

    assert compose_pull_command("org/repo", hostile) is None


def test_glob_metacharacters_in_filename_still_match_that_file():
    # --include is an fnmatch pattern: the printed command must archive
    # exactly the matched file, so metacharacters get escaped.
    tricky = "bge-m3-[v2].gguf"

    cmd = compose_pull_command("org/repo", tricky)

    assert cmd is not None
    pattern = shlex.split(cmd)[-1]
    assert fnmatch.fnmatch(tricky, pattern)
    assert not fnmatch.fnmatch("bge-m3-v.gguf", pattern)  # class not live


@pytest.mark.parametrize("repo_id", ["--yes", "nonsense", ""])
def test_returns_none_when_repo_id_is_not_a_hub_id(repo_id):
    # 0007 rule: never compose a pasteable command around a token that
    # is not shaped like an <org>/<name> hub repo id — quoting cannot
    # defuse an argv token like "--yes".
    assert compose_pull_command(repo_id, "file.gguf") is None
