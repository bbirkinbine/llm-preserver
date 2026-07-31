"""CLI behavior of ``discover --match-ollama`` — spec 0013 phase A.

Match mode reads the model-layer digest from a local Ollama manifest
(fake store in ``tmp_path`` via ``$OLLAMA_MODELS``), runs the normal
hub name search, fetches candidate file listings (metadata only), and
states facts per candidate GGUF: byte-identical, or unverified with
size. Hub order is kept — never a ranking. Match mode touches no
archive and takes no positionals. CliRunner + fake hub client, no
network; output ``click.unstyle``d before substring asserts."""

import contextlib
import hashlib
import json
from pathlib import Path

import click
from typer.testing import CliRunner

from llm_preserver import ollama_layout
from llm_preserver.cli import app
from llm_preserver.hub import PullHubError
from llm_preserver.hub_discovery import ModelSummary

runner = CliRunner()

# Same byte length on purpose: the near-miss is same-size,
# different-digest — visibly a different build, never a match.
MATCH_BYTES = b"m" * 96
NEAR_MISS_BYTES = b"n" * 96
MATCH_DIGEST = hashlib.sha256(MATCH_BYTES).hexdigest()

MATCH_REPO = "gpustack/bge-m3-GGUF"
NEAR_REPO = "compendium/bge-m3-gguf"
MATCH_FILE = "bge-m3-FP16.gguf"
NEAR_FILE = "bge-m3-f16.gguf"


def combined_output(result) -> str:
    """stdout plus stderr when captured separately (click version dependent)."""
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return out


def output_of(result) -> str:
    return click.unstyle(combined_output(result))


def stderr_of(result) -> str:
    """Unstyled stderr; falls back to combined output on older click."""
    try:
        return click.unstyle(result.stderr)
    except (ValueError, AttributeError):
        return output_of(result)


def line_with(output, *fragments):
    """Return the single output line containing every fragment."""
    matches = [line for line in output.splitlines() if all(f in line for f in fragments)]
    assert len(matches) == 1, f"expected exactly one line with {fragments!r} in:\n{output}"
    return matches[0]


def install_fake_hub(monkeypatch, client):
    import llm_preserver.cli as cli_module

    monkeypatch.setattr(cli_module, "HubClient", lambda: client)


def summary(repo_id):
    return ModelSummary(
        repo_id=repo_id, downloads=None, last_modified=None, gated=None, base_model=None
    )


def make_store(tmp_path: Path, monkeypatch, *, model="bge-m3", digest=MATCH_DIGEST) -> Path:
    """Fake Ollama store with one manifest; env points the tool at it."""
    store = tmp_path / "ollama-store"
    manifest = (
        store
        / ollama_layout.MANIFESTS_DIRNAME
        / ollama_layout.REGISTRY_DIRNAME
        / ollama_layout.DEFAULT_NAMESPACE
        / model
        / ollama_layout.DEFAULT_TAG
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "layers": [
                    {
                        "mediaType": "application/vnd.ollama.image.model",
                        "digest": f"sha256:{digest}",
                        "size": len(MATCH_BYTES),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(ollama_layout.MODELS_ENV_VAR, str(store))
    # Match mode needs no archive; make sure none leaks in from the env.
    monkeypatch.delenv("LLM_PRESERVER_ARCHIVE", raising=False)
    return store


def make_client(fake_hub_factory, **overrides):
    overrides.setdefault("search_results", [summary(MATCH_REPO), summary(NEAR_REPO)])
    overrides.setdefault(
        "repos",
        {
            MATCH_REPO: [(MATCH_FILE, MATCH_BYTES, True)],
            NEAR_REPO: [(NEAR_FILE, NEAR_MISS_BYTES, True)],
        },
    )
    return fake_hub_factory(**overrides)


def invoke_match(name="bge-m3", *args):
    return runner.invoke(app, ["discover", "--match-ollama", name, *args])


def test_match_mode_rejects_positional_arguments(tmp_path, monkeypatch, fake_hub_factory):
    make_store(tmp_path, monkeypatch)
    client = make_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_match("bge-m3", "stray-arg")

    assert result.exit_code == 2
    assert "positional" in output_of(result).lower()
    assert client.search_calls == []


def test_search_option_without_match_ollama_exits_2(tmp_path, monkeypatch, fake_hub_factory):
    archive = tmp_path / "archive"
    archive.mkdir()
    assert runner.invoke(app, ["init", str(archive)]).exit_code == 0
    client = make_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = runner.invoke(app, ["discover", "tiny", str(archive), "--search", "custom"])

    assert result.exit_code == 2
    assert "--match-ollama" in output_of(result)
    assert client.search_calls == []


def test_byte_identical_file_is_annotated_as_such(tmp_path, monkeypatch, fake_hub_factory):
    make_store(tmp_path, monkeypatch)
    client = make_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_match()

    assert result.exit_code == 0
    line_with(output_of(result), MATCH_FILE, "byte-identical")


def test_match_prints_pasteable_pull_command(tmp_path, monkeypatch, fake_hub_factory):
    make_store(tmp_path, monkeypatch)
    install_fake_hub(monkeypatch, make_client(fake_hub_factory))

    result = invoke_match()

    assert result.exit_code == 0
    line_with(output_of(result), f"llm-preserver pull {MATCH_REPO} --include", MATCH_FILE)


def test_candidates_keep_hub_order_even_when_the_match_is_second(
    tmp_path, monkeypatch, fake_hub_factory
):
    # The hub lists the near-miss repo first; the annotation must not
    # promote the matched repo — facts, never a ranking (spec 0013).
    make_store(tmp_path, monkeypatch)
    client = make_client(fake_hub_factory, search_results=[summary(NEAR_REPO), summary(MATCH_REPO)])
    install_fake_hub(monkeypatch, client)

    result = invoke_match()

    assert result.exit_code == 0
    output = output_of(result)
    assert output.index(NEAR_REPO) < output.index(MATCH_REPO)


def test_same_size_different_digest_prints_unverified_with_size(
    tmp_path, monkeypatch, fake_hub_factory
):
    make_store(tmp_path, monkeypatch)
    install_fake_hub(monkeypatch, make_client(fake_hub_factory))

    result = invoke_match()

    assert result.exit_code == 0
    near_line = line_with(output_of(result), NEAR_FILE)
    assert "unverified" in near_line
    assert str(len(NEAR_MISS_BYTES)) in near_line
    assert "byte-identical" not in near_line


def test_match_mode_downloads_nothing(tmp_path, monkeypatch, fake_hub_factory):
    make_store(tmp_path, monkeypatch)
    client = make_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_match()

    assert result.exit_code == 0
    assert client.download_calls == []


def test_search_term_defaults_to_name_with_tag_stripped(tmp_path, monkeypatch, fake_hub_factory):
    make_store(tmp_path, monkeypatch)
    client = make_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_match("bge-m3:latest")

    assert result.exit_code == 0
    assert client.search_calls == ["bge-m3"]


def test_search_option_overrides_the_default_term(tmp_path, monkeypatch, fake_hub_factory):
    make_store(tmp_path, monkeypatch)
    client = make_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_match("bge-m3", "--search", "custom-term")

    assert result.exit_code == 0
    assert client.search_calls == ["custom-term"]


def test_no_exact_match_prints_footer_and_exits_0(tmp_path, monkeypatch, fake_hub_factory):
    # Local digest matches nothing on the hub: still a successful scan
    # reporting facts, not a failure (spec 0013, confirmed 2026-07-31).
    make_store(tmp_path, monkeypatch, digest=hashlib.sha256(b"unrelated local build").hexdigest())
    install_fake_hub(monkeypatch, make_client(fake_hub_factory))

    result = invoke_match()

    assert result.exit_code == 0
    output = output_of(result).lower()
    assert "no exact match" in output
    assert "different build" in output
    assert str(len(MATCH_BYTES)) in output  # sizes still listed


def test_candidate_with_failing_metadata_is_noted_and_others_still_print(
    tmp_path, monkeypatch, fake_hub_factory
):
    make_store(tmp_path, monkeypatch)
    client = make_client(
        fake_hub_factory, repo_info_errors={NEAR_REPO: PullHubError("hub returned 500")}
    )
    install_fake_hub(monkeypatch, client)

    result = invoke_match()

    assert result.exit_code == 0
    output = output_of(result)
    line_with(output, NEAR_REPO, "metadata unavailable")
    line_with(output, MATCH_FILE, "byte-identical")


def test_missing_store_is_a_clean_exit_2_naming_the_lookup(tmp_path, monkeypatch, fake_hub_factory):
    missing_store = tmp_path / "no-store-here"
    monkeypatch.setenv(ollama_layout.MODELS_ENV_VAR, str(missing_store))
    monkeypatch.delenv("LLM_PRESERVER_ARCHIVE", raising=False)
    client = make_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_match()

    assert result.exit_code == 2
    err = stderr_of(result)
    assert "bge-m3" in err
    assert str(missing_store) in err
    assert "Traceback" not in output_of(result)
    assert client.search_calls == []


def test_unknown_ollama_model_is_a_clean_exit_2_naming_the_lookup(
    tmp_path, monkeypatch, fake_hub_factory
):
    store = make_store(tmp_path, monkeypatch, model="some-other-model")
    client = make_client(fake_hub_factory)
    install_fake_hub(monkeypatch, client)

    result = invoke_match("bge-m3")

    assert result.exit_code == 2
    err = stderr_of(result)
    assert "bge-m3" in err
    assert str(store) in err
    assert "Traceback" not in output_of(result)
    assert client.search_calls == []
