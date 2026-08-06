"""Frames fit a real terminal once rows are allowed to wrap (spec 0015).

The spec's headline criterion is "no frame exceeds one screen", and
every other CLI test measures *logical* lines at the fixed piped budget
of 20. That misses the case the spec was written for. Real hub ids
render 90-100 characters — ``quantized-provider-org/Qwen3-Coder-30B-
A3B-Instruct-00-GGUF`` plus downloads and date is 99 — so at 80 columns
each row wraps to two physical lines. Counting logical lines promised
one screen and delivered nearly two: the review round measured a 22-line
frame occupying 41-45 physical rows on an 80x24 terminal, the exact
scrollback-less failure the spec exists to fix (2026-08-06).

``fit_rows`` now charges each row its wrapped height and
``tree_chrome_lines`` charges the chrome — including the unbounded
``your path:`` breadcrumb — the same way.

The terminal is simulated at the resolver seam rather than by faking a
TTY: ``CliRunner`` replaces ``sys.stdout`` after any patch of it, so
patching ``isatty`` measures the piped path by accident. The
``isatty``-gate itself is pinned separately in
``test_discover_window_size.py`` and ``test_discover_window_degraded.py``.
"""

import math

from test_cli_discover_flow import (
    init_archive_dir,
    install_fake_hub,
    invoke_discover,
    summary,
    type_lines,
)
from test_cli_discover_paging import frames, unstyled_output

import llm_preserver.cli.discover_cmd.stages as stages

COLUMNS = 80
ROWS = 24

# A realistic long hub id; this archive holds ids of exactly this shape.
LONG_ID = "quantized-provider-org/Qwen3-Coder-30B-A3B-Instruct-{:02d}-GGUF"


def simulate_terminal(monkeypatch, *, columns: int = COLUMNS, rows: int = ROWS) -> None:
    """Point the stages at a terminal of the given size."""
    monkeypatch.setattr(stages, "resolve_window_width", lambda stream: columns)
    monkeypatch.setattr(stages, "resolve_window_size", lambda stream, chrome: max(5, rows - chrome))


def wide_tree_client(fake_hub_factory, *, base_model=None, count: int = 40):
    """A tree whose every child id wraps at 80 columns."""
    children = {
        ("acme/base-7b", relation): [
            summary(
                LONG_ID.format(index),
                relation=relation,
                downloads=1234567,
                last_modified="2026-07-01T00:00:00",
            )
            for index in range(count)
        ]
        for relation in ("quantized", "finetune")
    }
    return fake_hub_factory(
        repo_id="acme/base-7b",
        base_model=base_model,
        search_results=[summary("acme/base-7b", base_model=base_model)],
        summaries={"acme/root": summary("acme/root")},
        children=children,
    )


def physical_height(frame: str, columns: int = COLUMNS) -> int:
    """Rows a frame occupies once its lines wrap at ``columns``."""
    return sum(
        max(1, math.ceil(len(line) / columns)) for line in frame.splitlines() if line.strip()
    )


def test_no_frame_overflows_an_eighty_column_terminal(tmp_path, monkeypatch, fake_hub_factory):
    simulate_terminal(monkeypatch)
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, wide_tree_client(fake_hub_factory))

    result = invoke_discover(archive, stdin=type_lines("1", "m", "m", "m", "q"))

    assert result.exit_code == 0
    heights = [physical_height(frame) for frame in frames(result)]
    assert heights, "the run rendered no frames"
    assert max(heights) <= ROWS, f"a frame overflowed the screen: {heights}"
    # And the rows really do wrap — otherwise this test proves nothing.
    assert any(len(line) > COLUMNS for line in unstyled_output(result).splitlines())


def test_a_deep_breadcrumb_does_not_push_a_frame_off_the_screen(
    tmp_path, monkeypatch, fake_hub_factory
):
    # `your path:` grows with every hop and nothing caps it the way
    # MAX_PARENT_HOPS caps the ladder; a six-hop trail measured 444
    # characters, charged as one line before the review round.
    simulate_terminal(monkeypatch)
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, wide_tree_client(fake_hub_factory, base_model="acme/root"))

    # Hop up to the parent and back down, growing the trail, then page.
    result = invoke_discover(archive, stdin=type_lines("1", "1", "m", "m", "q"))

    assert result.exit_code == 0
    heights = [physical_height(frame) for frame in frames(result)]
    assert max(heights) <= ROWS, f"a frame overflowed the screen: {heights}"


def test_piped_output_is_unaffected_by_the_wrap_aware_budget(
    tmp_path, monkeypatch, fake_hub_factory
):
    # No terminal simulation: the flat one-line-per-row cost must hold,
    # so piped runs stay byte-identical across machines (the
    # determinism criterion).
    archive = init_archive_dir(tmp_path)
    install_fake_hub(monkeypatch, wide_tree_client(fake_hub_factory))
    first = invoke_discover(archive, stdin=type_lines("1", "m", "q"))

    install_fake_hub(monkeypatch, wide_tree_client(fake_hub_factory))
    second = invoke_discover(archive, stdin=type_lines("1", "m", "q"))

    assert first.exit_code == 0
    assert unstyled_output(first) == unstyled_output(second)
    # 19 rows under one section label = the piped 20-line budget.
    assert "showing 1-19 of 19 — more (m)" in unstyled_output(first)
