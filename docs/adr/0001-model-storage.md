# 0001 — Model Storage

**Status:** proposed
**Last updated:** 2026-07-09

## Context

Every planned feature — init/manifest (spec 0001), runtime views
(spec 0002), and the pending GGUF-pull / HF-snapshot / verify /
smoke-test / cache-import specs — reads or
writes the same on-disk archive, so its layout and record format are
inherited by all of them and become costly to reverse the moment a real
archive exists — migrating a schema across hundreds of GB on a NAS is
not a refactor. The decision has to hold up against the product's core
constraints (`docs/specs/0000-product.md`):

- **Preservation horizon.** The archive must still be usable years
  later, possibly *without this tool* — if `llm-preserver` bitrots, a
  human with `ls` and a text editor should still understand what each
  model is, where it came from, its license, and how to check its
  integrity.
- **Storage is user-provided** — a NAS path or big disk. The format
  must be friendly to `rsync`, partial copies, and tiered backup
  (Tier 1 fully backed up, Tier 3 re-downloadable), and must not
  assume the archive fits anywhere in particular.
- **Curated scale.** Roughly 6–10 models per the plan, maybe dozens
  over time — never thousands. Query performance is a non-force;
  legibility and durability are the forces.
- A usable preserved model is a *bundle* (weights + tokenizer/config +
  chat template + license/model card + checksums), so per-model
  metadata has to travel with the model files.

What argues against deciding now: no code exists yet, and the first
downloads may surface layout needs we can't see. The
counterweight is that spec 0001 builds `init`/`status` directly on this
decision, so leaving it implicit just moves the decision into code.

## Decision

We will store the archive as a **plain filesystem tree with per-model
JSON records — no database**, organized **model-first**: one directory
per logical model, with formats nested inside it.

- **Layout:**

  ```text
  <archive-root>/
    archive.json                      # root marker, schema_version
    models/<creator>/<model>/         # one dir per logical model
      model-record.json               # source of truth for ALL formats below
      MODEL-RECORD.md                 # generated rendering
      gguf/                           # only the formats actually archived
      hf-snapshot/
      mlx/
    runtimes/                         # preserved installers/builds
    manifests/                        # derived archive-wide aggregates
  ```

  Retrieval is by model, not by format — the future happy path is "I
  need Qwen2.5-Coder", and that one directory holds every archived
  form of it. A new weight format is just a new subdirectory and a
  record entry, not a new top-level tree or a schema migration.
- **Model identity:** `<creator>/<model>` is the *original* model's
  hub id — `<creator>` is the org or person that created the model
  (the hub namespace: `Qwen`, `mistralai`, `meta-llama`), `<model>`
  one specific model repo (e.g. `Qwen/Qwen2.5-Coder-32B-Instruct`;
  each size/variant is its own hub repo and so its own model dir,
  siblings under the same creator). Creator-as-subfolder is the
  namespace that keeps same-named models from different orgs from
  colliding, and — since hub orgs and model families coincide in
  practice — makes "what do I have from Qwen?" a single
  `ls models/Qwen/`. Artifacts often come from a different hub repo —
  GGUF quants are typically published by third parties (a `*-GGUF`
  repo under another namespace) — and still file under the model's
  *creator*; each artifact entry in the record carries its own actual
  source repo URL; the directory answers "what model is this," the
  record answers "where did each file come from." The name is
  deliberately `creator`, not `owner` or `publisher`: the creator of
  a third-party quant's *underlying model* is who the directory is
  named for, while the quant's publisher owns only the source repo
  recorded per artifact.
  <!-- assumption: group by original-model identity and record per-artifact sources, rather than one dir per hub repo -->
- **Role is metadata, not layout:** embeddings and rerankers are just
  models with `role: embedding` / `role: reranker` in their record
  (the vault plan's `embeddings/` and `rerankers/` top-level dirs
  collapse into this). `status` can group by role when printing.
  <!-- assumption: role as a record field; a role-based directory split would recreate the same retrieval problem format-first has -->
- **Per-model record:** a `model-record.json` in each model directory
  is the **source of truth**, defined as a Pydantic v2 model with an
  explicit schema; a human-readable `MODEL-RECORD.md` is **generated**
  from it and committed alongside so the archive stays legible without
  the tool. The tool never parses the markdown.
  <!-- assumption: JSON as source of truth with generated markdown; the vault plan's template is markdown-first, but hand-edited markdown can't be robustly round-tripped -->
- **Records travel with the model:** checksums and provenance live in
  the model directory itself, so a partially copied archive (one model
  dir rsynced to another disk) carries its own record. `manifests/`
  holds only derived, archive-wide aggregates (e.g. a combined
  SHA256 inventory), regenerable from the per-model records.
  <!-- assumption: manifests/ is a regenerable cache, never authoritative -->
- **Versioned root marker:** `archive.json` at the archive root
  identifies a directory as an llm-preserver archive and carries a
  `schema_version` integer. Tools refuse to operate on a newer schema
  than they know and migrate older schemas forward explicitly.

The following conventions are adopted from prior art (hub tooling and
digital-preservation practice — see `## References`); they refine the
decision rather than change its shape:

- **Pin immutable revisions, not branch names.** Every artifact entry
  records the resolved hub identity — for Hugging Face, the repo id
  *and the full commit hash* the files came from (`refs/main` is a
  moving pointer, not provenance); plus source URL and retrieval date.
  Every mature system keys on an immutable id (HF cache keys snapshots
  by commit; Ollama keys blobs by SHA256 digest).
- **Tool-independent verification:** each model directory carries a
  `manifest-sha256.txt` in `sha256sum -c`-compatible format covering
  the payload *and* `model-record.json` — BagIt's core lesson: a bag
  must be verifiable decades later with nothing but coreutils, no JSON
  parser, no llm-preserver. The JSON record keeps richer per-file data
  (size, original-vs-generated flag); the txt manifest is the
  lowest-common-denominator fixity check, and checksumming the record
  itself makes metadata corruption detectable (BagIt's tagmanifest
  pattern).
  <!-- assumption: one manifest covering payload + record, rather than BagIt's separate manifest/tagmanifest pair — full bag ceremony is overkill for a personal tool -->
- **Original vs. generated, tagged per file** (Internet Archive's
  `source` flag): weights, tokenizer/config, license, model card are
  *original* (sacred, never regenerable); `MODEL-RECORD.md`,
  `manifest-sha256.txt`, and anything under `manifests/` are
  *generated* (regenerable from originals + record). Recovery and
  backup logic treat the two classes differently.
- **Preserve upstream filenames verbatim.** GGUF names encode
  load-bearing metadata (quant type, `-NNNNN-of-NNNNN` shard position)
  that runtimes parse; HF snapshot trees keep their repo-relative
  paths. Canonical *identity* is the SHA256 (hash-first, No-Intro
  style); filenames are presentation and must survive unmangled.
- **Verify distinguishes "complete" from "valid"** (BagIt vocabulary,
  for the verify spec): complete = every file listed in the record/manifest
  is present; valid = complete *and* every hash matches. The record
  enumerates *expected* files, so a partially rsynced or
  crash-interrupted model dir is detectable offline.
- **Downloads use HF `local_dir` semantics, never the HF cache.** The
  hub's blob/symlink cache is explicitly a cache (HF marks it
  backup-excludable via `CACHEDIR.TAG`); `local_dir` produces the
  plain portable tree, and its leftover `.cache/huggingface/` sync
  metadata is deleted after download.

**Runtime consumption: views, not copies.** Models will grow past
what is practical to copy between bulk storage and local disks, so
the archive is designed to be the *single* storage copy that runtimes
consume in place. Two rules:

- **Immutable payload, mutable metadata.** Payload files (weights,
  tokenizer/config, license, model card) are write-once: locked
  (`chmod a-w`) after download + hashing. Records, rendered markdown,
  and `manifests/` stay writable — verify timestamps and smoke-test
  results (the verify and smoke-test specs) mutate them. A fully
  read-only filesystem
  is therefore supported for *consumers* (e.g. the NAS exports the
  share read-only to inference hosts, writable only to the archiving
  host) but not required by the design.
  <!-- assumption: file-mode locking + optional RO export, rather than a hard-RO filesystem — records must stay writable where the archiver runs -->
- **Views are generated, disposable adapters** — symlinks or config
  pointing into the archive, never weight copies, and never anything
  the archive's integrity depends on (deleting every view loses
  nothing). This ADR owns only the constraint that the layout must
  support in-place, read-only consumption; *which* runtime needs
  which adapter is tool-specific mechanics owned by the runtime-views
  feature spec (`docs/specs/0002-runtime-views.md`), where the
  per-tool feasibility research (verified 2026-07-09) now lives. The
  one-line summary that informed this decision: llama.cpp/vLLM take
  direct read-only paths, LM Studio works via a symlink view tree,
  Ollama currently cannot run in place — which is a tool constraint
  the layout should not contort around.

## Consequences

Easier:

- The archive is inspectable and recoverable with standard tools
  (`ls`, `cat`, `sha256sum`, `rsync`) — the preservation goal survives
  the death of the tool itself.
- Partial backup/restore and tiering fall out of the layout: a model
  directory is self-contained — *all* formats plus record — so backup
  tiers (which the product spec defines per model, not per format) map
  to directory lists, and copying a model dir copies its provenance.
- Adding a future weight format (or archiving a new format for an
  already-archived model) touches only that model's directory and
  record — no top-level layout change, no `schema_version` bump.
- Testing is trivial (`tmp_path` archives), and `git`-style diffing of
  records is possible since they are small text files.

Harder / accepted downsides:

- **No transactional writes.** A crash mid-download can leave a model
  dir without a record or with a stale one; the verify spec is the
  mechanical answer, and write-record-last is the convention.
- **Records can drift from files** (hand-moved files, bit rot). Same
  answer: verify re-hashes against records; drift is a detectable
  state, not a prevented one.
- **`status` walks the tree** every run instead of querying an index.
  Fine at curated-shelf scale; if the archive ever grows to thousands
  of models this ADR should be superseded, not patched.
- **Not a drop-in tree for runtimes.** LM Studio expects GGUFs at
  exactly `<publisher>/<model>/<file>.gguf`; our extra `gguf/` level
  breaks direct reuse of the archive as an LM Studio models dir. The
  archive is the source of record — runtimes consume it through
  generated views (see Decision), accepted rather than contorting the
  layout to match any one tool.
- **Cold starts over the network are slower.** Running in place from
  a NAS trades copy time for page-in time on every cold load; whether
  that is acceptable is per-host (link speed, model size, restart
  frequency). The design permits a local hot-cache copy but never
  requires one.
- **No dedup.** Content-addressed blob stores (HF cache, Ollama)
  deduplicate shared files across revisions; a plain tree stores
  duplicates. At curated-shelf scale, legibility beats the saved
  bytes.
- **Schema migrations are hand-written** per `schema_version` bump.
  The mitigation is conservatism: nullable-by-default fields, add
  rather than rename.
- **Canonical identity is a judgment call at download time.** Pulling
  `bartowski/...-GGUF` requires deciding which original model it
  belongs under. The download specs must ask or infer and
  confirm; a wrong grouping is a rename, not a data loss.
- **One record spans formats**, so concurrent downloads of two formats
  of the same model could race on `model-record.json`. Non-force at
  this scale (single-user CLI), but the write path should be
  read-modify-write with the record schema, not blind overwrite.
- Duplicate representation (JSON + generated markdown) can desync if
  something edits the markdown by hand; the "generated — edit the
  JSON" header in the markdown is the guard.

## Alternatives considered

- **Format-first layout** (the vault plan's sketch: top-level `gguf/`,
  `hf-snapshots/`, `mlx/`, `embeddings/`, `rerankers/`, each nesting
  `<creator>/<model>/`) — rejected because retrieval runs the other way:
  the future question is "give me Qwen," and one model routinely has
  multiple formats (GGUF for llama.cpp/Ollama, MLX for the Mac, an HF
  snapshot for conversions), which format-first scatters across trees
  with split provenance and no single per-model record. It also makes
  every new format a top-level layout addition, and it fights per-model
  backup tiering. Format-first would win if retrieval were per-runtime
  bulk operations ("sync all GGUFs to the 3090 host"), but that's a
  query `status` can answer from records.
- **SQLite catalog** — a single-file DB indexes/queries well, but it
  makes the archive illegible without the tool, concentrates
  corruption risk in one file, and complicates partial copies and NAS
  sync. Query needs don't exist at this scale.
- **`MODEL-RECORD.md` as the source of truth** (the vault plan's
  literal template) — most human-friendly, but hand-edited markdown
  can't be robustly parsed back; keeping it as a generated rendering
  preserves the readability without making prose load-bearing.
- **YAML or TOML records** — more pleasant to read than JSON, but the
  generated markdown already covers human reading; JSON is stdlib,
  unambiguous, and Pydantic-native.
- **Single central manifest** (one `manifests/models.json` for the
  whole archive) — one file to corrupt and one merge conflict point,
  and a copied model dir would lose its provenance. Rejected in favor
  of records-with-the-model plus regenerable aggregates.
- **Adopting a runtime's cache layout** (Ollama blobs / LM Studio
  dirs) — runtime-locked and lossy for archival (already rejected at
  the product level; caches are *imports*, a later spec). Hugging Face
  itself treats its cache as disposable (`CACHEDIR.TAG` marks it
  backup-excludable) and offers no archival format — `local_dir` plus
  your own checksums is the sanctioned path, which is what this ADR
  formalizes.
- **Strict BagIt conformance** (RFC 8493 bags per model) — the
  manifest format and complete/valid vocabulary are adopted, but the
  full ceremony (`data/` payload dir, `bagit.txt`, separate
  tagmanifests, `fetch.txt`) adds structure a personal tool doesn't
  need and would bury the model files one level deeper than every
  runtime expects. Borrow the lessons, skip the bag. Likewise full
  OAIS/PREMIS metadata: institutional-grade, out of scope.

## References

Conventions above were verified against fetched sources, 2026-07-09
(research session; summaries in the ADR, not verbatim copies):

- HF cache & download semantics:
  <https://huggingface.co/docs/huggingface_hub/guides/manage-cache>,
  <https://huggingface.co/docs/huggingface_hub/guides/download>
- HF gated repos / tokens:
  <https://huggingface.co/docs/hub/models-gated>,
  <https://huggingface.co/docs/hub/security-tokens>
- GGUF naming/shards/metadata:
  <https://github.com/ggml-org/ggml/blob/master/docs/gguf.md>,
  <https://huggingface.co/docs/hub/gguf>
- Ollama on-disk format (community-documented; no official spec
  found): <https://github.com/rvs/af-wiki/blob/main/ollama-storage-format.md>
- LM Studio layout:
  <https://lmstudio.ai/docs/app/advanced/import-model>
- BagIt: RFC 8493, <https://www.rfc-editor.org/rfc/rfc8493.html>
- Internet Archive item structure:
  <https://blog.archive.org/2011/03/31/how-archive-org-items-are-structured/>
- Fixity practice:
  <https://www.dpconline.org/handbook/technical-solutions-and-tools/fixity-and-checksums>
- No-Intro naming convention:
  <https://wiki.no-intro.org/index.php?title=Naming_Convention>
  (DAT hash-field details were only confirmed via secondary sources;
  treated as background, not load-bearing)

Runtime-views per-tool sources and unverified items live with the
runtime-views feature spec (`docs/specs/0002-runtime-views.md`
→ `## External references`).
