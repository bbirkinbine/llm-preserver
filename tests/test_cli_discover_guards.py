"""Interactive-edge pins for ``discover`` (spec 0006 review round).

EOF quits cleanly, invalid input re-prompts, tree-stage hub failures
map to the hub fault domain, and paged tree children stay grouped
under one relation header per section.
"""

import contextlib

import click
from typer.testing import CliRunner

from llm_preserver.cli import app
from llm_preserver.hub import PullHubError
from llm_preserver.hub_discovery import PAGE_SIZE, ModelSummary

runner = CliRunner()


def combined_output(result) -> str:
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return out


def init_archive_dir(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    result = runner.invoke(app, ["init", str(archive)])
    assert result.exit_code == 0
    return archive


def install_fake_hub(monkeypatch, client):
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)


def summary(repo_id, **overrides):
    kwargs = {
        "repo_id": repo_id,
        "downloads": 1,
        "last_modified": None,
        "gated": None,
        "base_model": None,
    }
    kwargs.update(overrides)
    return ModelSummary(**kwargs)


def invoke_discover(archive, *args, stdin=None):
    return runner.invoke(app, ["discover", "tiny", str(archive), *args], input=stdin)


def test_each_rendered_frame_starts_with_a_rule_line(tmp_path, monkeypatch, fake_hub_factory):
    # Accumulate-paging re-renders whole listings; scrollback needs a
    # visible boundary per frame (live-use feedback 2026-07-13). One
    # search frame + its "m" re-render + one tree frame = 3 rules.
    archive = init_archive_dir(tmp_path)
    rows = [summary(f"hub/repo-{i}") for i in range(PAGE_SIZE + 2)]
    client = fake_hub_factory(search_results=rows)
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, stdin="m\n1\nq\n")

    assert result.exit_code == 0
    assert combined_output(result).count("─" * 72) == 3


def test_eof_at_the_search_prompt_quits_cleanly(tmp_path, monkeypatch, fake_hub_factory):
    # Exhausted stdin is a quit, not an error: discovery is
    # interactive-only by design.
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(search_results=[summary("acme/tiny-chat")])
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, stdin="")

    assert result.exit_code == 0
    assert client.download_calls == []


def test_invalid_pick_reprompts_then_quit_works(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(search_results=[summary("acme/tiny-chat")])
    install_fake_hub(monkeypatch, client)

    result = runner.invoke(app, ["discover", "tiny", str(archive)], input="banana\n99\nq\n")

    assert result.exit_code == 0
    assert "not a listed pick" in combined_output(result)
    assert client.download_calls == []


def test_endless_invalid_input_is_refused_not_livelooped(tmp_path, monkeypatch, fake_hub_factory):
    # A pipe spraying garbage must hit a deterministic exit 2, mirroring
    # pull's unanswerable-prompt behavior (adjudicated 2026-07-13).
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(search_results=[summary("acme/tiny-chat")])
    install_fake_hub(monkeypatch, client)

    result = runner.invoke(app, ["discover", "tiny", str(archive)], input="x\n" * 50)

    assert result.exit_code == 2
    assert "interactive terminal" in click.unstyle(combined_output(result))
    assert client.download_calls == []


def test_tree_stage_hub_failure_exits_4_in_hub_domain(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(
        search_results=[summary("acme/tiny-chat")],
        list_children_error=PullHubError("hub says 500"),
    )
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, stdin="1\n")

    assert result.exit_code == 4
    assert "error [hub-side]" in click.unstyle(combined_output(result))


def test_tree_shows_the_navigation_trail_and_direction_labels(
    tmp_path, monkeypatch, fake_hub_factory
):
    # Live-use 2026-07-13: the parent list read as a ranked menu and
    # "1" silently climbed the tree. The page must show your path and
    # label the sections by direction.
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(
        search_results=[summary("q/tiny-gguf", base_model="acme/tiny-chat")],
        summaries={"acme/tiny-chat": summary("acme/tiny-chat")},
        children={("acme/tiny-chat", "quantized"): [summary("q/tiny-gguf", relation="quantized")]},
    )
    install_fake_hub(monkeypatch, client)

    # search pick -> tree of q/tiny-gguf -> hop UP to the parent -> quit.
    result = invoke_discover(archive, stdin="1\n1\nq\n")

    assert result.exit_code == 0
    output = combined_output(result)
    assert "up — ancestry, root at top" in output
    assert "down — derivatives" in output
    assert "your path: q/tiny-gguf → acme/tiny-chat  (you are here)" in output


def test_trail_pops_when_hopping_back_to_a_visited_repo(tmp_path, monkeypatch, fake_hub_factory):
    # The trail is a stack: parent -> back down to where you were shows
    # a single-entry path again, not a -> b -> a.
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(
        search_results=[summary("q/tiny-gguf", base_model="acme/tiny-chat")],
        summaries={"acme/tiny-chat": summary("acme/tiny-chat")},
        children={("acme/tiny-chat", "quantized"): [summary("q/tiny-gguf", relation="quantized")]},
    )
    install_fake_hub(monkeypatch, client)

    # search -> tree(q/tiny-gguf) -> up to parent (pick 1) -> back down
    # into q/tiny-gguf (pick 2: after the parent's own parents=none,
    # child 1 is q/tiny-gguf... pick 1 in that tree) -> quit.
    result = invoke_discover(archive, stdin="1\n1\n1\nq\n")

    assert result.exit_code == 0
    output = combined_output(result)
    # Back at q/tiny-gguf the trail is a single entry again — no
    # breadcrumb line renders for a one-hop path.
    final_render = output.rsplit("model tree for q/tiny-gguf:", 1)[1]
    assert "your path:" not in final_render


def test_paged_tree_children_stay_grouped_under_one_header(tmp_path, monkeypatch, fake_hub_factory):
    # After "m", the new quantized page joins its section — no
    # duplicate relation header trailing the finetune group.
    quantized = [summary(f"q/tiny-{i}", relation="quantized") for i in range(PAGE_SIZE + 3)]
    finetunes = [summary("f/tiny-ft", relation="finetune")]
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(
        search_results=[summary("acme/tiny-chat")],
        children={
            ("acme/tiny-chat", "quantized"): quantized,
            ("acme/tiny-chat", "finetune"): finetunes,
        },
    )
    install_fake_hub(monkeypatch, client)

    result = invoke_discover(archive, stdin="1\nm\nq\n")

    assert result.exit_code == 0
    output = combined_output(result)
    # Only the final render matters: take the last tree section.
    final_render = output.rsplit("model tree for", 1)[1]
    assert final_render.count("quantized versions:") == 1
    assert final_render.count("finetune versions:") == 1
    last_quant_row = final_render.rindex("q/tiny-")
    assert final_render.index("finetune versions:") > last_quant_row
