# 0003 — One Directory Per Source Repo

**Status:** accepted
**Last updated:** 2026-08-11 (accepted by Brian, after the spec 0017
clarification round settled the migration gate and the lineage view)

> **Scope note.** This ADR amends exactly one rule of
> [ADR 0001](0001-model-storage.md) — **model identity**, the rule that
> a model directory is named for the *original* model and that
> third-party artifacts file underneath it. Everything else ADR 0001
> decided stands unchanged: plain filesystem tree, no database,
> per-model JSON record as source of truth, generated markdown,
> `sha256sum`-compatible manifests, immutable payload, views as
> disposable adapters.

## Context

ADR 0001 organized the archive **model-first**: one directory per
logical model at `models/<creator>/<model>`, where `<creator>/<model>`
is the *original* model's hub id, with artifacts from third-party repos
(a `*-GGUF` quant published by someone else) filing under it and
carrying their own `source_repo` per artifact. The stated reason was
retrieval: the future question is "I need Qwen2.5-Coder," and one
directory should hold every archived form of it.

ADR 0001 also named the cost it was accepting, in its own consequences
section: *"Canonical identity is a judgment call at download time.
Pulling `bartowski/...-GGUF` requires deciding which original model it
belongs under. The download specs must ask or infer and confirm; a
wrong grouping is a rename, not a data loss."*

Thirteen months of real use say the judgment call is the problem, and
that it is not the tool's to make well. The archive was measured on
2026-08-11 — 25 models on the live shelf — and three facts
came out of it.

**1. Two quant repos of one model sit in two different layouts, and
the archive cannot say why.**

```text
unsloth/Muse-Glimmer-30B-GGUF/          own directory
meta-models/Muse-Glimmer-30B/
  ├── hf-snapshot/   from meta-models/Muse-Glimmer-30B
  └── gguf/         from meta-models/Muse-Glimmer-30B-GGUF
```

Both source repos declare the *same* base model on their cards —
verified against the archived copies at their pinned revisions,
`meta-models/...-GGUF` as a YAML scalar and `unsloth/...-GGUF` as a
single-element list, shapes that `_first_str`
(`src/llm_preserver/hub/client.py:38`) normalizes identically. The
tool's default would therefore have grouped both under
`meta-models/Muse-Glimmer-30B`, and one of them is not there. The
likely cause is a curator override at pull time (`--model`, or
declining the prompt and re-running); the record keeps no trace of the
grouping decision, so the archive itself cannot distinguish an override
from a metadata read that differed that day.

That is the finding, and it is not "the inference is wrong." The
inference was probably right on both repos. The problem is that **the
layout is set by pull-time circumstances the archive does not record**,
so two quants of one model scatter and nothing on disk explains it —
and a curator who wants the flat shape, as this directory shows was
wanted, must override the default on every pull, forever.

**2. Three directories are named for models the archive does not
hold.** `Qwen/Qwen3-Coder-Next`, `Qwen/Qwen3.5-122B-A10B`, and
`zai-org/GLM-4.7-Flash` contain zero files from the repos they are
named after — each holds only an unsloth GGUF conversion. `status`
lists all three as archived models. A curator reading that shelf in
2031, looking for Qwen3-Coder-Next's safetensors, finds a directory
with its name and none of its weights. For a tool whose purpose is
being right about what was preserved, a directory name that overstates
its contents is the worst available failure: silent, and legible only
by opening the record.

**3. Nearly half the archive is affected.** 11 of 25 model directories
hold at least one foreign repo's files — 3 hold *only* foreign files
(138.0 GiB), 8 mix their own with a foreign quant (544.5 GiB of foreign
payload).

Against all of that sit three real counterweights, recorded here so
this decision is not read as one-sided:

- **ADR 0001's retrieval argument is correct and is being given up.**
  Under nesting, "everything I have of Qwen3.6-27B" is one `ls`. After
  this change it is a query over records. If the query is not built,
  the archive gets *less* legible, not more.
- **The parent directory was itself a lineage statement.** ADR 0001's
  durability test is "a human with `ls` and a text editor, years later,
  with the tool dead." Under nesting, the path stated the relationship
  between a quant and its source model without any tool. Flattening
  removes that statement from the filesystem, and something must put it
  back in the record's rendered markdown.
- **Migration is a data operation on irreplaceable bytes**, on the
  don't-touch side of `CLAUDE.md`. Roughly 683 GiB moves. Doing nothing
  has no such cost.

One further piece of evidence is available only second-hand and is
flagged as such. Session notes from 2026-08-11 record that a previous
attempt to solve this the other way — making one model directory hold
several publishers cleanly — was implemented and then rejected on
measurement: roughly 500 lines of `src/` whose only job was making one
directory behave like two, three review rounds, ~15 reproduced defects
*all* rooted in two source repos sharing a directory, two of which were
never closed (a pre-v2 record with `source_repo: null` defeated
multi-publisher support entirely; concurrent pulls into one model
de-recorded a publisher). That branch was never pushed and the working
tree is gone, so this ADR cannot cite code or a diff for it. It is
included as testimony, not as proof, and the decision below does not
rest on it.

## Decision

We will store **one directory per source repo**. A model directory's
path mirrors the Hugging Face repo id verbatim:

```text
models/<owner>/<repo>/          # exactly the hub repo id, two components
  model-record.json
  MODEL-RECORD.md
  manifest-sha256.txt
  gguf/                         # format subdirectories unchanged
  hf-snapshot/
```

The naming changes with it: ADR 0001 deliberately called the first
component `creator` — *"the creator of a third-party quant's underlying
model is who the directory is named for"*. Under this ADR it is the
repo **owner**, and it means what it says. `models/unsloth/` holds
unsloth's repos.

Four rules follow:

- **The path is derived, never decided.** A pull's destination is a
  pure function of the repo id the human typed. No inference, no
  grouping prompt, no `--model` override, no hub metadata call to
  resolve a home.
- **`hub_id` names the source repo**, and the record-level invariant
  becomes checkable: for every artifact, `source_repo` is
  `https://huggingface.co/<hub_id>`, and `<hub_id>` matches the
  directory path. Today no invariant relates the directory name to its
  contents; after this, one does, and machinery can assert it.
- **Lineage moves into the record.** The declared `base_model` and any
  parent models are recorded fields. What the layout used to assert
  structurally, `status`, `show`, and the generated `MODEL-RECORD.md`
  assert in data and prose. This is not optional follow-up work — it is
  what keeps the change from being a net loss of information.
- **Format subdirectories stay.** A repo can ship both safetensors and
  GGUF, and keeping `gguf/` / `hf-snapshot/` means every path *inside*
  an artifact is unchanged by migration, so every recorded file path
  and hash survives the move untouched. (Migration moves the files an
  artifact lists, not whole subdirectories — one `gguf/` directory can
  legally hold two publishers' files from selective pulls.)

Both version numbers move: archive `schema_version` 1 → 2 (an old tool
must refuse a migrated archive) and record `record_schema_version`
2 → 3 (`hub_id` changes meaning, and lineage fields arrive).

## Consequences

Easier:

- **The directory name becomes verifiable.** `hub_id` vs. path vs.
  `source_repo` is a three-way equality a test and `verify` can assert.
  The class of defect where a directory misdescribes its contents
  stops being possible rather than being reviewed for.
- **Pulls become reproducible.** The same repo id lands in the same
  place today, next year, and on a rebuilt machine — independent of
  what the upstream card said on the day.
- **Code is deleted, not added.** The grouping proposal and its
  confirmation, `--model`, `require_single_snapshot_source`, and spec
  0006's rename-resolve hub call all exist to serve canonical identity.
  So does the second sanctioned exception to the one-metadata-call
  rule. All of it goes.
- **Two publishers of the same model coexist** with no collision rule,
  no shared record, and no concurrent-write hazard between them —
  each is a separate directory with a separate record.
- **A partial copy stays honest.** Rsync one directory and you have
  exactly one repo's bytes plus a record that names that repo.

Harder / accepted downsides:

- **Retrieval by model becomes a query.** `ls models/` no longer groups
  the forms of one model. `status` and `show` must group by recorded
  lineage, and until they do, the archive is harder to read. Load-bearing:
  it ships with the layout change, not after it.
- **Legibility without the tool depends on rendered prose.** The
  filesystem no longer states that a quant derives from a model, so
  `MODEL-RECORD.md` has to, in words, for the `ls`-and-`cat` reader.
- **Migration is required**, is a bulk data operation, and has no
  no-op fallback: leaving old directories in place would mean two
  layouts in one archive and two code paths in every reader, forever.
- **More directories.** 25 becomes ~36 here, many of them owner
  directories holding a single repo. Accepted: at curated-shelf scale
  the cost is cosmetic.
- **Upstream repo renames drift the directory name.** Nesting at least
  rename-resolved the canonical model. Accepted: the recorded commit
  revision is the durable identity, the directory is convenience, and a
  rename is a rename either way.
- **The lineage a human already confirmed is only in the old paths.**
  Every existing nested quant encodes a curator's confirmed judgment
  that repo X converts model Y. That information exists nowhere else,
  so migration must harvest it into the record rather than discard it.
- **Views and any external store seeded from archive paths break.**
  Views are disposable by design (ADR 0001); they get regenerated.

## Alternatives considered

- **Keep ADR 0001 and improve the grouping inference.** Rejected
  because inference quality is not the failing part: on the Muse pair
  the default was probably correct for both repos, and the archive
  still holds them in two shapes because a human overrode it once and
  nothing recorded that. Sharper prompts ask the same question on every
  pull without making the result predictable in advance or explicable
  afterwards. A related half-measure — *record* the grouping decision so
  the archive can explain itself — is strictly better than today and
  still leaves fact 2 (directories named for absent models) untouched.
- **Per-publisher subdirectories inside the canonical model**
  (`models/<creator>/<model>/gguf/<publisher>/`). This preserves
  ADR 0001's retrieval property and fixes the collision case, and it is
  what the earlier attempt built. Rejected: it keeps one record
  spanning several publishers, which is the shared-mutable-state that
  the session notes above attribute every reproduced defect to, and it
  keeps the canonical-identity judgment call that fact 1 shows the tool
  cannot make reliably. It also does not fix fact 2 — a directory named
  for a model whose weights are absent stays exactly as misleading.
- **Single-segment flat names** (`models/unsloth__Muse-Glimmer-30B-GGUF/`).
  Rejected: mangles an upstream name, against ADR 0001's
  preserve-names-verbatim rule, and loses `ls models/unsloth/` for no
  gain over the two-component form.
- **Repo directories plus a canonical symlink tree** (real bytes under
  the repo id, a parallel `by-model/` tree of symlinks for retrieval).
  Tempting because it buys back the `ls` property. Rejected: symlinks
  do not survive rsync, network shares, and backup tooling uniformly,
  which is the
  portability the whole design is built on; and it adds a second
  structure `verify` must reason about, in a tool whose value is that
  its integrity story is simple.
- **Apply the new layout only to new pulls, migrate nothing.** Rejected:
  every reader carries both code paths permanently, `status` shows two
  organizing principles at once, and the archive this exists to serve is
  the one already holding 11 mixed directories.
- **Do nothing.** The honest baseline. Rejected because facts 1 and 2
  are not cosmetic: the tool's product claim is that the archive is
  right about what it holds, and three directories currently are not.

## References

None external. This decision rests on measurement of the live archive
performed 2026-08-11 (25 models) plus the repository's own
code and prior ADRs, all cited inline above. No outside authority,
registry, or specification is claimed. The second-hand session-note
evidence is marked as testimony where it appears and is not
load-bearing.
