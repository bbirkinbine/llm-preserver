"""CLI behavior of ``discover`` — spec 0006, Phase D (tree + handoff).

Continues ``test_cli_discover.py``: picking a search row opens that
repo's TREE stage (parent chain via ``model_summary`` hops, children
via ``list_children`` in the fixed relation order quantized, finetune,
adapter, merge, continuous numbering ending in "pull this repo");
picking "pull this repo" lands in the unmodified pull flow via the
shared core (``run_pull`` with ``repo_info=`` fetched once and
``model=None`` — review adjudication 2026-07-13: pull's own
confirm-gated, format-directed grouping proposes the home and the
human answers y/n; no ``--model`` flag is ever typed, and hub
metadata never names a directory silently). ``--plan`` turns the
final pull into the 0005 dry run.

CliRunner + faked hub seam; no network; output unstyled before
substring asserts.
"""

import contextlib

import click
from typer.testing import CliRunner

from llm_preserver.cli import app
from llm_preserver.hub_discovery import PAGE_SIZE, ModelSummary

runner = CliRunner()

QUANT_REPO = "bartowski/tiny-chat-GGUF"  # the conftest default repo
BASE_MODEL = "acme/tiny-chat"  # its declared base_model


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


def invoke_discover(archive, *args, query="tiny", stdin=None):
    return runner.invoke(app, ["discover", query, str(archive), *args], input=stdin)


def quant_client(fake_hub_factory, **overrides):
    """The happy-path fake: one search hit (the conftest quant repo)
    whose summary declares BASE_MODEL, and a resolvable base summary.
    The pull's grouping default proposes BASE_MODEL (GGUF conversion).

    Tree picks for QUANT_REPO: 1 = parent (BASE_MODEL), 0 = pull this
    repo (the stable key; no children are configured).
    """
    defaults = dict(
        search_results=[summary(QUANT_REPO, downloads=41, base_model=BASE_MODEL)],
        summaries={BASE_MODEL: summary(BASE_MODEL, downloads=999)},
    )
    defaults.update(overrides)
    return fake_hub_factory(**defaults)


# Search pick 1 -> tree pick 0 (the stable pull-this-repo key) ->
# archive mode 1 (pick files) -> include patterns -> grouping confirm
# -> size confirm. No --model flag anywhere: pull's confirm-gated
# default proposes the home and the human says y.
HAPPY_PATH_STDIN = type_lines("1", "0", "1", "*Q4_K_M*", "y", "y")
PLAN_STDIN = type_lines("1", "0", "1", "*Q4_K_M*")  # --plan never confirms


def test_entering_tree_fetches_children_in_fixed_relation_order(
    tmp_path, monkeypatch, fake_hub_factory
):
    archive = init_archive_dir(tmp_path)
    client = quant_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, stdin=type_lines("1", "q"))

    assert result.exit_code == 0
    assert client.list_children_calls == [
        (QUANT_REPO, "quantized"),
        (QUANT_REPO, "finetune"),
        (QUANT_REPO, "adapter"),
        (QUANT_REPO, "merge"),
    ]


def test_full_discovery_pull_groups_under_confirmed_default_model(
    tmp_path, monkeypatch, fake_hub_factory
):
    # The spec's thesis end to end: name -> numbered picks -> pull, with
    # the archive home proposed by pull's format-directed default and
    # confirmed with a y — never typed, never silently decided.
    archive = init_archive_dir(tmp_path)
    client = quant_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, stdin=HAPPY_PATH_STDIN)

    assert result.exit_code == 0
    output = unstyled_output(result)
    assert f"files in {QUANT_REPO}:" in output  # the standard listing ran
    record = archive / "models" / "acme" / "tiny-chat" / "model-record.json"
    assert record.is_file()  # grouped under the confirmed default
    assert set(client.download_calls) == {"tiny-chat-Q4_K_M.gguf", "README.md"}
    # No model override exists, so the 0005 mismatch warning is
    # structurally silent on every discovery-driven pull. Assert on
    # the warning's rendered text — the internal kind label never
    # appears in output, so matching it would be vacuous.
    assert "declares base model" not in output


def test_pull_handoff_fetches_repo_metadata_exactly_once(tmp_path, monkeypatch, fake_hub_factory):
    # Spec 0003's one-metadata-call rule holds through the handoff: the
    # listing and the pull share a single repo_info fetch.
    archive = init_archive_dir(tmp_path)
    client = quant_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, stdin=HAPPY_PATH_STDIN)

    assert result.exit_code == 0
    assert client.repo_info_calls == [QUANT_REPO]


def test_repo_without_declared_base_groups_under_its_own_id(
    tmp_path, monkeypatch, fake_hub_factory
):
    # No parent anywhere in the tree: pull's default proposes the
    # repo's own id and the grouping confirmation takes a y.
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(
        repo_id="acme/original-7b",
        base_model=None,
        search_results=[summary("acme/original-7b", downloads=7)],
    )
    install_fake_hub(monkeypatch, client)

    # "0" pulls regardless of tree shape; then archive mode 1,
    # patterns, and the two y answers (grouping, size).
    result = invoke_discover(archive, stdin=type_lines("1", "0", "1", "*Q4_K_M*", "y", "y"))

    assert result.exit_code == 0
    assert (archive / "models" / "acme" / "original-7b" / "model-record.json").is_file()


def test_plan_handoff_reports_without_downloading_or_writing(
    tmp_path, monkeypatch, fake_hub_factory
):
    archive = init_archive_dir(tmp_path)
    client = quant_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, "--plan", stdin=PLAN_STDIN)

    assert result.exit_code == 0
    assert "plan only" in unstyled_output(result)
    assert client.download_calls == []
    assert list((archive / "models").iterdir()) == []
    assert not (archive / ".staging").exists()


def renamed_chain_client(fake_hub_factory):
    """A quant repo whose declared base was renamed, and whose current
    base declares a parent that no longer exists on the hub."""
    return fake_hub_factory(
        repo_id="quant/tiny-GGUF",
        search_results=[summary("quant/tiny-GGUF", base_model="old/base")],
        summaries={
            "old/base": summary("new/base", base_model="ghost/gone"),
            "new/base": summary("new/base", base_model="ghost/gone"),
        },
    )


def test_tree_shows_renamed_and_not_found_parents_honestly(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = renamed_chain_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, stdin=type_lines("1", "q"))

    assert result.exit_code == 0
    output = unstyled_output(result)
    renamed_lines = [
        line for line in output.splitlines() if "old/base" in line and "new/base" in line
    ]
    assert renamed_lines and all("renamed" in line for line in renamed_lines)
    assert "ghost/gone" in output  # the dead id is shown, not dropped
    assert "not found on the hub" in output


def test_navigating_into_renamed_parent_opens_its_current_id_tree(
    tmp_path, monkeypatch, fake_hub_factory
):
    # The renamed parent is navigable (it takes a number); entering it
    # opens the tree of the hub's CURRENT id, never the stale one.
    archive = init_archive_dir(tmp_path)
    client = renamed_chain_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, stdin=type_lines("1", "1", "q"))

    assert result.exit_code == 0
    assert "model tree for new/base" in unstyled_output(result)
    assert client.download_calls == []


def many_children_client(fake_hub_factory):
    children_rows = [
        summary(f"q/child-{i:02d}", relation="quantized") for i in range(PAGE_SIZE + 5)
    ]
    return fake_hub_factory(
        repo_id="acme/original-7b",
        base_model=None,
        search_results=[summary("acme/original-7b")],
        children={("acme/original-7b", "quantized"): children_rows},
    )


def test_tree_children_first_page_caps_at_page_size(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = many_children_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, stdin=type_lines("1", "q"))

    assert result.exit_code == 0
    output = unstyled_output(result)
    assert "q/child-19" in output  # child 20, last of page one
    assert "q/child-20" not in output  # child 21 stays unfetched
    assert "more available" in output


def test_tree_more_extends_children_with_continuous_numbering(
    tmp_path, monkeypatch, fake_hub_factory
):
    # After "m" the tree holds children 1..25 (pull this repo is 26);
    # pick "25" navigates into the 25th child fetched on page two.
    archive = init_archive_dir(tmp_path)
    client = many_children_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, stdin=type_lines("1", "m", "25", "q"))

    assert result.exit_code == 0
    assert "model tree for q/child-24" in unstyled_output(result)
    assert client.download_calls == []


def test_identical_plan_sessions_produce_identical_output(tmp_path, monkeypatch, fake_hub_factory):
    # The determinism invariant, CLI-shaped: same fake hub, same picks,
    # byte-identical output. --plan writes nothing, so the archive is
    # in the same state for both runs.
    archive = init_archive_dir(tmp_path)

    def run_once() -> str:
        client = quant_client(fake_hub_factory)
        install_fake_hub(monkeypatch, client)
        result = invoke_discover(archive, "--plan", stdin=PLAN_STDIN)
        assert result.exit_code == 0
        return combined_output(result)

    assert run_once() == run_once()
