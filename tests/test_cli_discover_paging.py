"""CLI behavior of the tree stage's window paging — spec 0015.

Spec 0006 paged by accumulating rows and reprinting the whole listing
on every "m", re-sorting the children by relation each loop: frames
grew past a screen (89 lines on tree entry, 169 after one "m"), and a
freshly fetched quantized page landed AHEAD of finetune rows already
numbered on screen — a probe over four relations of 40 children each
measured 60 of 80 numbers naming a different repo after one "m" (pick
21 was ``f/child-00`` before and ``q/child-20`` after). Spec 0015
replaces that with an append-only sequence shown one window at a time.

The CLI contract these tests state (implementer follows the tests): a
window prints only rows no earlier window printed; "m" advances one
window, fetching a batch only when the window would run past the rows
buffered; every number ever displayed stays pickable and keeps naming
the same repo, even scrolled out of view; "b" steps back one window
and fetches nothing, offered only once an earlier window exists; "m"
with nothing further on the hub prints one line and re-prompts.

The search stage's half lives in ``test_cli_discover.py``, the pure
pieces in ``test_discover_paging`` / ``test_discover_windows``, the line
rendering in ``test_discover_render``. Window size resolves to a fixed
20 without a TTY (the 0009 non-TTY determinism precedent) and
``CliRunner`` is never a TTY, so the budget here is 20 LINES — one per
row plus one per section label the slice emits — and every count below
derives from that.
"""

import re

from test_cli_discover_flow import (
    init_archive_dir,
    install_fake_hub,
    invoke_discover,
    summary,
    type_lines,
    unstyled_output,
)

from llm_preserver.hub_discovery import PAGE_SIZE

FRAME_RULE = "─" * 72
TREE_REPO = "acme/original-7b"

# The "no frame exceeds one screen" criterion, in non-blank lines: the
# 20-line window budget (rows plus the section labels they emit) plus a
# parentless tree frame's fixed chrome — "model tree for <id>:", the
# "down — derivatives ...:" intro, "  0. pull this repo (<id>)", the
# footer, and the prompt CliRunner echoes with the typed answer. 20 + 5.
MAX_FRAME_LINES = 25


def frames(result) -> list[str]:
    """Rendered frames of a transcript, split on the 0006 separator rule."""
    return unstyled_output(result).split(FRAME_RULE)[1:]


def frame_with(result, fragment) -> str:
    """The single rendered frame containing ``fragment``."""
    matches = [frame for frame in frames(result) if fragment in frame]
    assert len(matches) == 1, f"expected exactly one frame with {fragment!r}"
    return matches[0]


def pick_lines(frame) -> list[str]:
    """The numbered pick lines of one frame, verbatim."""
    return [line for line in frame.splitlines() if re.match(r"\s*\d+\. ", line)]


def child_rows(relation, count):
    """``count`` children ``<initial>/child-NN`` under one relation."""
    return [
        summary(f"{relation[0]}/child-{index:02d}", relation=relation) for index in range(count)
    ]


def tree_client(fake_hub_factory, **relations):
    """A parentless search hit whose tree serves the given children.

    Each keyword names a relation; its value is a child count or a
    ready-made row source.
    """
    return fake_hub_factory(
        repo_id=TREE_REPO,
        base_model=None,
        search_results=[summary(TREE_REPO)],
        children={
            (TREE_REPO, relation): child_rows(relation, rows) if isinstance(rows, int) else rows
            for relation, rows in relations.items()
        },
    )


# Two relations, each past PAGE_SIZE — the shape the 0006 regression
# test cannot catch (its re-sort was a no-op with one relation). Batch
# one is 20 quantized rows THEN 20 finetune rows, so pick 21 is
# f/child-00.
TWO_RELATIONS = {"quantized": PAGE_SIZE + 5, "finetune": PAGE_SIZE + 5}

# Batch one is 20 quantized rows plus the lone finetune row, so window
# two straddles the end of the quantized section, and the batch fetched
# after it opens a quantized section of its own.
STRADDLING = {"quantized": PAGE_SIZE + 3, "finetune": 1}


def run_tree(archive, monkeypatch, client, *picks, args=()):
    """Drive one discover session over ``client`` and return the result."""
    install_fake_hub(monkeypatch, client)
    return invoke_discover(archive, *args, stdin=type_lines(*picks))


class CountingRows:
    """A children source that counts the rows a pager pulled from it."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.pulled = 0

    def __iter__(self):
        for row in self.rows:
            self.pulled += 1
            yield row


# --- numbers are permanent --------------------------------------------


def test_a_number_read_before_paging_still_opens_the_repo_it_named(
    tmp_path, monkeypatch, fake_hub_factory
):
    # THE regression this spec exists for. Pick 21 (f/child-00) is
    # displayed in the second window; the third "m" runs past the
    # buffer and fetches a fresh batch, which under accumulate-and-
    # regroup landed AHEAD of the finetune rows — pick 21 silently
    # became q/child-20.
    archive = init_archive_dir(tmp_path)
    client = tree_client(fake_hub_factory, **TWO_RELATIONS)

    result = run_tree(archive, monkeypatch, client, "1", "m", "m", "m", "21", "q")

    assert result.exit_code == 0
    output = unstyled_output(result)
    assert "21. f/child-00" in output  # what the window offered
    assert "model tree for f/child-00" in output  # what typing it opened
    assert "model tree for q/child-20" not in output  # where the old code landed


def test_paging_forward_never_prints_a_row_twice(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = tree_client(fake_hub_factory, **TWO_RELATIONS)

    result = run_tree(archive, monkeypatch, client, "1", "m", "m", "m", "m", "q")

    assert result.exit_code == 0
    output = unstyled_output(result)
    ids = [f"q/child-{i:02d}" for i in range(25)] + [f"f/child-{i:02d}" for i in range(25)]
    assert [repo_id for repo_id in ids if output.count(repo_id) > 1] == []
    assert "q/child-24" in output  # paging did reach the second batch


def test_no_rendered_frame_exceeds_one_screen(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = tree_client(fake_hub_factory, **TWO_RELATIONS)

    result = run_tree(archive, monkeypatch, client, "1", "m", "m", "m", "q")

    assert result.exit_code == 0
    heights = [
        len([line for line in frame.splitlines() if line.strip()]) for frame in frames(result)
    ]
    assert max(heights) <= MAX_FRAME_LINES


# --- stepping back ----------------------------------------------------


def test_stepping_back_renders_a_frame_without_fetching_rows(
    tmp_path, monkeypatch, fake_hub_factory
):
    archive = init_archive_dir(tmp_path)

    def run(*picks):
        sources = {
            relation: CountingRows(child_rows(relation, count))
            for relation, count in TWO_RELATIONS.items()
        }
        client = tree_client(fake_hub_factory, **sources)
        result = run_tree(archive, monkeypatch, client, *picks)
        assert result.exit_code == 0
        return result, client, sum(source.pulled for source in sources.values())

    forward, forward_client, forward_pulled = run("1", "m", "q")
    stepped, stepped_client, stepped_pulled = run("1", "m", "b", "q")

    assert len(frames(stepped)) == len(frames(forward)) + 1  # "b" showed a frame
    assert stepped_pulled == forward_pulled  # and pulled no hub rows to do it
    assert stepped_client.list_children_calls == forward_client.list_children_calls


def test_stepping_back_reshows_the_previous_windows_rows(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = tree_client(fake_hub_factory, **TWO_RELATIONS)

    result = run_tree(archive, monkeypatch, client, "1", "m", "b", "q")

    assert result.exit_code == 0
    shown = frames(result)  # search, window one, window two, window one again
    assert pick_lines(shown[-1]) == pick_lines(shown[-3])
    assert pick_lines(shown[-1]) != pick_lines(shown[-2])


def test_the_first_frame_neither_offers_nor_accepts_back(tmp_path, monkeypatch, fake_hub_factory):
    # Stepping back off frame one would render zero rows.
    archive = init_archive_dir(tmp_path)
    client = tree_client(fake_hub_factory, **TWO_RELATIONS)

    result = run_tree(archive, monkeypatch, client, "1", "b", "q")

    assert result.exit_code == 0
    first_tree_frame = frames(result)[1]
    assert f"hop the tree by number — 0 = pull {TREE_REPO} (m = more, q = quit)" in first_tree_frame
    assert "b = back" not in first_tree_frame
    assert "not a listed pick — enter a listed number or one of m/q" in first_tree_frame


# --- the end of the hub's rows ----------------------------------------


def test_reaching_the_last_row_withdraws_the_more_key(tmp_path, monkeypatch, fake_hub_factory):
    # Exactly PAGE_SIZE children: the hub's end is only knowable after
    # one more, empty fetch. Spec 0006 answered a doomed "m" by
    # reprinting the whole frame having fetched nothing (observed: 168
    # lines, no new entries). Topping the buffer up (review round
    # 2026-08-06) discovers the end while building the LAST window, so
    # there is nothing to explain after the fact: the footer and the
    # prompt both drop "more" before the user can press it.
    archive = init_archive_dir(tmp_path)
    client = tree_client(fake_hub_factory, quantized=child_rows("quantized", PAGE_SIZE))

    result = run_tree(archive, monkeypatch, client, "1", "m", "q")

    assert result.exit_code == 0
    last = frames(result)[-1]
    assert "m = more" not in last  # the prompt withdrew it, and
    assert "— more (m)" not in last  # so did the footer, together
    assert "b = back a page" in last  # while the way back stays


# --- section labels across batches and windows ------------------------
# Replaces test_cli_discover_guards.py's
# test_paged_tree_children_stay_grouped_under_one_header, which pinned
# the accumulate-and-regroup rendering 0015 removes.


PICKS = ("1", "m", "m", "q")  # into the tree, one window on, one batch on


def test_a_window_continuing_a_section_labels_it_continued(tmp_path, monkeypatch, fake_hub_factory):
    # A frame nobody can scroll back to must say what it is showing.
    archive = init_archive_dir(tmp_path)

    result = run_tree(archive, monkeypatch, tree_client(fake_hub_factory, **STRADDLING), *PICKS)

    assert result.exit_code == 0
    assert "quantized versions (continued):" in frame_with(result, "q/child-19")


def test_a_later_batch_renders_its_own_section_label(tmp_path, monkeypatch, fake_hub_factory):
    # Nothing is re-sorted, so batch two's quantized page follows the
    # finetune row: a new section, not a continuation (0015 sketch).
    archive = init_archive_dir(tmp_path)

    result = run_tree(archive, monkeypatch, tree_client(fake_hub_factory, **STRADDLING), *PICKS)

    assert result.exit_code == 0
    window = frame_with(result, "q/child-22")
    lines = window.splitlines()
    row = next(i for i, line in enumerate(lines) if "q/child-22" in line)
    label = next(lines[i] for i in range(row, -1, -1) if lines[i].endswith("versions:"))
    # The label directly above it is a fresh section, not a carried-over
    # one. A frame may still open mid-section further up — that window
    # legitimately says "(continued)" about its OWN first row.
    assert label == "quantized versions:"
    assert "q/child-00" not in window  # whose rows this frame never reprints


def test_the_stable_picks_still_fire_from_a_later_window(tmp_path, monkeypatch, fake_hub_factory):
    # "0" and "q" are frame-independent by design (0006 pinned "0" as
    # the pull key after the last number moved on every fetch).
    archive = init_archive_dir(tmp_path)
    client = tree_client(fake_hub_factory, **TWO_RELATIONS)

    quit_run = run_tree(archive, monkeypatch, client, "1", "m", "m", "q")
    pull_picks = ("1", "m", "0", "1", "*Q4_K_M*")
    pull_run = run_tree(archive, monkeypatch, client, *pull_picks, args=("--plan",))

    assert (quit_run.exit_code, pull_run.exit_code) == (0, 0)
    assert client.download_calls == []  # "q" left the archive alone
    assert "plan only" in unstyled_output(pull_run)  # "0" reached the pull
    assert f"  0. pull this repo ({TREE_REPO})" in frames(pull_run)[-1]
    for run in (quit_run, pull_run):
        assert "q/child-00" not in frames(run)[-1]  # a later window, not frame one
