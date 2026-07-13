"""Tests for llm_preserver.pull_advisory — companion-artifact advisories.

Spec 0005, phase 2: the pure advisory engine only (`advisories_for`
and the frozen `Advisory` dataclass). The archive-scan half
(`archived_hub_repos`) lives in test_pull_advisory_archive.py; CLI
wiring lives in test_cli_pull_advisory.py. Everything here is pure
data in, data out — no hub client, no network.
"""

import dataclasses

import pytest

from llm_preserver.hub import RepoFile
from llm_preserver.pull_advisory import Advisory, advisories_for
from llm_preserver.records import ArtifactEntry, FileEntry, ModelRecord

QUANT_REPO = "bartowski/tiny-chat-GGUF"


def rf(path: str, size: int = 100) -> RepoFile:
    return RepoFile(path=path, size=size, sha256=None)


def advise(tree, selected, record=None, **overrides):
    kwargs = {
        "repo_id": QUANT_REPO,
        "base_model": None,
        "adapter_base": None,
        "archived_repos": frozenset(),
    }
    kwargs.update(overrides)
    return advisories_for(tree, selected, record, **kwargs)


def record_with_files(*paths: str) -> ModelRecord:
    """Minimal valid record whose artifact carries the given file paths."""
    return ModelRecord(
        name="tiny-chat",
        hub_id="acme/tiny-chat",
        artifacts=[
            ArtifactEntry(
                format="gguf",
                provenance="verified",
                files=[FileEntry(path=path, source="original") for path in paths],
            )
        ],
    )


# --- same-repo companion rows ---------------------------------------


def test_excluded_mmproj_triggers_vision_projector_advisory():
    # The gemma incident (2026-07-12): a Q4 pull silently omitted the
    # vision projector the tree shipped.
    tree = [rf("gemma-tiny-Q4_K_M.gguf"), rf("mmproj-F16.gguf"), rf("README.md")]
    selected = [tree[0], tree[2]]  # docs ride along in the selection

    advisories = advise(tree, selected)

    assert len(advisories) == 1
    advisory = advisories[0]
    assert advisory.kind == "vision projector"
    assert "mmproj-F16.gguf" in advisory.message
    assert "--include" in advisory.message
    assert "mmproj" in advisory.message.split("--include", 1)[1]  # remedy pattern


def test_selected_mmproj_produces_no_advisory():
    tree = [rf("gemma-tiny-Q4_K_M.gguf"), rf("mmproj-F16.gguf"), rf("README.md")]

    assert advise(tree, tree) == []


def test_excluded_mtp_file_triggers_speculative_decoding_head_advisory():
    tree = [rf("tiny-chat-Q4_K_M.gguf"), rf("mtp-F16.gguf")]

    advisories = advise(tree, [tree[0]])

    assert [a.kind for a in advisories] == ["speculative-decoding head"]
    assert "mtp-F16.gguf" in advisories[0].message
    assert "--include" in advisories[0].message


def test_excluded_imatrix_file_triggers_calibration_data_advisory():
    tree = [rf("tiny-chat-Q4_K_M.gguf"), rf("tiny-chat.imatrix")]

    advisories = advise(tree, [tree[0]])

    assert [a.kind for a in advisories] == ["quantization calibration data"]
    assert "tiny-chat.imatrix" in advisories[0].message
    assert "--include" in advisories[0].message


def test_companion_pattern_matches_on_basename_of_nested_path():
    tree = [rf("tiny-chat-Q4_K_M.gguf"), rf("extras/mmproj-F16.gguf")]

    advisories = advise(tree, [tree[0]])

    assert [a.kind for a in advisories] == ["vision projector"]


def test_companion_already_in_record_produces_no_advisory():
    # Archive-aware: an advisory means "you are missing this", and a
    # projector archived by an earlier pull is not missing.
    tree = [rf("gemma-tiny-Q4_K_M.gguf"), rf("mmproj-F16.gguf"), rf("README.md")]
    selected = [tree[0], tree[2]]
    record = record_with_files("gguf/mmproj-F16.gguf")

    assert advise(tree, selected, record) == []


# --- sharded weight sets ---------------------------------------------


SHARDS = [
    rf("model-00001-of-00003.safetensors"),
    rf("model-00002-of-00003.safetensors"),
    rf("model-00003-of-00003.safetensors"),
]


def test_partial_shard_selection_triggers_incomplete_set_advisory():
    tree = [*SHARDS, rf("README.md")]
    selected = [SHARDS[0], tree[3]]  # one of three shards

    advisories = advise(tree, selected)

    assert [a.kind for a in advisories] == ["sharded weight set"]
    assert "2" in advisories[0].message  # names the count missing
    assert "--include" in advisories[0].message


def test_selecting_no_shards_of_a_set_produces_no_advisory():
    # Zero of the set is a deliberate exclusion, not an incomplete set.
    tree = [*SHARDS, rf("tiny-chat-Q4_K_M.gguf")]

    assert advise(tree, [tree[3]]) == []


def test_selecting_all_shards_of_a_set_produces_no_advisory():
    tree = [*SHARDS, rf("README.md")]

    assert advise(tree, tree) == []


def test_shards_already_in_record_count_as_covered():
    tree = [*SHARDS]
    selected = [SHARDS[0], SHARDS[1]]
    record = record_with_files("hf-snapshot/model-00003-of-00003.safetensors")

    assert advise(tree, selected, record) == []


def test_shard_sets_group_by_prefix():
    other = [rf("extra-00001-of-00002.bin"), rf("extra-00002-of-00002.bin")]
    tree = [*SHARDS, *other]
    selected = [*SHARDS, other[0]]  # "model" set complete, "extra" set partial

    advisories = advise(tree, selected)

    assert [a.kind for a in advisories] == ["sharded weight set"]
    assert "extra-" in advisories[0].message
    assert "model-" not in advisories[0].message


# --- cross-repo rows --------------------------------------------------


def test_unarchived_adapter_base_triggers_follow_up_pull_advisory():
    tree = [rf("adapter_model.safetensors"), rf("adapter_config.json")]

    advisories = advise(tree, tree, adapter_base="acme/base-7b")

    assert [a.kind for a in advisories] == ["adapter base model"]
    assert "llm-preserver pull acme/base-7b" in advisories[0].message


def test_archived_adapter_base_produces_no_advisory():
    tree = [rf("adapter_model.safetensors"), rf("adapter_config.json")]

    advisories = advise(
        tree, tree, adapter_base="acme/base-7b", archived_repos=frozenset({"acme/base-7b"})
    )

    assert advisories == []


def test_unarchived_base_model_triggers_whole_repo_pull_advisory():
    tree = [rf("Qwen3-0.6B-Q4_K_M.gguf")]

    advisories = advise(tree, tree, repo_id="unsloth/Qwen3-0.6B-GGUF", base_model="Qwen/Qwen3-0.6B")

    assert [a.kind for a in advisories] == ["full-precision master"]
    assert "llm-preserver pull Qwen/Qwen3-0.6B --whole-repo" in advisories[0].message


def test_archived_base_model_produces_no_advisory():
    tree = [rf("Qwen3-0.6B-Q4_K_M.gguf")]

    advisories = advise(
        tree,
        tree,
        repo_id="unsloth/Qwen3-0.6B-GGUF",
        base_model="Qwen/Qwen3-0.6B",
        archived_repos=frozenset({"Qwen/Qwen3-0.6B"}),
    )

    assert advisories == []


def test_repo_without_base_model_metadata_produces_no_advisory():
    tree = [rf("Qwen3-0.6B-Q4_K_M.gguf")]

    assert advise(tree, tree, repo_id="unsloth/Qwen3-0.6B-GGUF", base_model=None) == []


def test_base_model_equal_to_repo_id_produces_no_advisory():
    # An original repo declaring itself is not a cross-repo dependency.
    tree = [rf("model.safetensors")]

    advisories = advise(tree, tree, repo_id="Qwen/Qwen3-0.6B", base_model="Qwen/Qwen3-0.6B")

    assert advisories == []


# --- purity, determinism, ordering ------------------------------------


def test_advisories_for_never_mutates_its_inputs():
    tree = [rf("tiny-chat-Q4_K_M.gguf"), rf("mmproj-F16.gguf"), *SHARDS]
    selected = [tree[0], SHARDS[0]]
    tree_before, selected_before = list(tree), list(selected)

    advise(tree, selected, base_model="acme/tiny-chat")

    assert tree == tree_before
    assert selected == selected_before


def test_advisories_are_deterministic_across_calls():
    tree = [rf("tiny-chat-Q4_K_M.gguf"), rf("mmproj-F16.gguf"), *SHARDS]
    selected = [tree[0], SHARDS[0]]

    first = advise(tree, selected, base_model="acme/tiny-chat", adapter_base="acme/base-7b")
    second = advise(tree, selected, base_model="acme/tiny-chat", adapter_base="acme/base-7b")

    assert first == second
    assert first != []


def test_advisory_order_is_tree_order_then_shard_sets_then_cross_repo():
    tree = [
        rf("tiny-chat.imatrix"),  # same-repo row, first in tree
        rf("mmproj-F16.gguf"),  # same-repo row, second in tree
        *SHARDS,
        rf("README.md"),
    ]
    selected = [SHARDS[0], tree[5]]

    kinds = [
        a.kind
        for a in advise(tree, selected, base_model="acme/tiny-chat", adapter_base="acme/base-7b")
    ]

    assert kinds[:2] == ["quantization calibration data", "vision projector"]
    assert kinds[2] == "sharded weight set"
    assert set(kinds[3:]) == {"adapter base model", "full-precision master"}
    assert len(kinds) == 5


def test_advisory_is_an_immutable_value():
    advisory = Advisory(kind="vision projector", message="add --include 'mmproj-*'")

    with pytest.raises(dataclasses.FrozenInstanceError):
        advisory.kind = "other"  # type: ignore[misc]


# --- remedy patterns ---------------------------------------------------


def test_shard_remedy_pattern_matches_nested_paths():
    # select_files fnmatches full repo paths anchored at the start, so
    # the remedy must lead with * or it silently selects nothing for
    # shard sets living in a subdirectory.
    nested = [
        rf("weights/model-00001-of-00002.safetensors"),
        rf("weights/model-00002-of-00002.safetensors"),
    ]
    tree = [*nested, rf("tokenizer.json")]

    advisories = advise(tree, [nested[0], tree[2]])

    [advisory] = advisories
    remedy = advisory.message.split("--include", 1)[1]
    assert "'*model-" in remedy
