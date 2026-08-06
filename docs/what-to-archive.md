# What to archive

A quant you can run is not the same thing as a model you have kept.
This page explains the difference, using one real model end to end, and
shows how to check which of the two you currently have on the shelf.

The short version: **quantization is a one-way door.** Archive the
full-precision weights if you want any future beyond the exact file you
downloaded.

## The one-way problem

Think of a `Q4_K_M` GGUF as a JPEG export and the model's original
weights as the RAW file.

From the RAW you can produce any JPEG you like — large, small, whatever
quality you need. From a JPEG you can only produce a worse JPEG. There
is no path back.

Quantization works the same way. `llama-quantize` takes a
**high-precision GGUF** (F32, F16, or BF16) as its input and writes a
smaller one. Nothing reads a `Q4_K_M` and reconstructs the precision
that was thrown away. So a model directory holding only `Q4_K_M` is a
model you can run today and can never re-derive anything from —
no other quant, no fine-tune, no conversion to a different runtime's
format.

This is invisible to the existing integrity commands, and correctly so:
`status` reports `ok` because the record is complete, and `verify`
reports `valid` because every checksum matches. Both are true. Neither
is answering the question on this page.

## Three goals, three shopping lists

What you need in the archive depends on what you want to still be able
to do later. The goals compose — most models on a shelf want row 1, and
the ones you care about long-term want row 3 as well.

| Goal | Archive this | Why |
| --- | --- | --- |
| Run it locally | one quant that fits your hardware | The file you actually load |
| Re-make any quant later, offline | the model's full-precision weights (safetensors), or a BF16/F16 GGUF | The input `llama-quantize` needs |
| Fine-tune or re-train it later | the model's full-precision safetensors | Training needs precision a quant no longer has |

Rows 2 and 3 are usually satisfied by the same single download, which
is why the practical advice collapses to: **pull the original repo with
`--whole-repo` for anything you want to keep.**

Two things worth adding when they exist:

- **An `*imatrix*` file**, if the quant repo publishes one. Importance
  matrices are an optional input to `llama-quantize` that reduce
  accuracy loss, and they are not derivable from the weights — the
  calibration data behind them is a separate artifact. The existing
  companion advisory flags these during a pull.
- **A BF16 or F16 GGUF**, if the repo publishes one. Strictly a
  convenience: it is derivable from the safetensors, and it saves you
  the conversion step later.

## Worked example: DeepHat

`DeepHat/DeepHat-V1-7B` is a good example because its lineage looks
alarming and turns out not to matter.

**What the model declares.** Its card names
`base_model: Qwen/Qwen2.5-Coder-7B`, which is itself a fine-tune of
`Qwen/Qwen2.5-7B`. Two levels of ancestry above it.

**What is actually in the repo.** Four BF16 safetensors shards
totalling 15.2 GB, plus `config.json`, the tokenizer files, and a chat
template. No `adapter_config.json`.

**What that means.** 15.2 GB of BF16 for a roughly 7.6B-parameter model
is about two bytes per parameter — the complete weight set, not a small
delta on top of something else. DeepHat carries its own full weights.
Every quant anyone will ever want is derivable from this one repo.

So if you have pulled a `Q4_K_M` of it and want a real backup:

```bash
llm-preserver pull DeepHat/DeepHat-V1-7B --whole-repo ~/models
```

That is the entire answer. Keep the Q4 too — it is the file you load
day to day. The full-precision pull is the negative in the drawer.

### Why Qwen is not on the list

The instinct with two levels of ancestry is that preserving the
re-quantization path means walking the chain: grab DeepHat, then
Qwen2.5-Coder-7B, then Qwen2.5-7B. It does not.

**You re-quantize from the weights of the artifact itself, never from
its ancestors.** DeepHat's weights are complete, so its ancestors add
nothing to that goal. They are provenance — the record of where the
model came from, worth *knowing* and not worth 30 GB to *hold*.

The one case where ancestry becomes a hard dependency is an adapter;
see below. `llm-preserver` never pulls an ancestor on its own, and the
advisory that mentions a parent repo says so in as many words ("not
required for this pull").

## Checking what you already have

Three ways, cheapest first.

**1. The formats column in `status`.** This is the fast shelf-wide
check:

```
model                    formats            roles  size     completeness
DeepHat/DeepHat-V1-7B    gguf               -      4.4 GiB  ok
Qwen/Qwen3.6-27B         gguf,hf-snapshot   chat   61 GiB   ok
```

`gguf` alone means quants only — nothing to re-derive from. Once the
full-precision pull lands in the same model directory the cell reads
`gguf,hf-snapshot`, and that model is covered. Note that both rows say
`ok` in the completeness column: that column is about the record's
health, not about derivability.

**2. Re-run the plan against the quant repo you pulled from.**

```bash
llm-preserver pull <the-gguf-repo> --include '*Q4_K_M*' --plan
```

`--plan` downloads nothing and reprints the advisories, including the
full-precision-master row, which names the exact command to fix the
gap. This works whenever the quant repo declares its base model, which
most third-party quant repos do.

**3. `show <model>`** lists every archived artifact for one model. One
`gguf` entry and no `hf-snapshot` is the same signal, read by eye.

## Deriving other quants later, offline

Once the full-precision weights are archived, producing another quant
is two steps and no network. `llm-preserver` does not run these for you
— it preserves the inputs they need.

```bash
# 1. Convert the archived safetensors to a high-precision GGUF
python convert_hf_to_gguf.py ~/models/models/DeepHat/DeepHat-V1-7B/hf-snapshot \
  --outfile deephat-bf16.gguf --outtype bf16

# 2. Quantize it to whatever you need
llama-quantize deephat-bf16.gguf deephat-Q4_K_M.gguf Q4_K_M
llama-quantize deephat-bf16.gguf deephat-Q8_0.gguf   Q8_0

# with an importance matrix, if you archived one
llama-quantize --imatrix imatrix.gguf deephat-bf16.gguf deephat-Q4_K_M.gguf Q4_K_M
```

Flag names and script paths shift between `llama.cpp` releases; treat
the upstream
[`tools/quantize/README.md`](https://github.com/ggml-org/llama.cpp/blob/master/tools/quantize/README.md)
as the current authority and the above as the shape of the pipeline.

Formats outside the GGUF family — FP8 for vLLM or TensorRT-LLM, for
instance — go through their own toolchains, but they start from the
same place: the full-precision safetensors. The shopping list does not
change.

## When you do need the base model

The exception is a **LoRA adapter**. An adapter is a small set of
weight deltas, not a model — a few hundred MB that mean nothing without
the weights they were trained against. To use or convert one you need
the base model, and you need it at the **exact revision** the adapter
was trained on, because a base repo that has been updated since may no
longer combine correctly.

The reliable marker is a root-level `adapter_config.json` in the repo.
When one is present, `llm-preserver` fetches it during planning to read
its base-model pointer and raises an advisory naming that base. A
secondary tell is size: a repo whose weights are a small fraction of
the model it claims to be is a delta, not a checkpoint.

DeepHat has no `adapter_config.json` and carries a full 15.2 GB of
weights, which is why it needs nothing above it.

## What the archive cannot promise

Archiving the weights makes a future conversion *possible*. It does not
make it *certain*.

`convert_hf_to_gguf.py` evolves, and a future `llama.cpp` may drop
support for an architecture it handles today. Preserving a working
build of the toolchain alongside the weights is the answer to that, and
it is a gap: ADR 0001 reserves a `runtimes/` directory for exactly this
purpose and nothing populates it yet.

So read the derivability signal as "the full-precision source is
archived" — necessary, and the part you can do something about — rather
than "this will definitely work in ten years."

## See also

- [`cli.md`](cli.md) — the full command reference, including the
  advisory rules and `--plan`
- [`adr/0001-model-storage.md`](adr/0001-model-storage.md) — why the
  archive is laid out the way it is, and the `runtimes/` gap above
- [`adr/0002-artifact-classification-and-lineage.md`](adr/0002-artifact-classification-and-lineage.md)
  and [`specs/0016-artifact-classification-and-lineage.md`](specs/0016-artifact-classification-and-lineage.md)
  — the work that will make the archive state all of this about itself,
  in each model's `MODEL-RECORD.md`, without you having to run a command
  to ask
