"""End-of-listing and pick-set edges of the windowed paging (spec 0015 review round).

Two contracts the main paging file cannot reach at the piped 20-line
budget, both raised by the review round on 2026-08-06:

- ``no further rows on the hub`` fires only when a window is left
  unfilled by a fetch that then comes back dry. Topping the buffer up
  means the piped budget discovers the hub's end while building the
  last window (so ``m`` is simply withdrawn — see
  ``test_reaching_the_last_row_withdraws_the_more_key``), but a tall
  terminal, where one hub page cannot fill one window, still lands on
  the message. Pinned here so the branch is not quietly dead.
- Rows fetched into the buffer but never displayed are NOT pickable —
  one of the spec's four plan-round adjudications, previously
  implemented but unasserted. The human cannot have read them.
"""

from test_cli_discover_flow import init_archive_dir, unstyled_output
from test_cli_discover_paging import child_rows, frames, run_tree, tree_client

import llm_preserver.cli.discover_cmd.stages as stages
from llm_preserver.hub_discovery import PAGE_SIZE


def test_a_window_a_fetch_cannot_fill_reports_the_end_of_the_hubs_rows(
    tmp_path, monkeypatch, fake_hub_factory
):
    # A tall terminal: the window wants far more rows than one hub page
    # carries, so the buffer is still short after the top-up and the
    # NEXT "m" is the one that discovers the end.
    monkeypatch.setattr(stages, "resolve_window_size", lambda stream, chrome: 500)
    archive = init_archive_dir(tmp_path)
    client = tree_client(fake_hub_factory, quantized=child_rows("quantized", PAGE_SIZE))

    result = run_tree(archive, monkeypatch, client, "1", "m", "q")

    assert result.exit_code == 0
    output = unstyled_output(result)
    assert output.count("no further rows on the hub") == 1
    # The message replaces a frame; it does not accompany one.
    assert len(frames(result)) == 2  # the search frame and one tree frame


def test_a_fetched_row_that_was_never_displayed_is_not_pickable(
    tmp_path, monkeypatch, fake_hub_factory
):
    # The buffer holds PAGE_SIZE quantized rows; the first window shows
    # 19 of them (one line goes to the section label). Row 20 exists in
    # memory and is NOT offered — the adjudicated rule.
    archive = init_archive_dir(tmp_path)
    client = tree_client(fake_hub_factory, quantized=child_rows("quantized", PAGE_SIZE))

    result = run_tree(archive, monkeypatch, client, "1", str(PAGE_SIZE), "q")

    assert result.exit_code == 0
    output = unstyled_output(result)
    assert "not a listed pick" in output
    # It never navigated into the undisplayed row.
    assert f"model tree for q/child-{PAGE_SIZE - 1:02d}" not in output


def test_the_footer_never_advertises_a_number_the_prompt_refuses(
    tmp_path, monkeypatch, fake_hub_factory
):
    # The footer's "of N" is numbers HANDED OUT, not rows fetched: the
    # first frame buffers PAGE_SIZE rows but has shown only 19, so it
    # must say "of 19" (review round 2026-08-06 — it said "of 80" on a
    # four-relation tree while refusing every number past the window).
    archive = init_archive_dir(tmp_path)
    client = tree_client(fake_hub_factory, quantized=child_rows("quantized", PAGE_SIZE))

    result = run_tree(archive, monkeypatch, client, "1", "q")

    assert result.exit_code == 0
    first = frames(result)[-1]
    shown = [line for line in first.splitlines() if line.startswith("showing ")]
    assert shown == ["showing 1-19 of 19 — more (m)"]
