"""Interactive-edge pins for ``discover`` (spec 0006 review round).

EOF quits cleanly, invalid input re-prompts, and tree-stage hub
failures map to the hub fault domain.

Paging itself moved out with spec 0015: the section-label pins that
used to live here (children grouped under one relation header, which
only held while every "m" reprinted a re-sorted listing) are replaced
by the window-rendering tests in ``test_cli_discover_paging.py``.
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
    # Every frame needs a visible boundary (live-use feedback
    # 2026-07-13). Search window one + the "m" window over page two +
    # the tree frame = 3 rules. Pick "1" comes from window one, which
    # has scrolled off by then: a displayed number stays pickable
    # (spec 0015).
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


# Section labels across batches and windows are pinned in
# test_cli_discover_paging.py (spec 0015). The test that stood here —
# test_paged_tree_children_stay_grouped_under_one_header — asserted the
# accumulate-and-regroup rendering that spec deletes: re-sorting the
# accumulated rows by relation on every "m" is exactly what made a
# later quantized page displace already-numbered finetune rows.


# --- positional contract after spec 0013 made query/path optional ----------


def test_discover_with_no_arguments_is_a_clean_exit_2(monkeypatch):
    # Query/path went Typer-optional for match mode; normal mode must
    # still refuse to run without them (adversarial round 2026-07-31).
    monkeypatch.delenv("LLM_PRESERVER_ARCHIVE", raising=False)

    result = runner.invoke(app, ["discover"])

    assert result.exit_code == 2
    assert "QUERY" in click.unstyle(combined_output(result))


def test_discover_query_without_path_or_env_is_a_clean_exit_2(monkeypatch):
    monkeypatch.delenv("LLM_PRESERVER_ARCHIVE", raising=False)

    result = runner.invoke(app, ["discover", "tiny"])

    assert result.exit_code == 2
    assert "LLM_PRESERVER_ARCHIVE" in click.unstyle(combined_output(result))


def test_env_fallback_still_binds_the_trailing_path(tmp_path, monkeypatch, fake_hub_factory):
    # The load-bearing binding: with the env var set, `discover QUERY`
    # must run normal mode against the env archive, not misparse.
    archive = init_archive_dir(tmp_path)
    monkeypatch.setenv("LLM_PRESERVER_ARCHIVE", str(archive))
    client = fake_hub_factory(search_results=[])
    install_fake_hub(monkeypatch, client)

    result = runner.invoke(app, ["discover", "tiny"])

    assert result.exit_code == 0
    assert client.search_calls == ["tiny"]


def test_match_mode_runs_with_a_bogus_archive_env_set(tmp_path, monkeypatch, fake_hub_factory):
    # An env-filled `path` must not trip the no-positionals refusal,
    # and a broken archive env must not matter — match mode never
    # touches the archive (adversarial round 2026-07-31).
    import hashlib
    import json

    from llm_preserver import ollama_layout

    store = tmp_path / "store"
    manifest = (
        store
        / ollama_layout.MANIFESTS_DIRNAME
        / ollama_layout.REGISTRY_DIRNAME
        / ollama_layout.DEFAULT_NAMESPACE
        / "bge-m3"
        / ollama_layout.DEFAULT_TAG
    )
    manifest.parent.mkdir(parents=True)
    digest = hashlib.sha256(b"m").hexdigest()
    layer = {
        "mediaType": "application/vnd.ollama.image.model",
        "digest": f"sha256:{digest}",
        "size": 1,
    }
    manifest.write_text(json.dumps({"schemaVersion": 2, "layers": [layer]}), encoding="utf-8")
    monkeypatch.setenv(ollama_layout.MODELS_ENV_VAR, str(store))
    monkeypatch.setenv("LLM_PRESERVER_ARCHIVE", str(tmp_path / "does-not-exist"))
    client = fake_hub_factory(search_results=[])
    install_fake_hub(monkeypatch, client)

    result = runner.invoke(app, ["discover", "--match-ollama", "bge-m3"])

    assert result.exit_code == 0
    assert client.search_calls == ["bge-m3"]


def test_plan_flag_is_refused_in_match_mode(monkeypatch, fake_hub_factory):
    # --plan promises a pull dry run and match mode never pulls;
    # refuse loudly instead of silently swallowing the flag
    # (review round 2026-07-31). Refused before any store or hub read.
    client = fake_hub_factory(search_results=[])
    install_fake_hub(monkeypatch, client)

    result = runner.invoke(app, ["discover", "--match-ollama", "bge-m3", "--plan"])

    assert result.exit_code == 2
    assert "--plan" in click.unstyle(combined_output(result))
    assert client.search_calls == []


def test_limit_without_match_ollama_is_refused(tmp_path, monkeypatch, fake_hub_factory):
    archive = init_archive_dir(tmp_path)
    client = fake_hub_factory(search_results=[])
    install_fake_hub(monkeypatch, client)

    result = runner.invoke(app, ["discover", "tiny", str(archive), "--limit", "50"])

    assert result.exit_code == 2
    assert "--match-ollama" in click.unstyle(combined_output(result))
    assert client.search_calls == []


def test_limit_outside_the_ceiling_is_refused(monkeypatch, fake_hub_factory):
    client = fake_hub_factory(search_results=[])
    install_fake_hub(monkeypatch, client)

    for bad in ("0", "501"):
        result = runner.invoke(app, ["discover", "--match-ollama", "bge-m3", "--limit", bad])
        assert result.exit_code == 2
        assert "between 1 and 500" in click.unstyle(combined_output(result))
    assert client.search_calls == []
