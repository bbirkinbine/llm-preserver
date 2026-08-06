"""CLI behavior of ``discover`` — spec 0006, Phase D (search stage).

``llm-preserver discover QUERY [PATH]`` opens guided discovery: SEARCH
(the hub's free-text results, its order verbatim, one page at a time)
then TREE per picked repo, then the standard pull handoff. This file
pins the archive gate, the search listing, its pagination, and the
fault-domain mapping; the tree stage and the pull handoff live in
``test_cli_discover_flow.py``.

Interaction contract these tests pin (implementer follows the tests):

- PATH is the archive root, last positional, same ``ArchivePath`` /
  ``LLM_PRESERVER_ARCHIVE`` argument every command uses, and
  ``require_archive`` fires before any hub call: a non-archive path is
  exit 1 with zero client calls.
- An empty first search page is not an error: exit 0, no ``error [``
  framing.
- Numbered rows are navigate picks; "q" quits with exit 0 from any
  prompt; "m" advances one WINDOW (spec 0015), fetching the hub's next
  page only when the window runs past the rows already fetched. Rows
  are numbered once, at append time: no window reprints a row an
  earlier one showed, numbering runs on across pages (row 21 is pick
  "21" after one "m"), and a number stays pickable after it scrolls
  out of the window. "b" steps back one window.
- Search failures map through the pull fault-domain table
  (``PullHubError`` -> exit 4, "error [hub-side]").

Everything runs via typer.testing.CliRunner with the hub-client seam
faked; no network. Output is ``click.unstyle``d before substring
asserts (rich colors CI output; spec 0005 lesson).
"""

import contextlib

import click
from typer.testing import CliRunner

from llm_preserver.cli import app
from llm_preserver.hub import PullHubError
from llm_preserver.hub_discovery import PAGE_SIZE, ModelSummary

runner = CliRunner()


def combined_output(result) -> str:
    """stdout plus stderr when captured separately (click version dependent)."""
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return out


def unstyled_output(result) -> str:
    return click.unstyle(combined_output(result))


def init_archive_dir(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    result = runner.invoke(app, ["init", str(archive)])
    assert result.exit_code == 0
    return archive


def install_fake_hub(monkeypatch, client):
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)


def type_lines(*lines) -> str:
    """One stdin answer per prompt, newline-terminated."""
    return "".join(f"{line}\n" for line in lines)


def summary(repo_id, **overrides):
    """Build a ModelSummary with all-None facts unless overridden."""
    kwargs = {
        "repo_id": repo_id,
        "downloads": None,
        "last_modified": None,
        "gated": None,
        "base_model": None,
    }
    kwargs.update(overrides)
    return ModelSummary(**kwargs)


def line_with(output, *fragments):
    """Return the single output line containing every fragment."""
    matches = [line for line in output.splitlines() if all(f in line for f in fragments)]
    assert len(matches) == 1, f"expected exactly one line with {fragments!r} in:\n{output}"
    return matches[0]


def invoke_discover(archive, *args, query="tiny", stdin=None):
    return runner.invoke(app, ["discover", query, str(archive), *args], input=stdin)


def search_rows():
    return [
        summary(
            "bartowski/tiny-chat-GGUF",
            downloads=41,
            last_modified="2026-07-01T12:00:00+00:00",
            base_model="acme/tiny-chat",
        ),
        summary("acme/tiny-chat-gated", downloads=87, gated="manual"),
    ]


def paged_rows():
    # PAGE_SIZE + 5: page one is exactly full (more available), page two
    # holds the remaining five rows.
    return [summary(f"hub/repo-{i:02d}") for i in range(PAGE_SIZE + 5)]


def test_non_archive_path_exits_1_before_any_hub_call(tmp_path, monkeypatch, fake_hub_factory):
    not_an_archive = tmp_path / "not-an-archive"
    not_an_archive.mkdir()
    client = fake_hub_factory(search_results=search_rows())
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(not_an_archive)

    assert result.exit_code == 1
    assert "archive" in combined_output(result).lower()
    assert client.search_calls == []
    assert client.list_children_calls == []
    assert client.model_summary_calls == []
    assert client.repo_info_calls == []
    assert client.download_calls == []


def test_empty_search_results_exit_0_without_error_framing(tmp_path, monkeypatch, fake_hub_factory):
    # A nonsense query is not a failure (spec 0006): nothing to pick, so
    # no prompt runs (no stdin is provided on purpose) and exit is 0.
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(search_results=[])
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, query="no such model anywhere")

    assert result.exit_code == 0
    assert "error [" not in unstyled_output(result)
    assert client.search_calls == ["no such model anywhere"]
    assert client.download_calls == []


def test_search_page_lists_numbered_rows_with_hub_facts(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(search_results=search_rows())
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, stdin=type_lines("q"))

    assert result.exit_code == 0
    output = unstyled_output(result)
    row_one = line_with(output, "1.", "bartowski/tiny-chat-GGUF")
    assert "41" in row_one  # downloads, the hub's fact verbatim
    assert "2026-07-01" in row_one  # last-modified date part
    assert "gated" not in row_one
    row_two = line_with(output, "2.", "acme/tiny-chat-gated")
    assert "gated" in row_two  # marked, never hidden


def test_quit_at_search_prompt_exits_0_downloading_nothing(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(search_results=search_rows())
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, stdin=type_lines("q"))

    assert result.exit_code == 0
    assert client.download_calls == []
    assert list((archive / "models").iterdir()) == []


def test_first_search_page_caps_at_page_size_and_advertises_more(
    tmp_path, monkeypatch, fake_hub_factory
):
    # Search rows carry no relation, so no section labels are charged
    # against the 20-line window: page one's 20 rows fill it exactly.
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(search_results=paged_rows())
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, stdin=type_lines("q"))

    assert result.exit_code == 0
    output = unstyled_output(result)
    assert "hub/repo-19" in output  # row 20, last of page one
    assert "hub/repo-20" not in output  # row 21 stays unfetched
    assert "showing 1-20 of 20 — more (m)" in output


def test_second_search_window_prints_only_the_rows_page_one_did_not(
    tmp_path, monkeypatch, fake_hub_factory
):
    # The window advances instead of reprinting, and "b" appears once
    # there is an earlier window to go back to (spec 0015).
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(search_results=paged_rows())
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, stdin=type_lines("m", "q"))

    assert result.exit_code == 0
    output = unstyled_output(result)
    assert "showing 21-25 of 25 · back (b)" in output
    assert output.count("hub/repo-00") == 1  # row 1 was not reprinted


def test_stepping_back_in_the_search_listing_keeps_the_highest_number(
    tmp_path, monkeypatch, fake_hub_factory
):
    # "b" re-shows window one, but the count it reports is the
    # sequence's, not the window's: 25 rows have been numbered, and a
    # number never shrinks back.
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(search_results=paged_rows())
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, stdin=type_lines("m", "b", "q"))

    assert result.exit_code == 0
    assert "showing 1-20 of 25 — more (m)" in unstyled_output(result)


def test_more_extends_search_listing_and_row_21_is_pick_21(tmp_path, monkeypatch, fake_hub_factory):
    # Append-only numbering: "m" fetches page two, numbers it 21..25,
    # and pick "21" navigates into the 21st search result's tree.
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(search_results=paged_rows())
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, stdin=type_lines("m", "21", "q"))

    assert result.exit_code == 0
    output = unstyled_output(result)
    assert "model tree for hub/repo-20" in output
    assert client.download_calls == []


def test_search_hub_failure_exits_4_in_hub_domain(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(search_error=PullHubError("hub returned 500"))
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive)

    assert result.exit_code == 4
    assert "error [hub-side]" in unstyled_output(result)
    assert client.download_calls == []
