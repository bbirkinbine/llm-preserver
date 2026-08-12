"""The roll-up lines, the fit predicate, and hostile hub text — spec 0018.

Continues ``test_pull_listing.py``, whose helpers these tests import.
Same posture: data in, lines out, no terminal and no ``typer``.

The roll-up is the frame an overflowing repo opens on, so its line shape
is what decides whether the human can read the answer to the only
question this stage asks — which glob to type. Two things that shape
must never do, both found by the review round on real 40-character
directory names:

- **Truncate the name.** A truncated directory name is not a pattern
  anyone can type.
- **Run the name flush into its own count.** ``AAAA…AAAA/7 files`` is
  technically complete and unreadable, and the assertions that only
  check "the name is in the line" and "the line ends with the count" are
  both satisfied by it. So the gap is asserted, not implied.

The count column is therefore adaptive and *listing-wide*: one long name
widens the column for every line, so the counts stay in one column
instead of stair-stepping. Both properties are pinned, because they fail
to different mutations — dropping the gap keeps the alignment, and
computing the pad per line keeps the gap.

The physical-line arithmetic ``fits`` spends lives in
``llm_preserver.text_window`` and is tested there; what is pinned here
is that ``fits`` charges a wrapped line every row it occupies.
"""

from test_pull_listing import HOSTILE, VISION_KIND, repo_file

from llm_preserver.cli.pull_exec.listing import (
    ListingGroup,
    fits,
    flat_lines,
    group_files,
    rollup_lines,
)
from llm_preserver.pull_preflight import human_size

# Longer than the 26-column default pad, in the shape unsloth actually
# publishes (a quant name with a build suffix).
LONG_DIR = "UD-Q4_K_XL-experimental-recompute-imatrix-2609"


def directory_group(name: str, count: int, size: int = 1024) -> ListingGroup:
    """A directory roll-up entry with all its member sizes known."""
    return ListingGroup(
        name=name,
        is_directory=True,
        file_count=count,
        total_size=size,
        has_unknown_size=False,
    )


# --- the roll-up lines --------------------------------------------------


def test_a_directory_line_pads_the_name_and_trails_the_file_count():
    group = directory_group("UD-Q4_K_XL", 21, size=19851335840)

    assert rollup_lines([group]) == [
        f"  {human_size(19851335840):>10}  {'UD-Q4_K_XL/':<26}21 files"
    ]


def test_a_directory_name_past_the_pad_survives_instead_of_being_truncated():
    # A truncated directory name is not a typeable pattern, so the count
    # moves right rather than the name losing characters.
    assert len(LONG_DIR) > 26

    line = rollup_lines([directory_group(LONG_DIR, 7)])[0]

    assert f"{LONG_DIR}/" in line
    assert line.endswith("7 files")
    assert "…" not in line and "..." not in line


def test_a_name_past_the_pad_keeps_a_gap_before_its_own_count():
    # Without the adaptive pad the line reads "…-2609/7 files": complete,
    # unreadable, and passing every assertion above.
    line = rollup_lines([directory_group(LONG_DIR, 7)])[0]

    after_name = line.split(f"{LONG_DIR}/", 1)[1]
    assert after_name.startswith("  "), f"the count ran into the name: {line!r}"
    assert after_name.strip() == "7 files"


def test_one_long_name_widens_the_count_column_for_the_whole_listing():
    # The pad is a property of the listing, not of each line, or the
    # counts stair-step down the frame.
    short_line, long_line = rollup_lines([directory_group("q4", 3), directory_group(LONG_DIR, 7)])

    assert short_line.index("3 files") == long_line.index("7 files")


def test_a_directory_holding_one_file_counts_it_in_the_singular():
    line = rollup_lines([directory_group("mmproj", 1)])[0]

    assert line.endswith("1 file")
    assert "1 files" not in line


def test_a_directory_line_carries_no_kind_note():
    # Spec non-goal: COMPANION_RULES classifies filenames, not
    # directories — inferring a directory's kind from its members is the
    # tool judgment the 0000 invariant excludes.
    group = directory_group("mmproj-parts", 3, size=4096)

    assert VISION_KIND not in rollup_lines([group])[0]


def test_a_root_file_line_is_identical_to_its_flat_line():
    files = [repo_file("mmproj-F16.gguf", size=4096)]

    assert rollup_lines(group_files(files)) == flat_lines(files)


def test_a_size_less_root_file_still_renders_a_question_mark_in_the_rollup():
    files = [repo_file("mystery.bin", size=None)]

    assert rollup_lines(group_files(files)) == [f"  {'?':>10}  mystery.bin"]


def test_the_rollup_prints_one_line_per_group():
    files = [repo_file("a/1.gguf"), repo_file("a/2.gguf"), repo_file("README.md")]

    groups = group_files(files)
    assert len(rollup_lines(groups)) == len(groups) == 2


# --- the fit predicate --------------------------------------------------


def test_fits_charges_each_line_the_rows_it_wraps_to():
    # Five lines that each occupy two rows at 40 columns: ten rows.
    lines = ["x" * 41] * 5

    assert not fits(lines, 9, 40)
    assert fits(lines, 10, 40)


def test_without_a_terminal_width_every_line_costs_one_row():
    # Width None is the piped case: no wrap adjustment, so output stays
    # byte-identical across machines.
    lines = ["x" * 200] * 5

    assert fits(lines, 5, None)
    assert not fits(lines, 4, None)


# --- hostile hub text ---------------------------------------------------


def test_a_control_character_in_a_path_never_reaches_a_rendered_line():
    files = [repo_file(f"{HOSTILE}/weights.gguf"), repo_file(HOSTILE)]

    rendered = flat_lines(files) + rollup_lines(group_files(files))

    assert rendered
    for line in rendered:
        assert "\x1b" not in line
        assert "\n" not in line and "\r" not in line
    # Scrubbed, never dropped: the human still sees there is a file here.
    assert any("evil" in line for line in rendered)


# --- a partial sum says so ----------------------------------------------


def test_a_directory_missing_one_hub_size_marks_its_total_as_a_floor():
    """The number a human weighs against free disk must not lie.

    A directory whose members are *all* sizeless renders "?", but one
    the hub reports 20 of 21 sizes for used to render a bare sum,
    typographically identical to an exact one. The header's "at least"
    warns globally; the line is what gets read (review round,
    2026-08-12).
    """
    groups = group_files(
        [
            repo_file("known/a.gguf", 1024),
            repo_file("known/b.gguf", 1024),
            repo_file("partial/a.gguf", 2048),
            repo_file("partial/b.gguf", None),
        ]
    )
    known, partial = rollup_lines(groups)

    assert known.split()[0] == "2.0"
    assert "+" not in known
    assert "2.0 KiB+" in partial
    # The names still line up: the flag spends one of the two gutter
    # spaces rather than shifting the column.
    assert known.index("known/") == partial.index("partial/")


def test_a_directory_with_no_sizes_at_all_still_renders_a_question_mark():
    groups = group_files([repo_file("dark/a.gguf", None), repo_file("dark/b.gguf", None)])

    line = rollup_lines(groups)[0]

    assert "?" in line
    assert "+" not in line


# --- the prompt teaches the pattern idiom -------------------------------


def test_the_example_pattern_names_a_directory_from_this_repo():
    """The roll-up's purpose is to show directory names, and the natural
    response is to type one — which matches nothing without wildcards."""
    from llm_preserver.cli.pull_exec.listing import example_pattern, pattern_prompt

    groups = group_files([repo_file("README.md"), repo_file("UD-Q4_K_XL/a.gguf")])

    assert example_pattern(groups) == "*UD-Q4_K_XL*"
    assert "*UD-Q4_K_XL*" in pattern_prompt(example_pattern(groups))


def test_a_repo_with_no_directories_keeps_the_generic_example():
    from llm_preserver.cli.pull_exec.listing import example_pattern, pattern_prompt

    groups = group_files([repo_file("a.gguf"), repo_file("b.gguf")])

    assert example_pattern(groups) is None
    assert "*Q4_K_M*" in pattern_prompt(None)


def test_a_directory_name_carrying_glob_syntax_is_not_offered_as_an_example():
    """`*UD-Q4[x]*` is a character class, not a literal: teaching it
    would hand the human a pattern that matches nothing."""
    from llm_preserver.cli.pull_exec.listing import example_pattern

    groups = group_files([repo_file("UD-Q4[recompute]/a.gguf"), repo_file("plain/b.gguf")])

    assert example_pattern(groups) == "*plain*"
