# 0016 — Artifact Classification And Lineage

**Status:** draft
**Last updated:** 2026-08-06

## Goal

Make the archive able to answer, from its own contents and with no
network, two questions it currently cannot: **"will this artifact
actually run?"** and **"can I still derive other forms of it?"** Today a
model directory holding one `Q4_K_M` GGUF reports `ok` under `status`
and `valid` under `verify`, both truthfully, while being a lossy
one-way export that no future quantization or fine-tune can start from.
The facts that would say so already exist — spec 0005's advisories read
the hub `base_model` pointer and `adapter_config.json`, and spec 0006's
grouping already distinguishes a conversion from a derivative — but they
are computed once at pull time, printed, and discarded. This spec
records the small set of facts only the download moment knows, derives
the human-facing verdicts from them at read time, and renders the
conclusions into `MODEL-RECORD.md` so a person with `cat` and no
`llm-preserver` can still read them years from now. Design is governed
by [ADR 0002](../adr/0002-artifact-classification-and-lineage.md);
this spec implements it and does not re-argue it.

## Success criteria

Behavioral, in terms of the running example
(`DeepHat/DeepHat-V1-7B`, verified in `## External references`):

1. After `pull <gguf-repo> --include '*Q4_K_M*'`, the model directory's
   `MODEL-RECORD.md` states in plain prose that no full-precision source
   is archived, that other quants cannot be derived from this directory,
   and names the repo that would provide one — without the tool being
   run again.
2. After additionally `pull DeepHat/DeepHat-V1-7B --whole-repo` into the
   same model directory, that same file states that other quants *can*
   be derived, naming the archived artifact that serves as the source.
   No manual edit, no flag: re-rendering on the second pull is enough.
3. `show <model>` prints the same per-artifact verdict as the rendered
   markdown; the two cannot disagree, because both read one derivation.
4. `status` answers derivability at a glance for every model on the
   shelf.
5. Each artifact records `artifact_type` **and the marker that decided
   it**. Given `adapter_config.json`, the type is `adapter` with
   evidence naming that marker; given a full safetensors set and no
   adapter config, the type is `full-weights`.
6. When no marker qualifies, the recorded type is `unknown` and the
   rendered text says the tool could not determine it — never phrasing
   that implies either answer. A guess is a defect.
7. An adapter's record carries its base as a `{repo_id, revision}` pair,
   and the archive reports the dependency as satisfied only when that
   base is archived **at that revision**. A base present at a different
   revision reports unsatisfied, naming both revisions.
8. Lineage depth does not change the answer: `DeepHat/DeepHat-V1-7B`
   declares a base two levels below `Qwen/Qwen2.5-7B` and still reports
   self-sufficient, because it carries full weights of its own.
9. No command added or changed by this spec initiates a download of a
   base model, an ancestor, or anything the human did not name.
10. A record written before this change loads unchanged and reports "not
    classified" — distinct in both the JSON and the rendered text from
    "classified, markers inconclusive". Unknown extra fields still
    survive a load/modify/save round-trip.
11. `verify` exit codes and vocabulary are unchanged. Derivability is
    not a fixity problem and must not alter a single existing exit code.

## Non-goals

- **Running any part of the reconstruction.** The tool says what you
  need; it never invokes `convert_hf_to_gguf.py`, `llama-quantize`,
  `llama-imatrix`, or any trainer. Those commands appear in
  documentation only.
- **Automatic, recursive, or transitive pulling** of base models or
  ancestors. Recording an edge must never cause a download — the human
  picks every pull. Offering a base as a *choice* is spec 0002's
  post-pull-offer territory, not this one.
- **Detecting `merged` as a distinct type.** Whether full weights came
  from full fine-tuning or from merging a LoRA is a claim about history,
  not a property of the files; no marker decides it, so under the 0000
  no-judgment invariant the tool must not claim it. See `## Notes`.
- **Archiving the toolchain.** ADR 0001's `runtimes/` directory is the
  right home for a pinned `llama.cpp` build, and it stays empty here.
  "The weights are archived" is the claim; "your future conversion will
  succeed" is not.
- **Tracking imatrix calibration corpora.** The existing companion
  advisory already surfaces `*imatrix*` files; modelling the datasets
  behind them is out.
- **A `reclassify` / backfill command** for records written before this
  change. Their fields stay null. Deferred — see `## Notes`.
- **Populating the `quantization` field** per file (a separately queued
  TODO item), beyond what classification itself needs.
- **`--json` output.** Separately queued; this spec changes prose
  surfaces only.
- **The `archive_policy` field.** ADR 0002 defines it as the one
  curator-judgment field (`runnable | reconstruction |
  provenance-only`), but nothing in the CLI would set it, no success
  criterion above depends on it, and a judgment field with no writer is
  a null column. Deferred to a later spec, which should ship the field
  and the command that populates it together. The ADR's facts-vs-
  judgment split still governs: nothing here fabricates it.

## External references

Fetched in-session 2026-08-06 (summaries, not verbatim copies):

- **llama.cpp quantization pipeline** —
  <https://github.com/ggml-org/llama.cpp/blob/master/tools/quantize/README.md>,
  retrieved 2026-08-06, license: MIT. Establishes that `llama-quantize`
  consumes a high-precision GGUF (F32/BF16), that
  `convert_hf_to_gguf.py --outtype bf16` produces one from a Hugging
  Face checkpoint, and that `--imatrix` takes a separate file. This is
  what makes "full-precision source present" the correct definition of
  derivable.
- **DeepHat lineage and file tree** —
  <https://huggingface.co/DeepHat/DeepHat-V1-7B> and
  <https://huggingface.co/DeepHat/DeepHat-V1-7B/tree/main>, retrieved
  2026-08-06, license: Apache-2.0 + DeepHat extension. Establishes the
  running example: `base_model: Qwen/Qwen2.5-Coder-7B` (itself from
  `Qwen/Qwen2.5-7B`), no `adapter_config.json`, four BF16 safetensors
  shards totalling 15.2 GB.

- **GGUF filename naming convention** —
  <https://github.com/ggml-org/ggml/blob/master/docs/gguf.md>, retrieved
  2026-08-06, license: MIT per the ggml repository (consult-only here;
  nothing is copied verbatim). Specifies the filename structure
  `[<Sidecar>]<BaseName><SizeLabel><FineTune><Version><Encoding><Type><Shard>.gguf`,
  which is what makes the `<Encoding>` component — `F16`, `BF16`,
  `Q4_K_M`, `Q4_0`, `KQ2` in its own examples — parseable from a
  filename at a defined position rather than by scanning for substrings.
  **It does not enumerate the valid encoding labels**, only illustrates
  them; see the marker rule below for why that is sufficient.

**Must be fetched during implementation, not reconstructed** (each
decides a marker, so a wrong value is a wrong classification):

- The PEFT `adapter_config.json` schema, beyond the
  `base_model_name_or_path` field `pull_metadata.py` already reads —
  needed to know which keys reliably identify an adapter repo.
- The **full-precision** GGUF encoding labels specifically (`F32`,
  `F16`, `BF16` and any sibling), from the `llama.cpp` type list. The
  classifier does not need the exhaustive quant vocabulary: a parsed
  encoding in the full-precision set is `full-weights`, any *other*
  parsed encoding is `quant`, and a filename with no parseable encoding
  is `unknown`. Only the short full-precision set has to be right, and
  it must come from a fetched source with a provenance comment at the
  table, never from memory.
- Hugging Face model-card `base_model` field semantics, including how
  multiple values are expressed, which decides `parent_models`.

Not verified and not load-bearing for this spec: whether non-GGUF
quantization targets (FP8 via vLLM / TensorRT-LLM) consume the same
full-precision safetensors input. Asserted in discussion; it is
mentioned in documentation only, never in a recorded verdict.

## Sketch

Per ADR 0002: store only the facts the download moment knows —
`artifact_type` with its evidence, `base_model`, `parent_models`, on
`ArtifactEntry` — at record schema v3, a pure widening. Derive every
verdict at read time: "needs a base" from the type, "base satisfied"
from an archive-wide lookup, "derivable" from whether a sibling artifact
in the same record is a full-precision source. One derivation function
feeds the markdown renderer, `show`, and `status`, so they cannot
disagree. `MODEL-RECORD.md` carries the conclusions as prose because it
is the surface that has to outlive the tool.

## Implementation plan (phase 1)

Planned 2026-08-06; decisions below all confirmed by Brian the same
day. Roughly 15 files, so it runs as **four passes, each independently
green and committable** — do not attempt it in one session.

1. **Schema v3, a pure widening.** `records/base.py` (move
   `_PreservingModel` out to break a `schema` ↔ `classification` import
   cycle), `records/classification.py` (the `ArtifactType` /
   `ClassificationEvidence` Literals, `Classification`, `LineageRef`,
   `CLASSIFICATION_RULE_VERSION`), the `RECORD_SCHEMA_VERSION` bump,
   three nullable fields on `ArtifactEntry`. Nothing user-visible
   changes. Also fixes a latent bug found while planning: `update_record`
   never resets `record_schema_version`, so a re-pulled v1 record keeps
   claiming v1 while gaining v2 fields.
2. **Classifier plus pull wiring.** `classification/markers.py` —
   `classify_artifact(format, file_paths)`, a pure function of format
   and paths with no hub input, so `remove` can re-run it offline.
   Wired through `pull_prepare` / `pull.py` / `pull_record`, and
   re-run by `remove/execute.py` on any artifact whose file list
   changed. **Hard ordering constraint:** classification must be set
   inside `update_record` *before* `write_manifest`, because the
   manifest hashes the record serialization it anticipates — a
   post-manifest mutation makes `verify` report an invalid record
   permanently.
3. **Derivation plus prose.** `classification/verdicts.py` is the one
   read-time derivation, consumed by the renderer, `show`, and later
   `status`: `artifact_verdicts(record)` (record-local) and
   `base_satisfaction(verdict, archived)` (archive-wide, fed by a new
   `classification/archive_index.py` that `pull_advisory` also
   delegates to, retiring its duplicate walk and dropping that file
   back under the 300-line cap it currently exceeds at 304).
4. **Hardening and docs.** Criterion 11 regression, `/security` (this
   parses hub-supplied data and writes archive files), the docs sweep
   (`docs/data-structures.md`'s class diagram and hardcoded schema
   version, `docs/cli.md` → `show`, `docs/what-to-archive.md`), close-out.

Resolved design questions, so a later session does not reopen them:

| Question | Decision |
| --- | --- |
| Base revision usually unknown (cards name a repo, not a commit) | **Never fabricate a pin.** Resolving the base's *current* commit would record a revision the model was not trained against. Report `satisfied-unpinned` and say so in words. |
| Does `MODEL-RECORD.md` state whether the base is archived? | **No.** That fact is archive-wide and goes stale on the next pull or an rsync elsewhere, breaking ADR 0001's records-travel-with-the-model. The markdown carries record-local conclusions; `show` appends the archive-wide line. |
| One artifact holding both a Q4 and an f16 GGUF | **Full precision wins** — it is what makes the directory derivable. |
| Multi-value card `base_model` | **Widen `RepoInfo`** with a defaulted `base_models` list; `base_model` stays the first value so grouping is byte-identical. Without this, `parent_models` only duplicates `base_model` and should not exist. |
| v2 → v3 rewrite when an untouched model is re-pulled or partially removed | **Accepted.** Equivalent content, new bytes and manifest line; the alternative keeps two formats alive indefinitely. |
| Evidence vocabulary | Add `weight-file-set` for `full-weights`, and **reserve `gguf-metadata` and `size-ratio` in the Literal now** so the deferred refinements are value changes, not schema changes. |

## Notes

- **Taxonomy: settled 2026-08-06 (Brian).** `artifact_type` is
  `adapter | quant | full-weights | unknown`, collapsed from the
  five-value list this work was queued with. `merged` is a claim about
  history that no file proves, and `full-checkpoint` versus `base` is
  lineage rather than artifact type. ADR 0002 carries the full
  reasoning and was amended to match; if a *structured* merge marker
  turns up later, `merged` may return as marker-proven-only, never as
  the default for full weights of unknown history.
- **GGUF precision: filename label this spec, header parsing
  deferred** (decided 2026-08-06). Whether an archived `bf16`/`f16`
  GGUF counts as a full-precision source matters, because
  [`docs/cli.md`](../cli.md)'s goal table recommends exactly that
  archive shape. The GGUF header is authoritative and the filename
  label is cheap; this spec takes the filename, records the weaker
  evidence honestly rather than presenting it as certain, and returns
  `unknown` for an unlabelled GGUF. Header parsing is a self-contained
  later refinement that can upgrade the evidence without changing the
  schema — and the `unknown` verdicts it would fix are exactly the
  records a future `reclassify` should revisit.
- **Phase split** (decided 2026-08-06): phase 1 is record + derive +
  render — criteria 1–3 and 5–11. Phase 2 is the `status` shelf view,
  criterion 4 alone. This touches the record schema, the classifier,
  the markdown renderer, `show`, `status`, and the pull path, which is
  comfortably a Large task under `CLAUDE.md`'s table, so it should not
  run in one session. `/plan` may re-cut the seam if it finds a better
  one, but phase 1 must stand alone: criteria 1 and 2 are the live
  question this spec exists to answer, and neither needs `status`.
- **Deferred, and worth queueing when this lands:** a `reclassify`
  command for pre-v3 records. Backfill is only partial anyway — an
  archived `hf-snapshot` still has its `adapter_config.json` on disk,
  but a GGUF-only model archived earlier has no card and no marker, and
  its lineage cannot be recovered offline.
- **Companion documentation** ships with this work:
  [`docs/what-to-archive.md`](../what-to-archive.md) explains the
  quant → full backup → re-derivation path in plain terms with DeepHat
  as the worked example. It documents behavior that already exists
  today, so it is valid before this spec lands and gains a section when
  it does.
