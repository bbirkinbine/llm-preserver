"""A flat repo of shards is a roll-up too — spec 0018, live-use round.

The directory roll-up was designed against quant repos, which lay their
quants out as directories (``UD-Q4_K_XL/``, 32 shards each). A
full-weights Hugging Face snapshot has **no directories at all**:
``Uniboshi/Kimi-K3-Abliterated-V1`` is 113 files, 96 of them
``model-NNNNN-of-NNNNNN.safetensors`` sitting at the root. Live use
walked straight into it (2026-08-12) — ``has_directories`` was False,
no roll-up appeared, and the human paged 96 identical shard rows, which
is the wall this spec exists to remove.

So a sharded weight set at the root rolls up the same way a directory
does. Two rules keep it honest:

- **Directory is the outer rule.** Shards inside ``UD-Q4_K_XL/`` stay
  under the directory; collapsing twice would hide the name the human
  types.
- **A set needs more than one member.** A lone ``-00001-of-00001`` file
  is just a file, and naming it with a glob would advertise a set that
  is not there.

The convention itself lives in ``llm_preserver.shard_sets``, shared
with the incomplete-set advisory, so the listing and the warning can
never disagree about what a shard set is. Its own padding-width
regression tests live in ``test_pull_advisory.py``.
"""

from test_cli_pull_listing_tty import ROLLUP_KEYS, info_for, run_listing, scripted
from test_pull_listing import repo_file
from test_pull_listing_rollup import group_files, rollup_lines

from llm_preserver.hub import RepoInfo

# --- a flat repo of shards is a roll-up too -----------------------------


def shard_repo(total: int = 96, width: int = 6):
    """A flat HF snapshot: shards plus the config files beside them.

    The default six-digit total is the shape
    ``Uniboshi/Kimi-K3-Abliterated-V1`` actually publishes, which is
    what made this case visible in live use.
    """
    return (
        [repo_file("README.md", 1024), repo_file("config.json", 512)]
        + [
            repo_file(f"model-{index:05d}-of-{total:0{width}d}.safetensors", 16_000_000_000)
            for index in range(1, total + 1)
        ]
        + [repo_file("model.safetensors.index.json", 57_000_000)]
    )


def test_a_flat_repo_of_shards_collapses_to_one_line():
    """The live-use gap: 113 files, zero directories, so the roll-up had
    nothing to say and the human paged 96 identical shard rows."""
    groups = group_files(shard_repo())

    names = [group.name for group in groups]
    assert names == [
        "README.md",
        "config.json",
        "model-*.safetensors",
        "model.safetensors.index.json",
    ]
    shards = groups[2]
    assert shards.is_shard_set and not shards.is_directory
    assert shards.file_count == 96
    assert shards.total_size == 96 * 16_000_000_000


def test_the_shard_line_carries_its_count_like_a_directory():
    line = rollup_lines(group_files(shard_repo()))[2]

    assert "model-*.safetensors" in line
    assert line.rstrip().endswith("96 files")
    assert not line.rstrip().endswith("/")


def test_the_index_json_beside_the_shards_is_not_swallowed():
    # model.safetensors.index.json is not a shard and must stay its own
    # row: without it the set cannot be loaded.
    lines = rollup_lines(group_files(shard_repo()))

    assert any("model.safetensors.index.json" in line for line in lines)


def test_a_lone_shard_stays_a_file_rather_than_a_one_member_glob():
    # A `-00001-of-00001` file is just a file; naming it with a glob
    # would read as a set that is not there.
    groups = group_files([repo_file("model-00001-of-00001.safetensors")])

    assert [group.name for group in groups] == ["model-00001-of-00001.safetensors"]
    assert not groups[0].is_shard_set


def test_shards_inside_a_directory_stay_under_the_directory():
    # Directory grouping is the outer rule: a quant repo's UD-Q4_K_XL/
    # already collapses, and a second collapse inside it would hide the
    # directory the human types.
    groups = group_files(
        [repo_file(f"UD-Q4_K_XL/m-{index:05d}-of-00003.gguf") for index in range(1, 4)]
    )

    assert [group.name for group in groups] == ["UD-Q4_K_XL"]
    assert groups[0].is_directory and not groups[0].is_shard_set


def test_a_shard_set_is_offered_as_the_example_verbatim():
    # It is already a glob; wrapping it in more stars would be noise.
    from llm_preserver.cli.pull_exec.listing import example_pattern, pattern_prompt

    groups = group_files(shard_repo())

    assert example_pattern(groups) == "model-*.safetensors"
    assert "e.g. model-*.safetensors or" in pattern_prompt(example_pattern(groups))


# --- the frame the roll-up decides to show ------------------------------


def shard_root_repo() -> RepoInfo:
    """113 root files, 96 of them one shard set — the live-use shape.

    ``Uniboshi/Kimi-K3-Abliterated-V1``: a full-weights snapshot has no
    directories at all, so the roll-up used to be withheld and the
    human paged 96 identical rows. Six-digit padding, as that repo
    actually publishes it.
    """
    return info_for(
        [(name, 1024) for name in ("README.md", "config.json", "tokenizer_config.json")]
        + [(f"model-{index:05d}-of-000096.safetensors", 16_000_000_000) for index in range(1, 97)]
        + [("model.safetensors.index.json", 57_000_000)]
        + [(f"extra-{index:02d}.py", 2048) for index in range(1, 14)]
    )


def test_a_flat_repo_of_shards_still_opens_on_the_rollup(monkeypatch, capsys):
    """The live-use gap, at the frame level rather than the grouper.

    ``offer_rollup`` used to read `any("/" in path)`. That was provably
    dead while directories were the only thing that collapsed, and both
    reviewers said so — but adding shard-set grouping made it live and
    wrong, and a 96-shard snapshot with no directory got the wall it
    was supposed to be spared (2026-08-12). The condition now asks
    whether the roll-up is shorter than the flat listing, which is the
    question that was always meant.
    """
    _, prompter = run_listing(monkeypatch, capsys, shard_root_repo(), scripted("*.safetensors"))

    opening = prompter.frames[0]
    assert ROLLUP_KEYS in opening
    assert "model-*.safetensors" in opening
    # The 96 shards are one line, not 96.
    assert "model-00050-of-000096.safetensors" not in opening
    assert opening.count("model-") == 1
    # The index beside them is not a shard and must keep its own row:
    # without it the set cannot be loaded.
    assert "model.safetensors.index.json" in opening


def test_the_shard_set_is_offered_as_the_prompt_example(monkeypatch, capsys):
    # It is already a glob, so it is pasteable without wrapping.
    _, prompter = run_listing(monkeypatch, capsys, shard_root_repo(), scripted("*.safetensors"))

    assert "e.g. model-*.safetensors or" in prompter.texts[0]
