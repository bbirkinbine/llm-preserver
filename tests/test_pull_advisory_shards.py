"""Sharded weight sets — spec 0005's incomplete-set advisory.

Split from ``test_pull_advisory.py`` at the 300-line rule when the
padding-width regressions landed. Everything here is pure data in, data
out; the shared ``rf`` / ``advise`` helpers come from the parent module.

The advisory these tests guard is what stands between a partial pull
and an archive that cannot load, and its failure mode is **silence**:
a filename the matcher does not recognize produces no warning at all,
which is indistinguishable from a complete selection. That is why the
padding widths are pinned by example rather than by reasoning about
the regex — ``Uniboshi/Kimi-K3-Abliterated-V1`` publishes
``-of-000096`` where both cited conventions pad to five, and every
fixture in this suite had used the documented width.
"""

from test_pull_advisory import SHARDS, advise, record_with_files, rf

# --- sharded weight sets ---------------------------------------------


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


def test_a_six_digit_shard_total_is_still_a_shard_set():
    """Padding width is a publisher's choice, and a missed set is silent.

    ``Uniboshi/Kimi-K3-Abliterated-V1`` ships
    ``model-00001-of-000096.safetensors`` — six digits in the total,
    where both cited conventions pad to five. The old ``\\d{5}`` total
    matched nothing there, so pulling a partial weight set produced no
    advisory at all: measured 9 of 96 shards selected, 0 advisories,
    against 1 for the identical selection at five-digit padding. That
    is the archive-that-cannot-load case this advisory exists to catch
    (live use, 2026-08-12).
    """
    tree = [rf(f"model-{index:05d}-of-000096.safetensors") for index in range(1, 97)]

    advisories = advise(tree, tree[:9])

    assert [a.kind for a in advisories] == ["sharded weight set"]
    assert "87 of 96 shards excluded" in advisories[0].message
    assert "model-*.safetensors" in advisories[0].message


def test_a_variable_width_set_groups_under_one_prefix_not_one_per_shard():
    """The lazy prefix is load-bearing, not a style choice.

    With a greedy ``.+`` and a variable ``\\d+`` the prefix eats the
    leading zeros, so ``model-00001-of-000096`` parses as prefix
    ``model-0000`` / index ``1`` — a distinct one-shard set per file,
    which reports nothing and is worse than no match at all.
    """
    tree = [rf(f"model-{index:05d}-of-000096.safetensors") for index in range(1, 97)]

    advisories = advise(tree, tree[:1])

    assert len(advisories) == 1
    assert "95 of 96 shards excluded" in advisories[0].message


def test_an_unpadded_shard_set_is_recognized_too():
    tree = [rf("model-1-of-2.gguf"), rf("model-2-of-2.gguf")]

    advisories = advise(tree, tree[:1])

    assert [a.kind for a in advisories] == ["sharded weight set"]


def test_a_digit_bearing_prefix_survives_the_lazy_match():
    # The lazy prefix must not stop at the first digit run it sees.
    tree = [rf(f"layer-01-model-{index:05d}-of-00003.safetensors") for index in range(1, 4)]

    advisories = advise(tree, tree[:1])

    assert "layer-01-model-*.safetensors" in advisories[0].message


def test_shard_sets_group_by_prefix():
    other = [rf("extra-00001-of-00002.bin"), rf("extra-00002-of-00002.bin")]
    tree = [*SHARDS, *other]
    selected = [*SHARDS, other[0]]  # "model" set complete, "extra" set partial

    advisories = advise(tree, selected)

    assert [a.kind for a in advisories] == ["sharded weight set"]
    assert "extra-" in advisories[0].message
    assert "model-" not in advisories[0].message
