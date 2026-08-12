"""Tests for llm_preserver.layout — spec 0017 pass 1 (ADR 0003).

``layout`` is the one place that turns a Hugging Face repo id into
archive paths and back, and the one place that judges whether a model
directory obeys the ADR-0003 three-way invariant:

    directory path == record.hub_id == every artifact's source_repo

Expected red (test-first): ``llm_preserver.layout`` does not exist, so
each test raises ModuleNotFoundError. The imports live inside test
bodies (the test_verify.py convention) so collection of the rest of the
suite never depends on the module existing.

Records here are built with *explicit* ``hub_id`` and ``source_repo``
values on purpose. ``sample_record_dict``'s default is now the
*migrated* shape (source follows hub id), so a layout test leaning on
the default would assert whatever the fixture happens to encode rather
than the case it names — in either direction.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from llm_preserver.records import ModelRecord

# Written out rather than imported from the module under test: the URL
# shape is what real records already carry (pull_record.py), so the test
# must state it independently instead of asking the code to agree with
# itself.
OWN_REPO = "acme/tiny-chat"
OWN_URL = "https://huggingface.co/acme/tiny-chat"
FOREIGN_REPO = "other/tiny-chat-GGUF"
FOREIGN_URL = "https://huggingface.co/other/tiny-chat-GGUF"

NOT_TWO_COMPONENTS = [
    "",
    "noslash",
    "acme/tiny-chat/extra",
    "acme/",
    "/tiny-chat",
    "acme//tiny-chat",
    "../etc",
    "acme/..",
    "acme/tiny chat",
    "/absolute/path",
]


def record_with(
    sample_record_dict: Callable[..., dict],
    *,
    hub_id: str,
    source_repos: list[str | None],
) -> ModelRecord:
    """A validated record with an explicit hub_id and per-artifact sources.

    One artifact per entry in ``source_repos``; ``None`` means the
    artifact records no source repo at all (a pre-v2 import).
    """
    raw = sample_record_dict(hub_id=hub_id)
    template = raw["artifacts"][0]
    raw["artifacts"] = [{**template, "source_repo": url} for url in source_repos]
    return ModelRecord.model_validate(raw)


# --- path derivation: models/ and .staging/ mirror the repo id ---


def test_model_dir_is_owner_then_repo_under_models(tmp_path: Path) -> None:
    from llm_preserver.layout import model_dir_for

    assert model_dir_for(tmp_path, "unsloth/Muse-Glimmer-30B-GGUF") == (
        tmp_path / "models" / "unsloth" / "Muse-Glimmer-30B-GGUF"
    )


def test_staging_dir_mirrors_the_model_path_under_staging(tmp_path: Path) -> None:
    from llm_preserver.layout import staging_dir_for

    assert staging_dir_for(tmp_path, "unsloth/Muse-Glimmer-30B-GGUF") == (
        tmp_path / ".staging" / "unsloth" / "Muse-Glimmer-30B-GGUF"
    )


@pytest.mark.parametrize("repo_id", NOT_TWO_COMPONENTS)
def test_model_dir_refuses_an_id_that_is_not_two_valid_components(
    tmp_path: Path, repo_id: str
) -> None:
    # No path is ever built from an id the component pattern rejects:
    # a repo id is untrusted input and could otherwise address files
    # outside models/ (the 0009 / 0010 posture).
    from llm_preserver.layout import model_dir_for

    with pytest.raises(ValueError):
        model_dir_for(tmp_path, repo_id)


@pytest.mark.parametrize("repo_id", NOT_TWO_COMPONENTS)
def test_staging_dir_refuses_an_id_that_is_not_two_valid_components(
    tmp_path: Path, repo_id: str
) -> None:
    from llm_preserver.layout import staging_dir_for

    with pytest.raises(ValueError):
        staging_dir_for(tmp_path, repo_id)


def test_split_repo_id_returns_the_owner_and_the_repo() -> None:
    from llm_preserver.layout import split_repo_id

    assert split_repo_id("unsloth/Muse-Glimmer-30B-GGUF") == ("unsloth", "Muse-Glimmer-30B-GGUF")


@pytest.mark.parametrize("repo_id", NOT_TWO_COMPONENTS)
def test_split_repo_id_rejects_ids_that_are_not_two_valid_components(repo_id: str) -> None:
    from llm_preserver.layout import split_repo_id

    with pytest.raises(ValueError):
        split_repo_id(repo_id)


# --- source_repo URL <-> repo id ---


def test_source_repo_url_is_the_hub_url_of_the_repo_id() -> None:
    from llm_preserver.layout import source_repo_url

    assert source_repo_url(OWN_REPO) == OWN_URL


def test_source_repo_url_round_trips_back_to_the_repo_id() -> None:
    from llm_preserver.layout import repo_id_from_url, source_repo_url

    assert repo_id_from_url(source_repo_url(FOREIGN_REPO)) == FOREIGN_REPO


def test_repo_id_is_read_back_out_of_a_recorded_source_repo() -> None:
    # The direction migrate and verify use: a URL that a record
    # already carries, parsed without help from source_repo_url.
    from llm_preserver.layout import repo_id_from_url

    assert repo_id_from_url(FOREIGN_URL) == FOREIGN_REPO


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "https://example.invalid/acme/tiny-chat",
        "https://huggingface.co/acme",
        "https://huggingface.co/acme/tiny-chat/tree/main",
        "https://huggingface.co/",
        "acme/tiny-chat",
    ],
)
def test_url_that_is_not_a_two_component_hub_repo_has_no_repo_id(url: str | None) -> None:
    # Reading a record is a read path over untrusted bytes: an
    # unparseable source_repo yields "no id", never a crash and never a
    # half-parsed path component.
    from llm_preserver.layout import repo_id_from_url

    assert repo_id_from_url(url) is None


# --- layout_state: the three-way invariant, per directory ---


def test_layout_is_ok_when_path_hub_id_and_source_repo_all_agree(
    sample_record_dict: Callable[..., dict],
) -> None:
    from llm_preserver.layout import layout_state

    record = record_with(sample_record_dict, hub_id=OWN_REPO, source_repos=[OWN_URL])

    assert layout_state(OWN_REPO, record).state == "ok"


def test_foreign_source_repo_makes_the_directory_unmigrated(
    sample_record_dict: Callable[..., dict],
) -> None:
    from llm_preserver.layout import layout_state

    record = record_with(sample_record_dict, hub_id=OWN_REPO, source_repos=[FOREIGN_URL])

    assert layout_state(OWN_REPO, record).state == "unmigrated"


def test_unmigrated_verdict_names_the_offending_source_repo(
    sample_record_dict: Callable[..., dict],
) -> None:
    # The repo id, not the URL: it is what the human types into the
    # migrate / pull command the remedy names.
    from llm_preserver.layout import layout_state

    record = record_with(sample_record_dict, hub_id=OWN_REPO, source_repos=[FOREIGN_URL])

    assert layout_state(OWN_REPO, record).offending_repo == FOREIGN_REPO


def test_hub_id_disagreeing_with_the_directory_is_unmigrated(
    sample_record_dict: Callable[..., dict],
) -> None:
    # Third leg of the invariant: the record's own id must match the
    # path it sits at, even when every artifact agrees with the record.
    from llm_preserver.layout import layout_state

    record = record_with(sample_record_dict, hub_id=FOREIGN_REPO, source_repos=[FOREIGN_URL])

    verdict = layout_state(OWN_REPO, record)

    assert (verdict.state, verdict.offending_repo) == ("unmigrated", FOREIGN_REPO)


def test_hub_id_alone_convicts_when_every_artifact_matches_the_path(
    sample_record_dict: Callable[..., dict],
) -> None:
    # The discriminating case for the hub_id leg. The sibling test above
    # sets hub_id *and* the source foreign, so the artifact loop returns
    # the same verdict and the leg is never exercised — deleting it left
    # the whole suite green (mutation-proved, 2026-08-11). Here every
    # artifact agrees with the directory and only the record's own id
    # disagrees, so nothing but the leg can produce the verdict.
    from llm_preserver.layout import layout_state

    record = record_with(sample_record_dict, hub_id="beta/other", source_repos=[OWN_URL])

    verdict = layout_state(OWN_REPO, record)

    assert (verdict.state, verdict.offending_repo) == ("unmigrated", "beta/other")


def test_unusable_hub_id_still_convicts_but_names_nobody(
    sample_record_dict: Callable[..., dict],
) -> None:
    # hub_id has no validator on the model and this value reaches
    # printed output, so a record carrying argv-ish text must not put it
    # on screen — the directory is still unmigrated, there is just no id
    # worth naming (0007 / 0010 / 0013 class).
    from llm_preserver.layout import layout_state

    record = record_with(
        sample_record_dict,
        hub_id="--yes  https://evil.example/pwn ; rm -rf ~",
        source_repos=[OWN_URL],
    )

    verdict = layout_state(OWN_REPO, record)

    assert (verdict.state, verdict.offending_repo) == ("unmigrated", None)


def test_ok_verdict_names_no_offending_repo(
    sample_record_dict: Callable[..., dict],
) -> None:
    from llm_preserver.layout import layout_state

    record = record_with(sample_record_dict, hub_id=OWN_REPO, source_repos=[OWN_URL, OWN_URL])

    assert layout_state(OWN_REPO, record).offending_repo is None


def test_null_source_repo_cannot_make_a_directory_unmigrated(
    sample_record_dict: Callable[..., dict],
) -> None:
    # A missing source_repo carries no contradiction — it says nothing
    # about where the files belong, so it cannot convict the directory.
    # (migrate, in pass 2, refuses on it: moving files needs a claim,
    # judging a path does not.)
    from llm_preserver.layout import layout_state

    record = record_with(sample_record_dict, hub_id=OWN_REPO, source_repos=[None])

    assert layout_state(OWN_REPO, record).state == "ok"


def test_null_source_repo_is_skipped_while_a_foreign_sibling_still_offends(
    sample_record_dict: Callable[..., dict],
) -> None:
    from llm_preserver.layout import layout_state

    record = record_with(sample_record_dict, hub_id=OWN_REPO, source_repos=[None, FOREIGN_URL])

    verdict = layout_state(OWN_REPO, record)

    assert (verdict.state, verdict.offending_repo) == ("unmigrated", FOREIGN_REPO)


def test_a_record_with_no_artifacts_is_ok(
    sample_record_dict: Callable[..., dict],
) -> None:
    # Nothing to contradict the path: an artifact-less record (a
    # freshly created directory) is not a layout problem.
    from llm_preserver.layout import layout_state

    record = record_with(sample_record_dict, hub_id=OWN_REPO, source_repos=[])

    assert layout_state(OWN_REPO, record).state == "ok"
