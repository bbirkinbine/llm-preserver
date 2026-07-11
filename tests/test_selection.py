"""Tests for llm_preserver.selection — file selection for pull.

Pins the spec-0003 plan seam:

- ``select_files(files, include)`` — repeatable fnmatch ``--include``
  patterns over ``RepoFile`` paths, docs always included.
- ``is_doc_file(path)`` — README / model card / LICENSE detection.
- ``selects_all_weights(files, selected)`` — the every-weight pull
  that requires explicit confirmation.
- ``infer_format_subdir(paths, source_repo_id)`` — ADR 0001 format
  subdirectory: ``.gguf`` → gguf; ``mlx-community/*`` source repo →
  mlx; else hf-snapshot.
- ``checked_target_path(..., relocate_docs=True)`` — spec 0004: False
  keeps docs at their in-tree paths (snapshot tree fidelity); path
  safety applies either way.
"""

import pytest

import llm_preserver.selection as selection
from llm_preserver.hub import PullUserError, RepoFile

SHA = "0" * 64


def repo_files():
    return [
        RepoFile(path="tiny-chat-Q4_K_M.gguf", size=100, sha256=SHA),
        RepoFile(path="tiny-chat-Q8_0.gguf", size=200, sha256=SHA),
        RepoFile(path="README.md", size=10, sha256=None),
        RepoFile(path="LICENSE", size=5, sha256=None),
        RepoFile(path="config.json", size=20, sha256=None),
    ]


def selected_paths(files, include):
    return {f.path for f in selection.select_files(files, include)}


def test_include_pattern_selects_matching_files_only():
    paths = selected_paths(repo_files(), ["*Q4_K_M*"])
    assert "tiny-chat-Q4_K_M.gguf" in paths
    assert "tiny-chat-Q8_0.gguf" not in paths
    assert "config.json" not in paths


def test_include_patterns_are_repeatable_and_union():
    paths = selected_paths(repo_files(), ["*Q4_K_M*", "*.json"])
    assert {"tiny-chat-Q4_K_M.gguf", "config.json"} <= paths
    assert "tiny-chat-Q8_0.gguf" not in paths


def test_readme_and_license_always_included_without_matching_pattern():
    paths = selected_paths(repo_files(), ["*Q4_K_M*"])
    assert {"README.md", "LICENSE"} <= paths


def test_is_doc_file_recognizes_readme_and_license():
    assert selection.is_doc_file("README.md")
    assert selection.is_doc_file("LICENSE")


def test_is_doc_file_recognizes_use_policy():
    # Meta-Llama repos ship license terms in USE_POLICY.md; the
    # license-completeness guarantee covers them (spec 0003).
    assert selection.is_doc_file("USE_POLICY.md")
    assert selection.is_doc_file("USE_POLICY.txt")


def test_is_doc_file_rejects_weights_and_config():
    assert not selection.is_doc_file("tiny-chat-Q4_K_M.gguf")
    assert not selection.is_doc_file("config.json")


def test_selecting_every_weight_is_flagged_for_confirmation():
    files = repo_files()
    weights = [f for f in files if f.path.endswith(".gguf")]
    assert selection.selects_all_weights(files, weights)


def test_selecting_one_of_many_weights_needs_no_confirmation():
    files = repo_files()
    one_quant = [f for f in files if "Q4_K_M" in f.path]
    assert not selection.selects_all_weights(files, one_quant)


def test_gguf_selection_infers_gguf_subdir():
    subdir = selection.infer_format_subdir(["tiny-chat-Q4_K_M.gguf"], "bartowski/tiny-chat-GGUF")
    assert subdir == "gguf"


def test_mlx_community_repo_infers_mlx_subdir():
    subdir = selection.infer_format_subdir(["model.safetensors"], "mlx-community/tiny-chat-4bit")
    assert subdir == "mlx"


def test_other_repo_defaults_to_hf_snapshot_subdir():
    subdir = selection.infer_format_subdir(["model.safetensors", "config.json"], "acme/tiny-chat")
    assert subdir == "hf-snapshot"


def test_gguf_extension_wins_over_mlx_repo_namespace():
    # Rule order from the plan: .gguf first, then mlx-community/*.
    assert selection.infer_format_subdir(["tiny.gguf"], "mlx-community/tiny-gguf") == "gguf"


def test_checked_target_path_relocates_docs_by_default():
    # Regression pin (spec 0003 behavior, unchanged default).
    target = selection.checked_target_path("gguf", "bartowski/tiny-chat-GGUF", "README.md")
    assert target == "gguf/docs/bartowski--tiny-chat-GGUF/README.md"


def test_relocate_docs_false_keeps_docs_at_their_in_tree_path():
    target = selection.checked_target_path(
        "hf-snapshot", "acme/tiny-orig", "README.md", relocate_docs=False
    )
    assert target == "hf-snapshot/README.md"


def test_relocate_docs_false_preserves_nested_doc_paths_verbatim():
    target = selection.checked_target_path(
        "hf-snapshot", "acme/tiny-orig", "docs/LICENSE", relocate_docs=False
    )
    assert target == "hf-snapshot/docs/LICENSE"


def test_relocate_docs_false_still_rejects_unsafe_hub_paths():
    with pytest.raises(PullUserError):
        selection.checked_target_path(
            "hf-snapshot", "acme/tiny-orig", "../escape.md", relocate_docs=False
        )
