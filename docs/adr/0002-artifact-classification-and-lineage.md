# 0002 — Artifact Classification And Lineage

**Status:** accepted
**Last updated:** 2026-08-06 (accepted by Brian; taxonomy collapsed to
four values before acceptance)

## Context

An archive can be `complete` and `valid` in the 0009 sense — every file
present, every SHA256 intact — and still fail the two questions that
decide whether preserving it accomplished anything.

**"Can I still produce other forms of this?"** This is the live trigger
(Brian, 2026-08-06): *I pulled a GGUF I use. What else do I need so I
can generate fp16, Q8, Q4 later?* If the archive holds only a
`Q4_K_M`, the answer is *everything* — quantization is lossy and
one-way. `llama-quantize` takes a high-precision GGUF (F32/BF16) as its
input; there is no path back up from Q4. The shelf reads `ok` under
`status` and `valid` under `verify`, and neither vocabulary has a word
for "runnable forever, re-derivable never."

**"Will it even load?"** A LoRA adapter without its base model is
bit-perfect and useless, as is a merged checkpoint whose base the
archive holds only at the wrong revision.

**Lineage depth is a red herring for both.** Worked example, verified
in-session (see `## References`): `DeepHat/DeepHat-V1-7B` declares
`base_model: Qwen/Qwen2.5-Coder-7B`, which is itself finetuned from
`Qwen/Qwen2.5-7B` — two levels up. The instinct is that preserving the
re-quantization path means walking that chain. It does not. The repo
carries no `adapter_config.json` and holds four BF16 safetensors shards
totalling 15.2 GB for a 7.6B model — the complete weight set. Every
quant anyone will ever want is derivable from that one repo, and both
Qwen levels are provenance. Had it been a LoRA adapter, the answer
inverts and the base *at the exact revision* becomes mandatory. So the
fact that decides the shopping list is **the artifact's own type plus
what the archive already holds** — neither of which the record stores.

**The tool already computes these facts and throws them away.** Spec
0005's advisory rules read the hub `base_model` pointer and fetch
`adapter_config.json` for its `base_model_name_or_path` (the adjudicated
exception to the one-metadata-call rule, tightened by 0014 to require a
hub-declared size), then warn once at pull time in
`pull_advisory._cross_repo_advisories`. Spec 0006's grouping goes
further and *acts* on the distinction —
`pull_grouping.propose_default_home` is format-directed: a GGUF/MLX tree
is a conversion and groups under the base, an `hf-snapshot` with a
`base_model` is a derived model and keeps its own home with the base as
lineage only. The tool already tells a conversion from a derivative and
already knows when a dependency is missing. It writes neither down, and
the person consulting the archive in five years is not the person who
watched the pull scroll by.

The other half of the trigger: tree discovery *displays* lineage, and a
displayed ancestry chain invites the reading that pulling a node implies
pulling its ancestors. It must not — the existing advisory wording
already fights this ("not required for this pull"; an ancestry chain is
per-level curation, never a dependency chain to the root). Recorded
lineage inherits that constraint.

This is ADR material rather than spec material because it changes
`model-record.json`, which ADR 0001 made the source of truth for every
feature — pull, status, show, verify, views, the queued smoke test — and
records already exist on disk across hundreds of GB, where a migration
is not a refactor.

What argues against deciding this now:

- **None of it is needed to store bytes.** It is metadata, and metadata
  can be wrong. A classifier that guesses would break the 0000
  invariant (no LLM, no tool judgment) in the one file the whole archive
  trusts; a confidently wrong `quant` is worse than no field at all.
- **0005 arguably already warns at the moment that matters** — before
  the download, when the human can still act. The counterweight is that
  the archive is read long afterward, by someone with no pull
  transcript.
- **Weights are necessary but not sufficient.** Even a perfect
  full-precision source does not guarantee a future conversion
  succeeds: `convert_hf_to_gguf.py` evolves, a future llama.cpp may drop
  an architecture, and an imatrix calibration corpus is not derivable
  from weights at all. Any field here risks over-promising.

## Decision

We will record classification and lineage as **additive,
evidence-tagged, artifact-level fields in `model-record.json` (record
schema v3)**, storing only what the download moment knows, deriving
every predicate at read time, treating `unknown` as a first-class
verdict, and treating lineage as a recorded edge the tool never
traverses.

**1. Store facts; derive predicates.** Three stored facts per artifact —
`artifact_type` (with its evidence), `base_model`, `parent_models`.
Every user-facing verdict is computed at read time instead:

| Question | Answered by | Why it is not stored |
| --- | --- | --- |
| Does this need a base to run? | a function of `artifact_type` | a second copy can disagree with the type |
| Is that base archived, at the pinned revision? | archive-wide lookup | changes when any *other* model is pulled or removed |
| Can I re-derive other quants of this? | sibling artifacts in the same record | changes the moment a snapshot lands in the same directory |

This is the same staleness argument that keeps model-level rollups out
(part 3): pull a BF16 snapshot into a directory holding only Q4 and a
stored `reconstructable: no` becomes a lie until something rewrites it.
The TODO entry that queued this work proposed `requires_base` and
`reconstructable` as stored fields; storing them is rejected here, and
the rendered output is identical either way.

**2. `reconstructable` is a record-local lookup, not a heuristic.** An
artifact is reconstructable-from-archive when the *same model directory*
holds a full-precision source for it: an `hf-snapshot` carrying the
model's weights, or a GGUF at F32/F16/BF16. That is exactly the input
the verified pipeline consumes — `convert_hf_to_gguf.py --outtype bf16`
produces the high-precision GGUF, `llama-quantize` turns it into
`Q4_K_M` and friends. No threshold, no size ratio, no judgment; a
quant-only directory answers `no`, deterministically and offline,
forever. This is the field the live trigger asked for, and it is the
one the archive can answer most cleanly.

Size-ratio survives only as a *fallback marker for `artifact_type`*
(adapter detection behind `adapter_config.json`), never as the basis for
this predicate.

**3. Artifact-level, not model-level.** The fields hang off
`ArtifactEntry`. The deciding case: one model directory can hold an
adapter's safetensors *and* a merged GGUF of the same lineage — one
needs a base, the other does not. The artifact, not the model, is the
thing that runs or doesn't. Model-level rollups are derived at read
time, never stored.

**4. Machine facts and curator judgment stay separate**, following the
`capabilities` vs `roles` split the schema already documents:

| Kind | Field | Written by |
| --- | --- | --- |
| Facts (marker-derived) | `artifact_type`, `base_model`, `parent_models` | the tool, from markers |
| Judgment | `archive_policy`: `runnable \| reconstruction \| provenance-only` | the human; never fabricated |

`archive_policy` answers "why is this on the shelf" — the question
`roles` answers — so it follows the same rule: null means unassigned,
not unknown.

**5. Every verdict carries its evidence**, mirroring the `provenance`
field on hashes, which already records *how* a hash was established
rather than only asserting it. Each artifact gets a classification
sub-object naming the marker behind the verdict — candidate vocabulary
`adapter-config`, `hub-base-model`, `gguf-metadata`, `size-ratio`,
`filename-quant-label` — plus the rule version that produced it.
<!-- assumption: a `classification: {type, evidence, rule_version}` sub-object rather than parallel `artifact_type` + `classified_by` scalars. The sub-object makes verdict and justification inseparable; the scalars read flatter in the JSON. -->
A threshold-derived verdict is then auditable, and a later rule revision
can find exactly the records it invalidates. **No qualifying marker
means `unknown`** — never a guess.

**6. `unknown` is a value; `null` is a schema state.** `"unknown"` means
the classifier ran and the markers were inconclusive; `null` means the
artifact was never classified, which is every v1 and v2 record. Keeping
them distinguishable lets a backfill find the records nobody has looked
at without re-adjudicating the rest. This is the schema's existing
nullable doctrine applied to a new field.

**7. Lineage is provenance, never a traversal.** `base_model` and
`parent_models` record `{repo_id, revision}` pairs rather than bare repo
ids — ADR 0001's revision lesson restated, and what makes "needs the
*exact* base revision" expressible at all. Recording an edge never
causes a pull. A missing dependency is a *reported state*, resolved by
the human choosing to pull.

### Where the answer surfaces

This ADR owns the record; the feature spec owns rendering. The
preservation premise constrains both, so the precedence is fixed here:

- **`MODEL-RECORD.md` is the surface that has to survive the tool.** ADR
  0001's whole premise is an archive legible with `ls` and `cat` after
  `llm-preserver` is gone, so the *conclusions* — "Q4_K_M quant; no
  full-precision source archived; other quants cannot be re-derived from
  this directory" — are rendered into the markdown as prose at
  generation time. **The JSON stores what only the download moment
  knows; the markdown states what a reader needs to conclude.**
- `model-record.json` holds the three facts, machine-readable.
- `pull --plan` and the 0005 advisories say it *before* the bytes land —
  the only moment the answer is cheap to act on.
- `status` and `show` say it afterward, on demand.

Record schema goes **2 → 3**, every part a widening: new fields
nullable, v2 records load unchanged, no renames.

### The taxonomy: only what the bytes prove

`artifact_type` is a strict `Literal` with four values —
**`adapter | quant | full-weights | unknown`** — matching how `format`
and `Role` are already typed. It is deliberately narrower than the
five-value list this work was queued with, because only properties of
the *files* survive the no-judgment invariant:

| Value | Proven by |
| --- | --- |
| `adapter` | a root-level `adapter_config.json` |
| `quant` | GGUF precision below full |
| `full-weights` | a complete weight set at full precision |
| `unknown` | no marker qualified |

Two of the queued values do not survive:

- **`merged` is not decidable.** Whether full weights came from full
  fine-tuning or from a flattened LoRA merge is a claim about history,
  not a property of the files. mergekit's only documented output
  artifact is a generated `README.md` model card (verified — see
  `## References`), and reading prose to classify is precisely the
  judgment this ADR exists to prevent. Should a *structured* merge
  marker appear later, `merged` may return as a **marker-proven-only**
  value: recorded when something proves it, never the default for full
  weights whose history is unknown.
- **`base` is not a property of the artifact at all.** "Nothing came
  before this" is answered by the absence of a declared `base_model`,
  which the lineage fields already record. Root-versus-derivative is
  read off lineage, not off the type.

## Consequences

Easier:

- **The quant-only trap becomes visible.** "You can run this forever and
  never re-derive anything from it" is answerable from the record alone,
  offline, years later. That is the gap this ADR exists to close.
- The shopping list stops depending on lineage depth: a full checkpoint
  reports self-sufficient no matter how many ancestors it declares, so
  the DeepHat case resolves to one repo instead of three.
- One classifier feeds the pre-download warning and the post-download
  report, so `pull --plan` and `show` cannot drift apart.
- Spec 0005's advisories stop being write-only.
- The queued smoke test gets its precondition free — never smoke-test an
  artifact whose base dependency is unsatisfied; the failure would say
  nothing about the artifact.
- Smaller schema than the stored-predicate design, with nothing to keep
  in sync on later pulls.

Harder / accepted downsides:

- **The strict enum is a forward-compatibility hazard, and it is the
  sharpest edge here.** Adding a value later (`distill`, some MoE-expert
  shard type, or a marker-proven `merged`) makes older tools declare
  those records unreadable — the documented "strict vocabularies degrade
  visibly" behavior, which cuts deeper in a preservation tool whose
  premise is outliving itself. Collapsing to four values is partly a
  response to this: every value not added is a compatibility break not
  planted. `unknown` absorbs anything outside the set, and unknown
  fields already survive round-trips.
- **The archive will not speak the community's vocabulary.** "Merged
  model" is normal usage and the record will never print it; a
  card-declared merge surfaces as lineage entries instead. `full-weights`
  is likewise a coined term where people say "full checkpoint." Accepted
  as the price of every label having a receipt.
- **"Reconstructable" is necessary, not sufficient.** The record proves
  the *weights* are archived; it cannot prove the toolchain is.
  `convert_hf_to_gguf.py` changes, a future llama.cpp may drop an
  architecture, and an imatrix calibration corpus is not derivable from
  weights at all. ADR 0001's `runtimes/` directory is the intended home
  for the toolchain and nothing populates it yet. The wording must stay
  "the full-precision source is archived" and never "you will succeed."
- **Two verdicts can disagree.** Hub metadata classifies before the
  download, on-disk markers after. The record stores the post-download
  verdict; the planner's is advisory. A repo whose card lies is caught
  at archive time, not plan time.
- **Derived predicates need read-time context.** The cross-model
  question ("is the base archived?") requires archive-wide state, so
  `show` on a single model now needs more than that model's record for
  one line of its output. `status` already walks the tree, so this adds
  no new cost class, but it does add a coupling.
- **A wrong recorded classification is worse than none**: it lands in
  the source of truth and in the generated markdown. The evidence field
  is the audit trail that makes it correctable, and `unknown`-over-guess
  is what keeps it rare.
- **Backfill is partial.** A GGUF-only model archived before v3 has no
  `adapter_config.json` on disk and possibly no card; its lineage cannot
  be recovered offline and stays `null` until a re-pull or a human edit.
  A `reclassify` command is follow-on work for the feature spec.
- Migration stays hand-written (ADR 0001's standing cost), and the
  `MODEL-RECORD.md` renderer grows a lineage/derivability section, so
  the renderer and its tests change too.

## Alternatives considered

- **Store `requires_base` and `reconstructable` as fields** (the
  original draft, and what the TODO entry proposed) — rejected: both are
  functions of state that changes after the write (sibling artifacts,
  the rest of the archive), so a stored copy is a staleness bug with a
  preservation-length fuse. Output is identical; only the JSON differs.
- **The five-value taxonomy this work was queued with**
  (`full-checkpoint | merged | adapter | quant | base`) — rejected: two
  of the five are not decidable from evidence, and the distinction they
  would add changes no decision anyone makes with the archive. Whether
  DeepHat was merged or fine-tuned alters nothing about what you
  download, what runs, or what you can re-derive. The cost of keeping
  them is a coin flip recorded in the one file designed to be believed
  after the tool is gone, plus two more values in a vocabulary whose
  every entry is a future compatibility break.
- **A size-ratio heuristic for derivability** ("safetensors far smaller
  than the claimed base") — rejected for this question once the
  record-local lookup was available; it survives only as a fallback
  marker for adapter detection, where a stated threshold and an
  `unknown` verdict are still required.
- **Model-level fields on `ModelRecord`** — simpler, but cannot express
  one directory holding both an adapter and a merged artifact.
- **Free strings, like `capabilities`** — forward-compatible and never
  makes a record unreadable, but the run/derive logic branches on the
  value, so an unrecognized string would silently mean "no rule
  applies." A closed taxonomy that drives behavior should fail loudly.
  Revisit if the taxonomy churns.
- **Verdict only, no evidence** — smaller, but a threshold-derived
  verdict with no record of the rule is neither auditable nor revisable;
  the `provenance`-on-hashes design already avoided this mistake.
- **Derive everything on read, store nothing** — partly adopted, but it
  cannot go all the way: `artifact_type`'s markers are not all on disk
  (the hub `base_model` lives in a card the archive may not hold), and
  the requirement is answering offline, years later.
- **Recursive or transitive pull of the lineage chain** — already
  rejected at the product level and in the advisory wording. Recording
  lineage must not reopen it; the DeepHat example is precisely why it
  would usually be wasted bytes.
- **A separate lineage file or archive-wide lineage graph** — splits
  provenance from the model directory, breaking ADR 0001's "records
  travel with the model": an rsynced model dir would lose its lineage.
- **Wait for a live need** — the live need is the trigger, and every
  record written at v2 without these fields makes the eventual addition
  a larger backfill problem, since the markers are only available at
  download time.

## References

Internal:

- [`0001-model-storage.md`](0001-model-storage.md) — layout, revision
  pinning, schema-evolution doctrine, records-travel-with-the-model,
  the `runtimes/` directory, original-vs-generated files.
- [`../specs/0000-product.md`](../specs/0000-product.md) — no LLM and no
  tool judgment inside the tool.
- Spec 0005 (advisory rules), 0006 (format-directed grouping), 0014
  (adapter-config fetch gated on a declared size), 0009 (the
  complete/valid vocabulary this extends).
- [`../data-structures.md`](../data-structures.md) — the `roles` vs
  `capabilities` split and the nullable-field doctrine.
- `TODO.md` → "Artifact classification and lineage requirements"
  (Brian, 2026-08-06).

External, fetched in-session 2026-08-06 (summaries, not verbatim
copies):

- llama.cpp quantization pipeline —
  <https://github.com/ggml-org/llama.cpp/blob/master/tools/quantize/README.md>
  (MIT). Confirms `llama-quantize` consumes a high-precision GGUF
  (F32/BF16), that `convert_hf_to_gguf.py --outtype bf16` produces it
  from an HF checkpoint, and that `--imatrix` takes a separate file.
- DeepHat lineage and file tree —
  <https://huggingface.co/DeepHat/DeepHat-V1-7B> and
  <https://huggingface.co/DeepHat/DeepHat-V1-7B/tree/main>. Confirms
  `base_model: Qwen/Qwen2.5-Coder-7B` (itself from `Qwen/Qwen2.5-7B`),
  no `adapter_config.json`, four BF16 safetensors shards totalling
  15.2 GB.
- mergekit output artifacts — <https://github.com/arcee-ai/mergekit>
  (LGPL-3.0; consulted only, nothing copied). The docs describe a
  generated `README.md` model card as the output artifact and document
  no structured config written into the merged model directory. This is
  what rules `merged` out as a marker-decidable type.

Not yet verified — the **feature spec** must pin these under its
`## External references` rather than reconstruct them: the PEFT
`adapter_config.json` schema beyond the `base_model_name_or_path` field
already read by `pull_metadata.py`; hub model-card `base_model`
semantics; GGUF metadata keys used as classification markers; and
whether non-GGUF quantization targets (FP8 via vLLM / TensorRT-LLM
toolchains) consume the same full-precision safetensors input, which was
asserted in discussion but not checked against a source.
