"""Pure rendering for pull's file listing — spec 0018, pass 3.

Data in, lines out: no terminal, no ``typer``, no CLI runner. These
tests ARE the API contract for
``llm_preserver.cli.pull_exec.listing``:

    @dataclass(frozen=True)
    class ListingGroup:
        name: str               # "UD-IQ1_M" for a directory, "README.md" for a root file
        is_directory: bool
        file_count: int
        total_size: int         # sum of the sizes the hub reports
        has_unknown_size: bool  # any member whose RepoFile.size is None

    def group_files(files: Sequence[RepoFile]) -> list[ListingGroup]
    def rollup_lines(groups: Sequence[ListingGroup]) -> list[str]
    def flat_lines(files: Sequence[RepoFile]) -> list[str]
    def flat_header(repo_id: str) -> str
    def summary_header(repo_id: str, files: Sequence[RepoFile]) -> str
    def fits(lines: Sequence[str], budget: int, width: int | None) -> bool

The load-bearing invariants, in the spec's words:

- **Hub order.** A group takes the slot of its *first* member in
  ``files``; nothing is sorted, ranked, or moved (0000/0006).
- **Arithmetically honest.** Every file lands in exactly one group, a
  directory's count and size are sums of exactly what is there, and an
  unknown size makes the header say ``at least`` rather than lie.
- **The flat listing is unchanged.** ``flat_header`` and ``flat_lines``
  reproduce today's ``prompts.py`` output byte for byte, kind notes
  included — that is what keeps piped runs and every existing pull test
  valid.
- **Hostile text is scrubbed.** Paths, directory names, and the repo id
  are 100% hub-supplied; every rendered line goes through
  ``clean_text(..., single_line=True)``.

Grouping, the two headers, and the flat per-file lines are pinned here.
The roll-up's own line shape, the fit predicate, and hostile paths live
in ``test_pull_listing_rollup.py``, which imports the helpers below —
two modules because one would be over the 300-line cap in
``.claude/rules/python-code.md``.
"""

from llm_preserver.cli.pull_exec.listing import (
    flat_header,
    flat_lines,
    group_files,
    summary_header,
)
from llm_preserver.hub import RepoFile
from llm_preserver.pull_advisory import COMPANION_RULES
from llm_preserver.pull_preflight import human_size

REPO_ID = "unsloth/Kimi-K3-Instruct-GGUF"

# Read the kind string out of the curated table rather than restating
# it: the note the listing prints is whatever the advisories call it.
VISION_KIND = next(kind for pattern, kind in COMPANION_RULES if pattern == "*mmproj*")

# One string carrying an ESC sequence and a line break — the two ways
# hub text forges a line.
HOSTILE = "evil\x1b[31m\nforged row"


def repo_file(path: str, size: int | None = 1024) -> RepoFile:
    """One hub file entry; only path and size are read by the renderer."""
    return RepoFile(path=path, size=size, sha256=None)


def names(groups) -> list[str]:
    return [group.name for group in groups]


# --- grouping ----------------------------------------------------------


def test_groups_appear_in_the_hub_order_of_their_first_file():
    # Deliberately interleaved: "a" is opened before "b" and closed
    # after it, so a sorted or last-seen ordering would move it.
    files = [
        repo_file("a/1.gguf"),
        repo_file("README.md"),
        repo_file("b/1.gguf"),
        repo_file("a/2.gguf"),
    ]

    assert names(group_files(files)) == ["a", "README.md", "b"]


def test_a_later_file_joins_the_group_it_belongs_to_without_moving_it():
    files = [
        repo_file("a/1.gguf"),
        repo_file("README.md"),
        repo_file("b/1.gguf"),
        repo_file("a/2.gguf"),
    ]

    groups = {group.name: group for group in group_files(files)}
    assert groups["a"].file_count == 2
    assert groups["b"].file_count == 1


def test_a_nested_path_counts_toward_its_first_segment_only():
    groups = group_files([repo_file("x/y/z.gguf")])

    assert names(groups) == ["x"]
    assert groups[0].is_directory is True
    assert groups[0].file_count == 1


def test_a_root_file_becomes_its_own_single_file_group():
    groups = group_files([repo_file("README.md", size=512)])

    assert groups[0].name == "README.md"
    assert groups[0].is_directory is False
    assert groups[0].file_count == 1
    assert groups[0].total_size == 512


def test_every_file_lands_in_exactly_one_group():
    # The completeness criterion: the roll-up summarizes, never elides.
    files = [repo_file(f"UD-Q4_K_XL/shard-{index:05d}.gguf") for index in range(1, 22)]
    files += [repo_file("README.md"), repo_file("nested/deep/blob.bin")]

    groups = group_files(files)

    assert sum(group.file_count for group in groups) == len(files)


def test_a_directory_size_is_the_sum_of_its_members():
    files = [repo_file("q/1.gguf", size=1000), repo_file("q/2.gguf", size=24)]

    assert group_files(files)[0].total_size == 1024


def test_an_unknown_member_size_is_flagged_and_left_out_of_the_sum():
    files = [
        repo_file("q/1.gguf", size=100),
        repo_file("q/2.gguf", size=None),
        repo_file("q/3.gguf", size=50),
    ]

    group = group_files(files)[0]
    assert group.has_unknown_size is True
    assert group.total_size == 150  # a floor, never a guess


def test_a_group_whose_sizes_are_all_known_is_not_flagged():
    files = [repo_file("q/1.gguf", size=100), repo_file("q/2.gguf", size=50)]

    assert group_files(files)[0].has_unknown_size is False


# --- headers -----------------------------------------------------------


def test_summary_header_states_the_repo_wide_count_and_total():
    files = [
        repo_file("a/1.gguf", size=1024),
        repo_file("a/2.gguf", size=512),
        repo_file("README.md", size=512),
    ]

    assert summary_header(REPO_ID, files) == f"files in {REPO_ID} (3 files, {human_size(2048)}):"


def test_summary_header_says_at_least_when_a_hub_size_is_missing():
    files = [repo_file("a/1.gguf", size=1024), repo_file("mystery.bin", size=None)]

    expected = f"files in {REPO_ID} (2 files, at least {human_size(1024)}):"
    assert summary_header(REPO_ID, files) == expected


def test_flat_header_is_byte_identical_to_the_shipped_listing_header():
    # prompts.py:70 today. Piped output is pinned to this exact line.
    assert flat_header(REPO_ID) == f"files in {REPO_ID}:"


# --- the flat per-file lines -------------------------------------------


def test_a_flat_line_is_the_human_size_right_aligned_then_the_path():
    path = "UD-Q4_K_XL/Kimi-K3-Instruct-UD-Q4_K_XL-00001-of-00021.gguf"

    assert flat_lines([repo_file(path, size=19851335840)]) == [
        f"  {human_size(19851335840):>10}  {path}"
    ]


def test_a_file_the_hub_reports_no_size_for_renders_a_question_mark():
    assert flat_lines([repo_file("mystery.bin", size=None)]) == [f"  {'?':>10}  mystery.bin"]


def test_a_companion_file_keeps_the_kind_note_the_advisory_table_gives_it():
    assert flat_lines([repo_file("mmproj-F16.gguf", size=4096)]) == [
        f"  {human_size(4096):>10}  mmproj-F16.gguf  — {VISION_KIND}"
    ]


def test_flat_lines_are_one_per_file_in_hub_order():
    files = [repo_file("b/2.gguf"), repo_file("a/1.gguf"), repo_file("README.md")]

    lines = flat_lines(files)
    assert len(lines) == 3
    assert [line.split()[-1] for line in lines] == ["b/2.gguf", "a/1.gguf", "README.md"]


# --- hostile hub text ---------------------------------------------------


def test_a_control_character_in_the_repo_id_never_reaches_a_header():
    files = [repo_file("a.gguf", size=10)]

    for header in (flat_header(HOSTILE), summary_header(HOSTILE, files)):
        assert "\x1b" not in header
        assert "\n" not in header and "\r" not in header
