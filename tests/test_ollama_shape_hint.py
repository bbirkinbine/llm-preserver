"""Ollama-shape hint composition — spec 0013 phase B, unit level.

``ollama_shape_hint`` inspects a raw id that the hub's own validator
already rejected and, when the id is Ollama-shaped, composes a courtesy
hint pointing at the deterministic recovery command: ``name:tag`` gets
``discover``/``discover --match-ollama`` pointers, ``hf.co/<org>/<repo>``
gets the exact mechanical ``pull`` translation. Detection only: no id
is ever rewritten, nothing here talks to a network, and an id that is
not Ollama-shaped (or whose embedded repo id is not a valid pasteable
token — 0007 rule) yields None, never a guess and never an exception.

Split from test_ollama_store.py preemptively (300-line rule).
"""

import pytest

from llm_preserver.ollama_store import ollama_shape_hint


def tokens_of(hint: str) -> list[str]:
    """Whitespace tokens with shell quoting stripped, for token asserts."""
    return [tok.strip("'\"") for tok in hint.split()]


# --- name:tag shape --------------------------------------------------------


def test_name_tag_hint_says_it_looks_like_an_ollama_name():
    hint = ollama_shape_hint("qwen3-vl:30b-a3b-instruct")

    assert hint is not None
    assert "Ollama" in hint


def test_name_tag_hint_says_no_direct_hugging_face_equivalent():
    hint = ollama_shape_hint("qwen3-vl:30b-a3b-instruct")

    assert hint is not None
    assert "Hugging Face" in hint


def test_name_tag_hint_points_discover_at_the_tag_stripped_name():
    hint = ollama_shape_hint("qwen3-vl:30b-a3b-instruct")

    assert hint is not None
    assert "discover" in hint
    # The tag-stripped model name must appear as its own token — not
    # merely as the prefix of the full name:tag form.
    assert "qwen3-vl" in tokens_of(hint)


def test_name_tag_hint_points_match_ollama_at_the_full_name_tag():
    hint = ollama_shape_hint("qwen3-vl:30b-a3b-instruct")

    assert hint is not None
    assert "--match-ollama" in hint
    assert "qwen3-vl:30b-a3b-instruct" in hint


# --- hf.co/<org>/<repo>[:<quant>] shape -------------------------------------


def test_hf_co_with_quant_hint_carries_the_exact_pull_translation():
    hint = ollama_shape_hint("hf.co/unsloth/Qwen3-8B-GGUF:Q4_K_M")

    assert hint is not None
    assert "pull unsloth/Qwen3-8B-GGUF" in hint


def test_hf_co_with_quant_hint_mentions_the_quant_as_an_include():
    hint = ollama_shape_hint("hf.co/unsloth/Qwen3-8B-GGUF:Q4_K_M")

    assert hint is not None
    assert "--include" in hint
    assert "Q4_K_M" in hint


def test_hf_co_without_quant_hint_has_pull_translation_and_no_include():
    hint = ollama_shape_hint("hf.co/unsloth/Qwen3-8B-GGUF")

    assert hint is not None
    assert "pull unsloth/Qwen3-8B-GGUF" in hint
    assert "--include" not in hint


# --- not Ollama-shaped: no hint ---------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "nonsense",  # plain word: no colon, no hf.co prefix
        "org/repo",  # a valid-looking hub id gets no second opinion
        "",  # empty input
        # 0007 rule: never compose a pasteable command around a token
        # that is not shaped like a hub repo id ('--yes' would parse as
        # a flag on paste).
        "hf.co/--yes:Q4",
        # Same rule on the name:tag side (security round 2026-07-31):
        # a leading dash would put a flag-shaped token into the paste
        # lines bare.
        "--yes:latest",
        "-h:latest",
    ],
)
def test_non_ollama_shapes_produce_no_hint(raw):
    assert ollama_shape_hint(raw) is None


# --- hostile and degenerate input --------------------------------------------


def test_ansi_escapes_never_reach_the_hint():
    hint = ollama_shape_hint("evil\x1b[31m:tag")

    assert hint is None or "\x1b" not in hint


@pytest.mark.parametrize("raw", [":", "a:b:c", "hf.co/"])
def test_degenerate_input_never_raises(raw):
    result = ollama_shape_hint(raw)

    assert result is None or isinstance(result, str)
