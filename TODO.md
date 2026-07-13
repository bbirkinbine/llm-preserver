# TODO

What's next, in rough order. Feature detail lives in
[`docs/specs/0000-product.md`](docs/specs/0000-product.md) (roadmap)
and the numbered specs; this file is the short-term working list.
Check items off as they ship; update when priorities shift.

## Next spec (0007) — pick one

- [ ] **Verify** (recommended next): audit the archive against
  records/manifests, BagIt-style — *complete* (every recorded file
  exists) and *valid* (every file re-hashes to its recorded SHA256).
  Read-only report. The archive now holds real Tier-1 content, so
  drift/bitrot detection is earning its keep.
- [ ] **Runtime views** (spec 0002, drafted): symlink/config views so
  runtimes run archived models in place. Its blocker (the download
  specs) is lifted — this is what makes the archive *usable* daily.
- [ ] **Managed remove/retire**: the only sanctioned way to delete
  from the archive (record + directory updated together). Real
  pruning needs exist from first live use.
- [ ] **Smoke test**: load an archived model offline in a local
  runtime (llama.cpp / ollama), check a trivial deterministic
  prompt, record the result in the record's `runtime_tested` field
  (a 0000 success metric: the archive is *tested*, not just
  downloaded). Pairs with runtime views — views make models
  loadable in place, smoke test proves they load.
- [ ] **Interactive listing TUI** (promoted from smaller items —
  see its entry below for scope): after 0006's live testing, the
  numbered-pick UX is workable but the scroll pain is real.

## Shipped

- 0001 archive init + records, 0003 selective pull, 0004 full
  snapshot (`pull --whole-repo`, shipped as `--all` and renamed by
  0005). The core loop works end to end and is live-verified: init →
  pull quants and masters → status/show.
- 0005 companion advisories + `pull --plan` (merged 2026-07-13,
  rebase-merge): archive-aware advisory rules (companions, shard
  sets, adapter base, full-precision master, `--model` grouping
  mismatch as a highlighted warning), the `--plan` dry run,
  `--all` → `--whole-repo`, size confirmation + disk preflight on
  every pull mode. Live-verified against real Qwen3.6 repos,
  including the copy-pasted `--model` footgun it now catches.
- 0006 guided discovery (merged 2026-07-13, PR #7, rebase-merge):
  the `discover` command — hub search passed through verbatim →
  model-tree navigation (ancestry ladder, breadcrumb, stable `0`
  pull key, archive-mode choice) → the unmodified pull flow, with
  declared base models rename-resolved (one disclosed light call)
  so records carry current ids. Hub seam extended (search/children/
  summary) and `hub.py` split into a package. Fifteen live-use
  adjudications from manual testing shaped the UX; the full record
  is in the spec.

## Smaller items (from live use)

- [ ] Goal-definitive archiving (live-use 2026-07-13: "my goal was
  fine-tuning and I couldn't tell if I'd archived enough"). Two
  halves, both deterministic from existing data: (a) post-pull
  master *offer* in discover — when a quant pull completes and the
  full-precision master isn't archived, ask "also archive
  <master> (<size>) — needed for future fine-tuning? [y/N]" (human
  pick, never auto-add; turns the advisory into a decision point);
  (b) capability report in `status` derived from each record —
  runnable / re-quantizable (bf16+imatrix present) / fine-tunable
  (safetensors master present), with the exact missing pull named.
  The `docs/cli.md` "Archiving for a goal" table is the interim
  reference.
- [ ] Interactive listing TUI (future spec candidate; live-use
  2026-07-13): discovery's accumulate-paging re-renders the whole
  fetched list on every `m` (80+ lines after two pages), and pull's
  file listing has the same long-scroll problem. A terminal UI —
  scrollable viewport sized to the terminal, arrow-key
  highlight-and-enter selection, optional type-to-filter — replaces
  numbered picks as presentation only; the deterministic
  facts/no-ranking invariants and the pipe-testable pick model both
  need a story (TUI harness for tests, plain fallback for dumb
  terminals). New dependency (`textual` or `prompt_toolkit`) goes
  through the dependency-hygiene skill first.
- [ ] File-kind dictionary in the listing (grew from the quant-label
  UX item; 0000 roadmap "Later"): annotate recognized quant labels
  (deterministic provenance-pinned table: bits/weight, quality tier,
  "common default" marker), tag bf16/f16 GGUFs as full-precision
  re-quantization sources, and/or `--quant` sugar. Companion-kind
  annotations (imatrix/mmproj/mtp, from the advisory rules table)
  shipped in the listing 2026-07-13 — this item is the rest of the
  dictionary. Live-use additions (2026-07-12): the listing prints
  raw byte counts where the plan report prints human sizes — use
  `human_size` there too; empty pattern input at the prompt errors
  (exit 2) instead of re-prompting.
- [ ] Example-run cookbook (`docs/examples.md`): one worked pull per
  repo archetype — GGUF quant repo, original safetensors
  (`--whole-repo`),
  multimodal (weights + `mmproj`), sharded weights, adapter/LoRA,
  embedding/reranker, gated repo (`hf auth login`). Each example
  shows the non-interactive form (`--include` + `--model` + `--yes`)
  so scripted/cron runs have a copy-paste recipe per model type.
  The `--plan` flag belongs in every recipe as the verify step.
- [ ] `quantization` record field is never populated (artifact-level
  label extraction was never specced; per-file is likely the right
  shape now that one artifact can hold several quants).
- [ ] Split `records.py` (296) — near the 300-line cap; split before
  the next feature touches it (`pull.py` was split by 0005).
