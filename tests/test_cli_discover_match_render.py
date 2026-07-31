"""Output shape of ``discover --match-ollama`` (spec 0013).

Live-use adjudication 2026-07-31: the first real run buried the one
actionable line in twenty candidate blocks. Pins the two rendering
rules that fix it — no-GGUF repos roll up into a single summary line,
and the pasteable command prints in a footer as the final output
(0007 stance: the line to paste sits directly above the next prompt).

Split from test_cli_discover_match.py (at the 300-line cap).
"""

import contextlib
import hashlib
import json
from pathlib import Path

import click
from typer.testing import CliRunner

from llm_preserver import ollama_layout
from llm_preserver.cli import app
from llm_preserver.hub_discovery import ModelSummary

runner = CliRunner()

MATCH_BYTES = b"m" * 96
MATCH_DIGEST = hashlib.sha256(MATCH_BYTES).hexdigest()
MATCH_REPO = "gpustack/bge-m3-GGUF"
MATCH_FILE = "bge-m3-FP16.gguf"
BARE_REPOS = ["BAAI/bge-m3", "Xenova/bge-m3", "mlx-community/bge-m3-mlx-fp16"]


def output_of(result) -> str:
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return click.unstyle(out)


def make_store(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / "ollama-store"
    manifest = (
        store
        / ollama_layout.MANIFESTS_DIRNAME
        / ollama_layout.REGISTRY_DIRNAME
        / ollama_layout.DEFAULT_NAMESPACE
        / "bge-m3"
        / ollama_layout.DEFAULT_TAG
    )
    manifest.parent.mkdir(parents=True)
    layer = {
        "mediaType": "application/vnd.ollama.image.model",
        "digest": f"sha256:{MATCH_DIGEST}",
        "size": len(MATCH_BYTES),
    }
    manifest.write_text(json.dumps({"schemaVersion": 2, "layers": [layer]}), encoding="utf-8")
    monkeypatch.setenv(ollama_layout.MODELS_ENV_VAR, str(store))
    monkeypatch.delenv("LLM_PRESERVER_ARCHIVE", raising=False)


def summary(repo_id):
    return ModelSummary(
        repo_id=repo_id, downloads=None, last_modified=None, gated=None, base_model=None
    )


def make_client(fake_hub_factory):
    return fake_hub_factory(
        search_results=[summary(repo) for repo in [*BARE_REPOS, MATCH_REPO]],
        repos={
            MATCH_REPO: [(MATCH_FILE, MATCH_BYTES, True)],
            # The bare repos serve a GGUF-less file list.
            **{repo: [("model.safetensors", b"st", True)] for repo in BARE_REPOS},
        },
    )


def install_fake_hub(monkeypatch, client):
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)


def run_match(tmp_path, monkeypatch, fake_hub_factory):
    make_store(tmp_path, monkeypatch)
    install_fake_hub(monkeypatch, make_client(fake_hub_factory))
    result = runner.invoke(app, ["discover", "--match-ollama", "bge-m3"])
    assert result.exit_code == 0
    return output_of(result)


def test_no_gguf_repos_roll_up_into_one_line(tmp_path, monkeypatch, fake_hub_factory):
    output = run_match(tmp_path, monkeypatch, fake_hub_factory)

    rollups = [line for line in output.splitlines() if "no GGUF files" in line]
    assert len(rollups) == 1
    line = rollups[0]
    assert "3 of 4" in line
    for repo in BARE_REPOS:
        assert repo in line


def test_pasteable_command_is_the_final_output_line(tmp_path, monkeypatch, fake_hub_factory):
    output = run_match(tmp_path, monkeypatch, fake_hub_factory)

    last = output.rstrip("\n").splitlines()[-1]
    assert last.strip() == f"llm-preserver pull {MATCH_REPO} --include {MATCH_FILE}"


def test_match_footer_states_the_count_and_the_action(tmp_path, monkeypatch, fake_hub_factory):
    output = run_match(tmp_path, monkeypatch, fake_hub_factory)

    assert "1 byte-identical match — run this to archive it:" in output


def test_limit_pages_past_the_first_search_page(tmp_path, monkeypatch, fake_hub_factory):
    # The first live run's second true match sat beyond the hub's
    # first page (adjudicated 2026-07-31): --limit must keep paging
    # the same search, hub order intact, until the cap.
    make_store(tmp_path, monkeypatch)
    fillers = [summary(f"filler/repo-{n}") for n in range(24)]
    client = fake_hub_factory(
        search_results=[*fillers, summary(MATCH_REPO)],
        repos={
            MATCH_REPO: [(MATCH_FILE, MATCH_BYTES, True)],
            **{f"filler/repo-{n}": [("model.safetensors", b"st", True)] for n in range(24)},
        },
    )
    install_fake_hub(monkeypatch, client)

    result = runner.invoke(app, ["discover", "--match-ollama", "bge-m3", "--limit", "25"])

    assert result.exit_code == 0
    output = output_of(result)
    assert "checking the first 25 hub search results" in output
    assert "1 byte-identical match" in output  # candidate #25, page two
    assert f"llm-preserver pull {MATCH_REPO} --include {MATCH_FILE}" in output


def test_default_limit_still_stops_at_the_first_page(tmp_path, monkeypatch, fake_hub_factory):
    make_store(tmp_path, monkeypatch)
    fillers = [summary(f"filler/repo-{n}") for n in range(24)]
    client = fake_hub_factory(
        search_results=[*fillers, summary(MATCH_REPO)],
        repos={
            MATCH_REPO: [(MATCH_FILE, MATCH_BYTES, True)],
            **{f"filler/repo-{n}": [("model.safetensors", b"st", True)] for n in range(24)},
        },
    )
    install_fake_hub(monkeypatch, client)

    result = runner.invoke(app, ["discover", "--match-ollama", "bge-m3"])

    assert result.exit_code == 0
    output = output_of(result)
    assert "checking the first 20 hub search results" in output
    assert "no exact match" in output  # the match sits at #25, unchecked
    assert len(client.repo_info_calls) == 20


def test_rollup_line_truncates_past_ten_names(tmp_path, monkeypatch, fake_hub_factory):
    # At --limit 500 the roll-up itself became a 440-name wall
    # (live-use adjudication 2026-07-31): names cap at ten, the count
    # carries the rest.
    make_store(tmp_path, monkeypatch)
    bare = [f"filler/repo-{n}" for n in range(12)]
    client = fake_hub_factory(
        search_results=[*(summary(repo) for repo in bare), summary(MATCH_REPO)],
        repos={
            MATCH_REPO: [(MATCH_FILE, MATCH_BYTES, True)],
            **{repo: [("model.safetensors", b"st", True)] for repo in bare},
        },
    )
    install_fake_hub(monkeypatch, client)

    result = runner.invoke(app, ["discover", "--match-ollama", "bge-m3", "--limit", "13"])

    assert result.exit_code == 0
    output = output_of(result)
    rollup = next(line for line in output.splitlines() if "no GGUF files" in line)
    assert "12 of 13" in rollup
    assert "and 2 more" in rollup
    assert "filler/repo-9" in rollup
    assert "filler/repo-10" not in rollup


def test_multiple_matches_say_any_one_suffices(tmp_path, monkeypatch, fake_hub_factory):
    # Byte-identical matches are the same bytes: the footer must not
    # read as "run all of these".
    make_store(tmp_path, monkeypatch)
    second_repo = "lm-kit/bge-m3-gguf"
    client = fake_hub_factory(
        search_results=[summary(MATCH_REPO), summary(second_repo)],
        repos={
            MATCH_REPO: [(MATCH_FILE, MATCH_BYTES, True)],
            second_repo: [("bge-m3-F16.gguf", MATCH_BYTES, True)],
        },
    )
    install_fake_hub(monkeypatch, client)

    result = runner.invoke(app, ["discover", "--match-ollama", "bge-m3"])

    assert result.exit_code == 0
    output = output_of(result)
    assert "2 byte-identical matches — all the same bytes; run any ONE to archive it:" in output
    assert f"llm-preserver pull {second_repo} --include bge-m3-F16.gguf" in output


def test_match_footer_carries_the_repos_hub_facts(tmp_path, monkeypatch, fake_hub_factory):
    # Identical bytes leave provenance as the only pick criterion:
    # each footer match shows the same facts a search row shows
    # (downloads · date · gated), never a ranking (adjudicated
    # 2026-07-31).
    make_store(tmp_path, monkeypatch)
    popular = ModelSummary(
        repo_id=MATCH_REPO,
        downloads=181142,
        last_modified="2025-07-14T09:00:00.000Z",
        gated="manual",
        base_model=None,
    )
    client = fake_hub_factory(
        search_results=[popular],
        repos={MATCH_REPO: [(MATCH_FILE, MATCH_BYTES, True)]},
    )
    install_fake_hub(monkeypatch, client)

    result = runner.invoke(app, ["discover", "--match-ollama", "bge-m3"])

    assert result.exit_code == 0
    output = output_of(result)
    footer = output.split("byte-identical match", 1)[1]
    assert "181142 downloads" in footer
    assert "2025-07-14" in footer
    assert "gated" in footer
